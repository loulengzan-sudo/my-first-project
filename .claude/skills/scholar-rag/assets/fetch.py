#!/usr/bin/env python3
"""
fetch.py — locate open-access PDFs for library items whose file isn't on disk.

For each document with status 'no_pdf' (a Zotero item that has an attachment
record but no local file) and a DOI, try, in order:
  1. Unpaywall   best_oa_location.url_for_pdf  (needs a contact email)
  2. OpenAlex    best_oa_location / primary_location pdf_url
  3. arXiv       title match -> pdf link
Downloads to $SCHOLAR_RAG_DIR/raw/pdfs/<doc_id>.pdf (verified %PDF magic),
sets documents.pdf_path + source='oa-fetch' + status='new' so extract/embed
pick it up. Resumable + polite (rate-limited). OA-only: never bypasses paywalls.

Usage:
  python3 fetch.py run [--limit N] [--email you@inst.edu] [--sleep 0.5]
  python3 fetch.py one <doi> [--email ...]
"""
import os, sys, json, time, argparse, urllib.parse
import store

EMAIL = (os.environ.get("SCHOLAR_UNPAYWALL_EMAIL")
         or os.environ.get("SCHOLAR_CROSSREF_EMAIL")
         or "research@example.com")
UA = {"User-Agent": "scholar-rag/0.1 (mailto:%s)" % EMAIL}
PDF_DIR = None


def _pdf_dir():
    global PDF_DIR
    if PDF_DIR is None:
        PDF_DIR = os.path.join(store.rag_dir(), "raw", "pdfs")
        os.makedirs(PDF_DIR, exist_ok=True)
    return PDF_DIR


def _get_json(url, timeout=25):
    import requests
    r = requests.get(url, headers=UA, timeout=timeout)
    return r.json() if r.ok else None


def unpaywall_pdf(doi):
    d = _get_json("https://api.unpaywall.org/v2/%s?email=%s"
                  % (urllib.parse.quote(doi), urllib.parse.quote(EMAIL)))
    if not d:
        return None
    loc = d.get("best_oa_location") or {}
    return loc.get("url_for_pdf") or loc.get("url")


def openalex_pdf(doi):
    d = _get_json("https://api.openalex.org/works/doi:%s?mailto=%s"
                  % (urllib.parse.quote(doi), urllib.parse.quote(EMAIL)))
    if not d:
        return None
    for loc in [d.get("best_oa_location"), d.get("primary_location")]:
        if loc and loc.get("pdf_url"):
            return loc["pdf_url"]
    return None


def arxiv_pdf(title):
    import requests, re
    if not title:
        return None
    q = urllib.parse.quote('ti:"%s"' % title[:120])
    try:
        r = requests.get("http://export.arxiv.org/api/query?search_query=%s"
                         "&max_results=1" % q, headers=UA, timeout=25)
        if not r.ok:
            return None
        m = re.search(r'<id>(http[^<]*arxiv\.org/abs/[^<]+)</id>', r.text)
        if m:
            return m.group(1).replace("/abs/", "/pdf/") + ".pdf"
    except Exception:
        pass
    return None


def download_pdf(url, dest, timeout=60):
    import requests
    try:
        r = requests.get(url, headers=UA, timeout=timeout, stream=True,
                         allow_redirects=True)
        if not r.ok:
            return False
        first = next(r.iter_content(chunk_size=1024), b"")
        if not first.startswith(b"%PDF"):
            return False                          # not actually a PDF (HTML wall)
        with open(dest, "wb") as f:
            f.write(first)
            for chunk in r.iter_content(chunk_size=1 << 16):
                f.write(chunk)
        return os.path.getsize(dest) > 4096
    except Exception:
        return False


def resolve_and_fetch(doc, email=None):
    doi, title = doc.get("doi"), doc.get("title")
    for name, url in (("unpaywall", unpaywall_pdf(doi) if doi else None),
                      ("openalex", openalex_pdf(doi) if doi else None),
                      ("arxiv", arxiv_pdf(title))):
        if not url:
            continue
        dest = os.path.join(_pdf_dir(), doc["doc_id"] + ".pdf")
        if download_pdf(url, dest):
            return dest, name
    return None, None


def run(limit=None, sleep=0.5):
    con = store.connect()
    todo = [d for d in con.execute(
        "SELECT * FROM documents WHERE status='no_pdf' AND doi!='' ORDER BY doc_id")]
    sys.stderr.write("[fetch] %d no-PDF items with a DOI (email=%s)\n"
                     % (len(todo), EMAIL))
    got = miss = 0
    for i, row in enumerate(todo):
        if limit and i >= limit:
            break
        d = dict(row)
        dest, src = resolve_and_fetch(d)
        if dest:
            con.execute("""UPDATE documents SET pdf_path=?, source='oa-fetch',
                    status='new', updated_at=? WHERE doc_id=?""",
                        (dest, store._now(), d["doc_id"]))
            con.commit(); got += 1
        else:
            miss += 1
        if (i + 1) % 25 == 0:
            sys.stderr.write("  ...%d/%d  got=%d\n" % (i + 1, len(todo), got))
        time.sleep(sleep)
    return {"fetched": got, "not_found": miss, "attempted": got + miss}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "one"])
    ap.add_argument("doi", nargs="?")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--email", default=None)
    ap.add_argument("--sleep", type=float, default=0.5)
    args = ap.parse_args()
    global EMAIL, UA
    if args.email:
        EMAIL = args.email; UA = {"User-Agent": "scholar-rag/0.1 (mailto:%s)" % EMAIL}
    if args.command == "one":
        print(json.dumps({"unpaywall": unpaywall_pdf(args.doi),
                          "openalex": openalex_pdf(args.doi)}, indent=2))
        return
    print(json.dumps(run(limit=args.limit, sleep=args.sleep), indent=2))


if __name__ == "__main__":
    main()
