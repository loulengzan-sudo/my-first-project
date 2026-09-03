#!/usr/bin/env python3
"""
query.py — semantic retrieval over the scholar-rag index (what MCP wraps).

Dense vector search over bge-m3 embeddings, with optional hybrid fusion against
the LanceDB full-text (BM25) index via Reciprocal Rank Fusion, optional metadata
filters (section / year / doi), and optional cross-encoder reranking.

Usage:
  python3 query.py "how does residential segregation affect social mobility?" \
        [-k 8] [--section methods,results] [--year-min 2000] [--year-max 2024] \
        [--hybrid] [--rerank] [--json]
"""
import os, sys, json, argparse, re
# Retrieval is a read path: the embedding model is already cached by ingest, so
# stay offline to avoid HF Hub round-trips on every query / MCP cold start.
# (Override with HF_HUB_OFFLINE=0 the first time you use an uncached reranker.)
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
import store

EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.environ.get("RAG_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
TABLE = "chunks"

_MODEL = None
_RERANKER = None


def get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer
        import torch
        dev = "mps" if torch.backends.mps.is_available() else (
            "cuda" if torch.cuda.is_available() else "cpu")
        _MODEL = SentenceTransformer(EMBED_MODEL, device=dev)
    return _MODEL


def _where(section=None, year_min=None, year_max=None, doi=None):
    conds = []
    if section:
        secs = ",".join("'%s'" % s.strip() for s in section.split(","))
        conds.append("section IN (%s)" % secs)
    if year_min:
        conds.append("year >= %d" % int(year_min))
    if year_max:
        conds.append("year <= %d and year > 0" % int(year_max))
    if doi:
        conds.append("doi = '%s'" % doi.replace("'", "''"))
    return " AND ".join(conds) if conds else None


def _list_tables(db):
    # newer lancedb: list_tables() -> ListTablesResponse(.tables=[...]);
    # older: table_names() -> [...]
    if hasattr(db, "list_tables"):
        r = db.list_tables()
        return list(getattr(r, "tables", r))
    return list(db.table_names())


def _open_table():
    import lancedb
    db = lancedb.connect(os.path.join(store.rag_dir(), "index"))
    if TABLE not in _list_tables(db):
        raise SystemExit("ERROR: no index yet. Run ingest -> extract -> chunk_embed.")
    return db.open_table(TABLE)


def _fmt_cite(r):
    # authors stored as "Last, First; Last, First; ..." -> split on ';'
    au = [a.strip() for a in (r.get("authors") or "").split(";") if a.strip()]
    first_last = au[0].split(",")[0].strip() if au else "?"
    etal = " et al." if len(au) > 1 else ""
    yr = r.get("year") or "n.d."
    pg = r.get("page_start")
    pgs = (" p.%s" % pg) if pg else ""
    return "%s%s (%s)%s" % (first_last, etal, yr, pgs)


def search(query, k=8, section=None, year_min=None, year_max=None, doi=None,
           hybrid=False, rerank=False, overfetch=None):
    tbl = _open_table()
    where = _where(section, year_min, year_max, doi)
    nfetch = overfetch or (max(k * 5, 30) if (hybrid or rerank) else k)

    qv = get_model().encode([query], normalize_embeddings=True,
                            convert_to_numpy=True)[0].tolist()
    vq = tbl.search(qv, vector_column_name="vector").metric("cosine")
    if where:
        vq = vq.where(where, prefilter=True)
    vres = vq.limit(nfetch).to_list()

    ranked = vres
    if hybrid:
        try:
            fq = tbl.search(query, query_type="fts")
            if where:
                fq = fq.where(where, prefilter=True)
            fres = fq.limit(nfetch).to_list()
            ranked = _rrf(vres, fres, k=k * 4)
        except Exception as e:
            sys.stderr.write("[hybrid] FTS unavailable, dense only: %s\n" % e)

    if rerank:
        ranked = _rerank(query, ranked, top=nfetch)

    out = []
    for r in ranked[:k]:
        # normalize to a similarity where higher = better across all modes:
        # hybrid/rerank carry _score (higher better); dense carries _distance
        # (cosine distance, lower better) -> similarity = 1 - distance
        if r.get("_score") is not None:
            score = float(r["_score"])
        elif r.get("_distance") is not None:
            score = 1.0 - float(r["_distance"])
        else:
            score = 0.0
        out.append({
            "chunk_id": r.get("chunk_id"), "doc_id": r.get("doc_id"),
            "cite": _fmt_cite(r), "title": r.get("title"),
            "section": r.get("section"),
            "pages": [r.get("page_start"), r.get("page_end")],
            "year": r.get("year"), "journal": r.get("journal"),
            "doi": r.get("doi"),
            "score": round(score, 4),
            "text": r.get("text"),
        })
    return out


def _rrf(list_a, list_b, k=60, c=60):
    """Reciprocal Rank Fusion of two result lists keyed by chunk_id."""
    score = {}
    rows = {}
    for lst in (list_a, list_b):
        for rank, r in enumerate(lst):
            cid = r.get("chunk_id")
            if cid is None:
                continue
            score[cid] = score.get(cid, 0.0) + 1.0 / (c + rank + 1)
            rows[cid] = r
    order = sorted(score, key=lambda cid: -score[cid])
    fused = []
    for cid in order[:k]:
        r = dict(rows[cid]); r["_score"] = score[cid]; fused.append(r)
    return fused


def _rerank(query, rows, top=None):
    global _RERANKER
    if not rows:
        return rows
    try:
        from sentence_transformers import CrossEncoder
        import torch
        if _RERANKER is None:
            dev = "mps" if torch.backends.mps.is_available() else "cpu"
            _RERANKER = CrossEncoder(RERANK_MODEL, device=dev)
        pairs = [(query, r.get("text", "")) for r in rows]
        scores = _RERANKER.predict(pairs)
        for r, s in zip(rows, scores):
            r["_score"] = float(s)
        return sorted(rows, key=lambda r: -r["_score"])
    except Exception as e:
        sys.stderr.write("[rerank] unavailable, keeping order: %s\n" % e)
        return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--section", default=None)
    ap.add_argument("--year-min", type=int, default=None)
    ap.add_argument("--year-max", type=int, default=None)
    ap.add_argument("--doi", default=None)
    ap.add_argument("--hybrid", action="store_true")
    ap.add_argument("--rerank", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    res = search(args.query, k=args.k, section=args.section,
                 year_min=args.year_min, year_max=args.year_max, doi=args.doi,
                 hybrid=args.hybrid, rerank=args.rerank)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2)); return
    for i, r in enumerate(res, 1):
        print("\n[%d] %s  — %s §%s  (score %.3f)"
              % (i, r["cite"], (r["title"] or "")[:70], r["section"], r["score"]))
        snippet = re.sub(r"\s+", " ", r["text"])[:360]
        print("    " + snippet)


if __name__ == "__main__":
    main()
