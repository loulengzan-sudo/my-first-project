#!/usr/bin/env python3
"""
chunk_embed.py — section-aware chunking + bge-m3 dense embeddings -> LanceDB.

For each extracted document (status='extracted'):
  1. rebuild full text with a char->page index (for accurate page citations)
  2. detect coarse sections (front / intro / methods / results / discussion /
     ... ); the References list is tagged and excluded from the chunk pool
  3. slide a ~500-token window (char-based, sentence-aware, with overlap)
  4. embed all chunks with BAAI/bge-m3 (normalized, MPS if available)
  5. write vectors + metadata to the LanceDB `chunks` table; mark embedded

Idempotent per document: existing LanceDB rows + chunk manifest rows for a doc
are deleted before re-adding, so re-runs converge. Resumable across runs.

Usage:
  python3 chunk_embed.py run [--limit N] [--force] [--batch 48]
  python3 chunk_embed.py model-info
"""
import os, re, sys, json, bisect, argparse
import store

EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
CHARS_PER_CHUNK = int(os.environ.get("RAG_CHUNK_CHARS", "2000"))   # ~500 tokens
CHUNK_OVERLAP = int(os.environ.get("RAG_CHUNK_OVERLAP", "300"))    # ~75 tokens
TABLE = "chunks"

# ---- section detection -------------------------------------------------------
# extract.py collapses single newlines into spaces (see _dehyphenate), so by the
# time text reaches here a 30-page paper is only a handful of "lines" and
# headings sit INLINE inside running text. Detection therefore anchors on
# structural boundaries rather than line starts. A line-based pass still runs
# afterwards for extractions that did preserve their line breaks.

# canonical order; used to force marks to progress monotonically through a paper
_CANON = ["abstract", "introduction", "literature", "methods",
          "results", "discussion", "conclusion", "references"]

# Case-SENSITIVE surface forms. Requiring Title Case / ALL CAPS is the single
# strongest filter against prose mentions ("as noted in the introduction ...").
_ALTS = {
    "abstract":     r"Abstract|ABSTRACT",
    "introduction": r"Introduction|INTRODUCTION",
    # Ordering matters: regex alternation is first-match, so multi-word forms
    # must precede the bare word ("Theoretical Framework" before "Theory",
    # "Related Works" before "Related Work") or the shorter form wins and the
    # heading is truncated mid-phrase.
    "literature":   r"Literature Review|LITERATURE REVIEW|Related Works|Related Work|"
                    r"RELATED WORK|Prior Research|Prior Literature|Prior Work|"
                    r"Previous Work|Theoretical Background|Theoretical Framework|"
                    r"THEORETICAL FRAMEWORK|Theoretical Considerations|"
                    r"Conceptual Framework|Background and Theory|"
                    r"Theory and Hypotheses|Theory and Hypothesis|Theory|THEORY",
    "methods":      r"Materials and Methods|MATERIALS AND METHODS|Data and Methods|"
                    r"DATA AND METHODS|Methods and Data|Methodology|METHODOLOGY|"
                    r"Methods|METHODS|Method|Research Design|Empirical Strategy|"
                    r"Empirical Approach|Study Design|Data and Measures",
    "results":      r"Results and Discussion|Results|RESULTS|Findings|FINDINGS|"
                    r"Empirical Results",
    "discussion":   r"Discussion and Conclusion[s]?|Discussion|DISCUSSION|General Discussion",
    "conclusion":   r"Conclusions|Conclusion|CONCLUSIONS?|Concluding Remarks",
    "references":   r"References|REFERENCES|Bibliography|BIBLIOGRAPHY|Works Cited|"
                    r"Literature Cited",
}

# A heading sits at a structural boundary (start of text, page break, or the end
# of the previous sentence), may carry a section number, and is followed by body
# text starting with a capital or digit.
_PRE = r"(?:\A|\n|(?<=[.!?])\s{1,4}|(?<=[.!?]\s))"
_NUM = r"(?:\d{1,2}\s*[.)]?\s+|[IVX]{1,5}\s*[.)]\s+)?"
_POST = r"\s*[:.—-]?\s+(?=[A-Z“\"(\d])"
_HEAD_RES = {lab: re.compile(_PRE + _NUM + r"(?:" + alt + r")" + _POST)
             for lab, alt in _ALTS.items()}

# Standalone-heading form, for extractor output that kept its newlines.
_SECTION_RE = re.compile(
    r"^\s*" + _NUM + r"(?P<h>" +
    "|".join("(?:%s)" % a for a in _ALTS.values()) + r")\s*:?\s*$")

# Where each section plausibly begins, as a fraction of the document. _PRIOR
# only breaks ties; _WINDOW is a hard reject for out-of-place matches.
_PRIOR = {"abstract": 0.02, "introduction": 0.08, "literature": 0.20,
          "methods": 0.40, "results": 0.58, "discussion": 0.75,
          "conclusion": 0.86, "references": 0.93}
