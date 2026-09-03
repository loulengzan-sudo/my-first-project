#!/usr/bin/env python3
"""
extract.py — PDF -> page-mapped full text (the "doc2rag" extraction ladder).

Ladder per document:
  1. PyMuPDF (fitz)          fast, layout-aware, born-digital PDFs (most papers)
  2. pdftotext CLI (poppler) fallback if fitz is unavailable
  3. vision-OCR (ollama)     scanned/image PDFs whose text yield is too low
                             (only when --ocr is passed; llama3.2-vision)

Writes raw/text/<doc_id>.json = {"pages":[{"page":i,"text":...}], "method",
"n_pages","n_chars","quality"}. Updates the corpus.sqlite manifest. Resumable:
already-extracted docs are skipped unless --force.

Usage:
  python3 extract.py run [--limit N] [--force] [--ocr] [--min-cpp 80]
  python3 extract.py one <pdf_path>          # ad-hoc, prints stats
"""
import os, re, sys, json, argparse, subprocess
import store

# text-yield threshold: avg chars/page below this => likely scanned => OCR
DEFAULT_MIN_CPP = 80


def _dehyphenate(text):
    """Join lines that merely WRAPPED; keep line breaks that carry structure.

    A PDF extractor emits one newline per visual line, so prose arrives broken
    at the column width. Collapsing every single newline (the original rule)
    reflowed the prose but also destroyed standalone heading lines, leaving
    chunk_embed.detect_sections nothing to anchor on — 98% of chunks came back
    labelled 'front'.

    A line that ran to the column edge wrapped; a line that stopped short ended
    deliberately (heading, author line, list item, last line of a paragraph).
    So a break is kept unless the line reached most of the body width — or
    unless the next line opens lowercase, which is an unambiguous mid-sentence
    continuation. Headings are followed by capitalised body text, so that second
    rule never swallows them.
    """
    # join words split across line breaks:  "popula-\ntion" -> "population"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+\n", "\n", text)

    lines = text.split("\n")
    lens = sorted(len(l.rstrip()) for l in lines if l.strip())
    if not lens:
        return text.strip()
    # 75th percentile approximates the body column width; a plain median would
    # be dragged down by table cells and figure labels on dense pages.
    width = lens[min(int(len(lens) * 0.75), len(lens) - 1)]
    join_min = max(30, int(width * 0.75))

    out, buf = [], ""
    for i, raw in enumerate(lines):
        ln = raw.rstrip()
        if not ln.strip():                      # blank line = paragraph break
            if buf:
                out.append(buf); buf = ""
            out.append("")
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        buf = (buf + " " + ln.strip()).strip() if buf else ln.strip()
        continues = bool(re.match(r"^[a-z]", nxt))
        if not nxt or (len(ln) < join_min and not continues):
            out.append(buf); buf = ""
    if buf:
        out.append(buf)

    text = "\n".join(out)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_fitz(pdf_path):
    import fitz  # PyMuPDF
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        raw = page.get_text("text") or ""
        pages.append({"page": i + 1, "text": _dehyphenate(raw)})
    n_pages = len(pages)
    doc.close()
    return pages, n_pages, "pymupdf"


def extract_pdftotext(pdf_path):
    # poppler CLI fallback; -layout keeps columns readable, \f = page breaks
    out = subprocess.run(["pdftotext", "-layout", pdf_path, "-"],
                         capture_output=True, text=True, timeout=300)
    raw_pages = out.stdout.split("\f")
    pages = [{"page": i + 1, "text": _dehyphenate(p)}
             for i, p in enumerate(raw_pages) if p.strip()]
    return pages, len(pages), "pdftotext"


def ocr_vision(pdf_path, model=None, dpi=150, max_pages=40):
    """OCR a scanned PDF via ollama's vision model (fully local).

    Renders each page to PNG (fitz) and asks the model to transcribe. Bounded
    by max_pages to keep runaway scans in check. Requires `ollama` on PATH.
    """
    import fitz, base64, requests
    model = model or os.environ.get("RAG_OCR_MODEL", "llama3.2-vision:90b")
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        pix = page.get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
        b64 = base64.b64encode(png).decode()
        r = requests.post(host + "/api/generate", json={
            "model": model, "stream": False,
            "prompt": "Transcribe all text from this scanned academic page "
                      "verbatim. Output only the text, preserving paragraphs.",
            "images": [b64]}, timeout=600)
        txt = r.json().get("response", "") if r.ok else ""
        pages.append({"page": i + 1, "text": _dehyphenate(txt)})
    doc.close()
    return pages, len(pages), "vision-ocr"


