#!/usr/bin/env python3
"""
ingest.py — populate the corpus manifest from a source (Zotero or a folder).

This is stage 1 of the pipeline (source -> corpus.sqlite manifest). It does NOT
extract or embed; run extract.py then chunk_embed.py next (or use run-ingest.sh
to chain all three). Stdlib-only, so it runs before the venv exists.

Usage:
  python3 ingest.py zotero [--limit N] [--types t1,t2] [--with-pdf-only]
  python3 ingest.py folder <dir> [--limit N]
  python3 ingest.py status
"""
import os, re, sys, json, glob, argparse, hashlib
import store
import zotero_reader as zr


def parse_zotero_filename(stem):
    """Parse Zotero-style attachment names into (title, year, authors).

    Handles the common patterns:
      "Author(s) - YEAR - Title"   e.g. "Fiel and Zhang - 2019 - With All..."
      "Author(s) YEAR"             e.g. "Irvine and Gal 2000"
    Author strings like "X et al." are kept verbatim (single element) so the
    citation formatter renders "X et al. (YEAR)"; "A and B" splits to [A, B].
    Falls back to (stem, None, []) when nothing matches.
    """
    title, year, auth_raw = stem, None, None
    m = re.match(r"^(.+?)\s+-\s+((?:18|19|20)\d{2})[a-z]?\s+-\s+(.+)$", stem)
    if m:
        auth_raw, year, title = m.group(1), int(m.group(2)), m.group(3).strip()
    else:
        m2 = re.match(r"^(.+?)\s+((?:18|19|20)\d{2})[a-z]?$", stem)
        if m2:
            auth_raw, year, title = m2.group(1), int(m2.group(2)), stem.strip()
    if not auth_raw:
        return stem, None, []
    auth_raw = auth_raw.strip().strip("_")
    if re.search(r"\bet al\.?$", auth_raw):
        authors = [auth_raw]                                # keep "X et al." intact
    else:
        authors = [p.strip() for p in re.split(r"\s+and\s+|\s*&\s*|,\s+", auth_raw)
                   if p.strip()]
    return title, year, authors


def ingest_zotero(limit=None, types=None, with_pdf_only=False):
    zdir = zr.detect_zotero_dir()
    if not zdir:
        raise SystemExit("ERROR: no Zotero library found. Set SCHOLAR_ZOTERO_DIR.")
    con_z, note = zr.open_zotero_db(zdir)
    sys.stderr.write("[ingest] zotero: %s (%s)\n" % (zdir, note))
    con = store.connect()
    n = with_pdf = 0
    for rec in zr.fetch_items(con_z, zdir, types=types,
                              with_pdf_only=with_pdf_only, limit=limit):
        if not rec.get("doc_id"):
            continue
        rec["source"] = "zotero"
        store.upsert_document(con, rec)
        n += 1
        if rec.get("n_pdf"):
            with_pdf += 1
    con.commit()
    store.write_manifest(source="zotero", zotero_dir=zdir)
    return {"ingested": n, "with_pdf": with_pdf, **store.counts(con)}


def ingest_folder(folder, limit=None):
    con = store.connect()
    pdfs = sorted(glob.glob(os.path.join(folder, "**", "*.pdf"), recursive=True))
    tag = "folder:" + os.path.basename(os.path.normpath(folder))
    n = 0
    for pdf in pdfs:
        if limit and n >= limit:
            break
        stem = os.path.splitext(os.path.basename(pdf))[0]
        title, year, authors = parse_zotero_filename(stem)
        # path-based id: stable + idempotent, dedupes re-runs of the same folder
        doc_id = hashlib.sha256(pdf.encode()).hexdigest()[:16]
        rec = {"doc_id": doc_id, "zotero_key": "", "doi": "", "title": title,
               "authors": authors, "year": year, "journal": "", "item_type": "pdf",
               "source": tag,
               "pdf_paths": [{"path": pdf, "exists": True, "link_mode": 2, "key": ""}]}
        store.upsert_document(con, rec)
        n += 1
    con.commit()
    store.write_manifest(source=tag, folder=folder)
    return {"ingested": n, "source": tag, **store.counts(con)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["zotero", "folder", "status"])
    ap.add_argument("folder", nargs="?")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--types", default=None)
    ap.add_argument("--with-pdf-only", action="store_true")
    args = ap.parse_args()
    if args.command == "status":
        print(json.dumps(store.counts(store.connect()), indent=2)); return
    if args.command == "zotero":
        res = ingest_zotero(limit=args.limit,
                            types=args.types.split(",") if args.types else None,
                            with_pdf_only=args.with_pdf_only)
    else:
        if not args.folder:
            raise SystemExit("folder mode needs a directory argument")
        res = ingest_folder(args.folder, limit=args.limit)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