_WINDOW = {"abstract": (0.0, 0.25), "introduction": (0.0, 0.45),
           "literature": (0.02, 0.60), "methods": (0.05, 0.80),
           "results": (0.10, 0.92), "discussion": (0.25, 0.97),
           "conclusion": (0.35, 0.99), "references": (0.40, 1.0)}


def _heading_candidates(full):
    """All plausible heading offsets per label, filtered by position window."""
    n = max(len(full), 1)
    out = {}
    for lab, rx in _HEAD_RES.items():
        lo, hi = _WINDOW[lab]
        hits = []
        for m in rx.finditer(full):
            s = m.start()
            while s < len(full) and full[s] in " \n\t":
                s += 1          # anchor on the word, not the leading boundary
            if lo <= s / n <= hi:
                hits.append(s)
        if hits:
            out[lab] = hits
    return out


def _choose_marks(cands, n):
    """At most one offset per label, strictly increasing in canonical order.

    Maximises how many sections get placed, breaking ties by closeness to the
    positional prior — a paper whose "Methods" only matches at 5% of the way in
    is better explained by dropping that match than by accepting it.
    """
    labs = [l for l in _CANON if l in cands]
    best = [None]

    def rec(i, prev, chosen, pen):
        if i == len(labs):
            key = (len(chosen), -pen)
            if best[0] is None or key > best[0][0]:
                best[0] = (key, list(chosen))
            return
        rec(i + 1, prev, chosen, pen)                  # skip this section
        for pos in cands[labs[i]]:                     # or take the first viable
            if pos > prev:
                chosen.append((labs[i], pos))
                rec(i + 1, pos, chosen, pen + abs(pos / n - _PRIOR[labs[i]]))
                chosen.pop()
                break

    rec(0, -1, [], 0.0)
    return best[0][1] if best[0] else []


def build_fulltext(pages):
    """Concatenate pages -> (full_text, page_starts) where page_starts[i] is
    the char offset at which page i+1 begins (for char->page mapping)."""
    parts, starts, off = [], [], 0
    for p in pages:
        starts.append(off)
        t = p["text"] + "\n\n"
        parts.append(t); off += len(t)
    return "".join(parts), starts


def char_to_page(offset, page_starts):
    # page_starts is ascending; page number is 1-based index of the last start <= offset
    return max(1, bisect.bisect_right(page_starts, offset))