def quality_of(pages, n_pages):
    total = sum(len(p["text"]) for p in pages)
    cpp = (total / n_pages) if n_pages else 0
    # normalize to 0..1 (saturates ~2000 chars/page for a dense paper)
    return total, cpp, min(1.0, cpp / 2000.0)


def extract_one(pdf_path, do_ocr=False, min_cpp=DEFAULT_MIN_CPP):
    """Run the ladder; return (pages, meta dict)."""
    method = None; ocr_used = 0
    try:
        pages, n_pages, method = extract_fitz(pdf_path)
    except Exception:
        pages, n_pages, method = extract_pdftotext(pdf_path)
    total, cpp, q = quality_of(pages, n_pages)
    needs_ocr = cpp < min_cpp
    if needs_ocr and do_ocr:
        try:
            opages, on_pages, _ = ocr_vision(pdf_path)
            ototal, ocpp, oq = quality_of(opages, on_pages)
            if ototal > total:                       # OCR won -> use it
                pages, n_pages, method, ocr_used = opages, on_pages, "vision-ocr", 1
                total, cpp, q = ototal, ocpp, oq
                needs_ocr = False
        except Exception as e:
            sys.stderr.write("  [ocr] failed: %s\n" % e)
    meta = {"method": method, "n_pages": n_pages, "n_chars": total,
            "quality": round(q, 3), "cpp": round(cpp, 1),
            "needs_ocr": needs_ocr, "ocr_used": ocr_used}
    return pages, meta


def run(con, limit=None, force=False, do_ocr=False, min_cpp=DEFAULT_MIN_CPP):
    root = store.rag_dir()
    statuses = ("new", "failed") if not force else None
    todo = []
    if force:
        todo = list(store.iter_documents(con, with_pdf=True))
    else:
        for st in statuses:
            todo += list(store.iter_documents(con, status=st, with_pdf=True))
    sys.stderr.write("[extract] %d documents to process\n" % len(todo))
    ok = fail = skipped = 0
    for i, d in enumerate(todo):
        if limit and i >= limit:
            break
        pdf = d["pdf_path"]
        if not pdf or not os.path.isfile(pdf):
            store.set_status(con, d["doc_id"], "no_pdf", "pdf missing on disk")
            skipped += 1; continue
        try:
            pages, meta = extract_one(pdf, do_ocr=do_ocr, min_cpp=min_cpp)
            text_path = os.path.join(root, "raw", "text", d["doc_id"] + ".json")
            json.dump({"doc_id": d["doc_id"], "pages": pages, **meta},
                      open(text_path, "w"), ensure_ascii=False)
            status = "extracted" if not meta["needs_ocr"] else "extracted"
            store.set_extract_result(
                con, d["doc_id"], method=meta["method"], n_pages=meta["n_pages"],
                n_chars=meta["n_chars"], quality=meta["quality"],
                text_path=text_path, pdf_sha256=store.sha256_file(pdf),
                ocr_used=meta["ocr_used"], status=status)
            ok += 1
            if (i + 1) % 50 == 0:
                sys.stderr.write("  ...%d/%d  (%s, %dch)\n"
                                 % (i + 1, len(todo), meta["method"], meta["n_chars"]))
        except Exception as e:
            store.set_status(con, d["doc_id"], "failed", str(e)[:300])
            fail += 1
    sys.stderr.write("[extract] ok=%d fail=%d skipped=%d\n" % (ok, fail, skipped))
    return {"ok": ok, "fail": fail, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "one"])
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--ocr", action="store_true")
    ap.add_argument("--min-cpp", type=int, default=DEFAULT_MIN_CPP)
    args = ap.parse_args()
    if args.command == "one":
        pages, meta = extract_one(args.pdf, do_ocr=args.ocr, min_cpp=args.min_cpp)
        preview = (pages[0]["text"][:400] if pages else "")
        print(json.dumps({**meta, "preview": preview}, indent=2))
        return
    con = store.connect()
    res = run(con, limit=args.limit, force=args.force,
              do_ocr=args.ocr, min_cpp=args.min_cpp)
    print(json.dumps({**res, **store.counts(con)}, indent=2))


if __name__ == "__main__":
    main()