def detect_sections(full_text):
    """Return list of (label, start_char, end_char) covering full_text.
    Unlabeled spans before the first heading are 'front'."""
    n = len(full_text)
    if n == 0:
        return [("front", 0, 0)]

    marks = _choose_marks(_heading_candidates(full_text), n)

    # Second pass for extractor output that preserved line breaks: a heading
    # alone on a short line is unambiguous, so it fills any label still missing.
    seen = {lab for lab, _ in marks}
    for m in re.finditer(r"^.{0,60}$", full_text, re.MULTILINE):
        sm = _SECTION_RE.match(m.group(0))
        if not sm:
            continue
        for lab, alt in _ALTS.items():
            if lab not in seen and re.fullmatch("(?:%s)" % alt, sm.group("h")):
                marks.append((lab, m.start())); seen.add(lab); break

    marks.sort(key=lambda t: t[1])
    if not marks or marks[0][1] > 0:
        marks = [("front", 0)] + marks

    spans = []
    for i, (label, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else n
        if end > start:                      # drop zero-width spans
            spans.append((label, start, end))
    return spans


def _split_points(text, size, overlap):
    """Yield (start, end) windows over text, preferring to end at a sentence
    or paragraph boundary near the target size."""
    n = len(text); i = 0
    while i < n:
        end = min(i + size, n)
        if end < n:
            window = text[end - 200:end + 200]
            # nearest sentence boundary after the soft end
            m = None
            for mm in re.finditer(r"[.!?]\s|\n\n", window):
                m = mm
            if m:
                end = (end - 200) + m.end()
        end = min(max(end, i + 1), n)
        yield i, end
        if end >= n:
            break
        i = max(end - overlap, i + 1)


def chunk_document(pages):
    """Return list of chunk dicts (text + span metadata), references excluded."""
    full, page_starts = build_fulltext(pages)
    spans = detect_sections(full)
    chunks, ordn = [], 0
    for label, s, e in spans:
        if label == "references":
            continue                        # drop the reference list from retrieval
        section_text = full[s:e]
        if len(section_text.strip()) < 40:
            continue
        for cs, ce in _split_points(section_text, CHARS_PER_CHUNK, CHUNK_OVERLAP):
            text = section_text[cs:ce].strip()
            if len(text) < 40:
                continue
            abs_start = s + cs
            chunks.append({
                "ord": ordn, "section": label, "text": text,
                "char_start": abs_start, "char_end": s + ce,
                "n_chars": len(text),
                "page_start": char_to_page(abs_start, page_starts),
                "page_end": char_to_page(s + ce, page_starts),
                "text_sha256": store.sha256_text(text),
            })
            ordn += 1
    return chunks


# ---- embedding + LanceDB -----------------------------------------------------

_MODEL = None


def get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        import torch
        dev = "mps" if torch.backends.mps.is_available() else (
            "cuda" if torch.cuda.is_available() else "cpu")
        sys.stderr.write("[embed] loading %s on %s\n" % (EMBED_MODEL, dev))
        _MODEL = SentenceTransformer(EMBED_MODEL, device=dev)
    return _MODEL


def emb_dim(model):
    # sentence-transformers renamed the accessor across versions
    for attr in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        if hasattr(model, attr):
            return getattr(model, attr)()
    return len(model.encode(["_"], convert_to_numpy=True)[0])


def embed_texts(texts, batch=48):
    model = get_model()
    return model.encode(texts, batch_size=batch, normalize_embeddings=True,
                        show_progress_bar=False, convert_to_numpy=True)


def open_table(dim):
    import lancedb
    db = lancedb.connect(os.path.join(store.rag_dir(), "index"))
    if hasattr(db, "list_tables"):
        _r = db.list_tables()
        tables = list(getattr(_r, "tables", _r))   # ListTablesResponse(.tables) or list
    else:
        tables = list(db.table_names())
    if TABLE in tables:
        return db, db.open_table(TABLE)
    # create with an explicit schema by seeding one dummy row then deleting it
    import pyarrow as pa
    schema = pa.schema([
        pa.field("chunk_id", pa.string()),
        pa.field("doc_id", pa.string()),
        pa.field("ord", pa.int32()),
        pa.field("section", pa.string()),
        pa.field("page_start", pa.int32()),
        pa.field("page_end", pa.int32()),
        pa.field("title", pa.string()),
        pa.field("authors", pa.string()),
        pa.field("year", pa.int32()),
        pa.field("journal", pa.string()),
        pa.field("doi", pa.string()),
        pa.field("text", pa.string()),
        pa.field("vector", pa.list_(pa.float32(), dim)),
    ])
    tbl = db.create_table(TABLE, schema=schema)
    return db, tbl


def run(con, limit=None, force=False, batch=48):
    todo = list(store.iter_documents(con, status="extracted", with_pdf=True))
    if force:
        todo = list(store.iter_documents(con, with_pdf=True))
    sys.stderr.write("[chunk_embed] %d documents to embed\n" % len(todo))
    if not todo:
        return {"docs": 0, "chunks": 0}
    dim = emb_dim(get_model())
    db, tbl = open_table(dim)
    total_chunks = docs_done = 0
    for i, d in enumerate(todo):
        if limit and i >= limit:
            break
        tp = d["text_path"]
        if not tp or not os.path.isfile(tp):
            store.set_status(con, d["doc_id"], "failed", "missing text cache")
            continue
        pages = json.load(open(tp)).get("pages", [])
        chunks = chunk_document(pages)
        if not chunks:
            store.set_status(con, d["doc_id"], "failed", "no chunks produced")
            continue
        vecs = embed_texts([c["text"] for c in chunks], batch=batch)
        # join authors with ';' — each author is "Last, First" (has an internal
        # comma), so ';' keeps author boundaries unambiguous for the formatter
        authors = "; ".join(json.loads(d["authors_json"] or "[]")[:8])
        rows = []
        for c, v in zip(chunks, vecs):
            rows.append({
                "chunk_id": "%s:%d" % (d["doc_id"], c["ord"]),
                "doc_id": d["doc_id"], "ord": c["ord"], "section": c["section"],
                "page_start": c["page_start"], "page_end": c["page_end"],
                "title": d["title"] or "", "authors": authors,
                "year": int(d["year"]) if d["year"] else 0,
                "journal": d["journal"] or "", "doi": d["doi"] or "",
                "text": c["text"], "vector": v.tolist()})
        tbl.delete("doc_id = '%s'" % d["doc_id"])     # idempotent re-embed
        tbl.add(rows)
        store.record_chunks(con, d["doc_id"], chunks)
        con.execute("UPDATE chunks SET embedded=1 WHERE doc_id=?", (d["doc_id"],))
        store.set_status(con, d["doc_id"], "embedded")
        total_chunks += len(chunks); docs_done += 1
        if (i + 1) % 25 == 0:
            sys.stderr.write("  ...%d/%d docs, %d chunks\n"
                             % (i + 1, len(todo), total_chunks))
    # build/refresh the full-text (BM25) index for hybrid search
    try:
        tbl.create_fts_index("text", replace=True)
    except Exception as e:
        sys.stderr.write("  [fts] index build skipped: %s\n" % e)
    store.write_manifest(embed_model=EMBED_MODEL, embed_dim=dim,
                         chunk_chars=CHARS_PER_CHUNK, chunk_overlap=CHUNK_OVERLAP)
    return {"docs": docs_done, "chunks": total_chunks}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["run", "model-info"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--batch", type=int, default=48)
    args = ap.parse_args()
    if args.command == "model-info":
        m = get_model()
        print(json.dumps({"model": EMBED_MODEL, "dim": emb_dim(m)}, indent=2))
        return
    con = store.connect()
    res = run(con, limit=args.limit, force=args.force, batch=args.batch)
    print(json.dumps({**res, **store.counts(con)}, indent=2))


if __name__ == "__main__":
    main()
