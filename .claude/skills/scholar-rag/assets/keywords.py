#!/usr/bin/env python3
"""
keywords.py — fold human/curated keyword vocabularies into the entity graph.

Motivation
----------
The graph was built entirely from LLM-extracted entities, while three sources of
*human- or curator-assigned* keywords sat unused:

  zotero    itemTags — 5.6k distinct tags over ~69% of the library. Publisher /
            author keywords land here on import. Never read: zotero_reader.py
            contained no mention of tags and corpus.sqlite has no column.
  openalex  `topics` / `keywords` / `concepts` on each work — a curated, scored,
            normalized taxonomy. Returned by the very same call citations.py
            already makes, so it costs nothing extra.
  intext    The "Keywords: a; b; c" line under the abstract (~39% of papers).
            Author-supplied and highly precise.

These matter disproportionately because they are *inherently multi-document*
("machine learning" tags 52 papers), and the entity graph's central weakness is
that most LLM entities appear in exactly one paper and so link nothing.
They also carry no hallucination risk.

Entities are written with source="kw:<origin>" and merged into the existing
graph by the same normalized identity used everywhere else, so a keyword that
matches an LLM-extracted entity reinforces that node rather than duplicating it.

Usage:
  python3 keywords.py load [--sources zotero,openalex,intext] [--min-score 0.3]
  python3 keywords.py status
"""
import os, re, sys, json, sqlite3, argparse
import store
import graphrag as G

# Tags that are bookkeeping rather than topic. Extended by --exclude.
_STOP_TAGS = {
    "migrated-yang-zotero-2026-08", "unread", "to read", "toread", "read",
    "important", "todo", "favorite", "starred", "duplicate", "_tablet",
}
_STOP_PREFIX = ("migrated-", "zotero-", "_")


def _zotero_dir():
    import zotero_reader
    return zotero_reader.detect_zotero_dir(os.environ.get("SCHOLAR_ZOTERO_DIR"))


def from_zotero(con):
    """zotero_key -> [tag]. Read live from zotero.sqlite (read-only, no lock)."""
    zdir = _zotero_dir()
    if not zdir:
        sys.stderr.write("[keywords] no Zotero dir found; skipping zotero tags\n")
        return {}
    p = os.path.join(zdir, "zotero.sqlite")
    if not os.path.isfile(p):
        return {}
    try:
        z = sqlite3.connect("file:%s?immutable=1" % p, uri=True)
    except Exception as e:
        sys.stderr.write("[keywords] cannot open zotero.sqlite: %s\n" % e)
        return {}
    out = {}
    q = """SELECT i.key, t.name FROM itemTags it
           JOIN tags t USING(tagID) JOIN items i USING(itemID)
           WHERE i.itemID NOT IN (SELECT itemID FROM deletedItems)"""
    for key, name in z.execute(q):
        nm = (name or "").strip()
        low = nm.lower()
        if not nm or low in _STOP_TAGS or low.startswith(_STOP_PREFIX):
            continue
        out.setdefault(key, []).append(nm)
    return out


def from_openalex(min_score=0.3):
    """doc_id -> [keyword]. Reads the cache citations.py fetch already wrote."""
    cdir = os.path.join(store.rag_dir(), "raw", "openalex")
    if not os.path.isdir(cdir):
        return {}
    out = {}
    for fn in os.listdir(cdir):
        if not fn.endswith(".json"):
            continue
        try:
            rec = json.load(open(os.path.join(cdir, fn)))
        except Exception:
            continue
        if rec.get("missing"):
            continue
        names = []
        # topics are the coarse, high-confidence layer -> always keep
        for t in rec.get("topics") or []:
            if t.get("name"):
                names.append(t["name"])
        for f in ("keywords", "concepts"):
            for t in rec.get(f) or []:
                sc = t.get("score")
                if t.get("name") and (sc is None or sc >= min_score):
                    names.append(t["name"])
        if names:
            out[fn[:-5]] = names
    return out


_KW_RE = re.compile(r"\bkey\s?words?\b\s*[:\-—]?\s*(.{10,300})", re.I | re.S)
_SPLIT_RE = re.compile(r"[;,·|]|(?:\s{3,})")


def from_intext(con, limit=None):
    """doc_id -> [keyword] parsed from the 'Keywords:' line under the abstract."""
    out = {}
    rows = con.execute(
        "SELECT doc_id, text_path FROM documents WHERE text_path IS NOT NULL "
        "AND status IN ('embedded','extracted','chunked')").fetchall()
    for n, r in enumerate(rows):
        if limit and n >= limit:
            break
        tp = r["text_path"]
        if not tp or not os.path.isfile(tp):
            continue
        try:
            pages = json.load(open(tp)).get("pages", [])[:2]
        except Exception:
            continue
        head = "\n".join(p.get("text", "") for p in pages)
        m = _KW_RE.search(head)
        if not m:
            continue
        raw = " ".join(m.group(1).split())
        # The line runs into body text; keep only well-formed short phrases and
        # stop at the first token that looks like prose (very long, or a digit
        # affiliation marker).
        parts, kws = _SPLIT_RE.split(raw), []
        for p in parts:
            p = p.strip(" .:-—")
            if not p:
                continue
            wc = len(p.split())
            if wc > 6 or len(p) > 60 or len(p) < 3:
                break
            if re.search(r"\d\s*(department|university|school|college)", p, re.I):
                break
            kws.append(p)
            if len(kws) >= 12:
                break
        if kws:
            out[r["doc_id"]] = kws
    return out


def load(sources=("zotero", "openalex", "intext"), min_score=0.3, limit=None):
    con = store.connect()
    ents = G.load_entities()
    before = len(ents)

    per_doc = {}          # doc_id -> {(name, origin)}
    if "zotero" in sources:
        bykey = from_zotero(con)
        if bykey:
            rows = con.execute(
                "SELECT doc_id, zotero_key FROM documents "
                "WHERE zotero_key IS NOT NULL AND zotero_key != ''").fetchall()
            for r in rows:
                for nm in bykey.get(r["zotero_key"], []):
                    per_doc.setdefault(r["doc_id"], set()).add((nm, "zotero"))
    if "openalex" in sources:
        for d, names in from_openalex(min_score).items():
            for nm in names:
                per_doc.setdefault(d, set()).add((nm, "openalex"))
    if "intext" in sources:
        for d, names in from_intext(con, limit=limit).items():
            for nm in names:
                per_doc.setdefault(d, set()).add((nm, "intext"))

    stats = {"zotero": 0, "openalex": 0, "intext": 0}
    for doc_id, pairs in per_doc.items():
        for nm, origin in pairs:
            eid = G._merge_entity(ents, nm, "concept", doc_id,
                                  source="kw:%s" % origin)
            if eid:
                stats[origin] += 1
                e = ents[eid]
                # mark provenance without clobbering a curated `kg` source
                if e.get("source", "").startswith("extract"):
                    e["source"] = "kw:%s" % origin
    G.save_entities(ents)

    from collections import Counter
    dc = Counter(len(e.get("doc_ids", [])) for e in ents.values())
    multi = sum(v for k, v in dc.items() if k >= 2)
    return {"docs_with_keywords": len(per_doc),
            "assignments": stats,
            "entities_before": before, "entities_after": len(ents),
            "multi_doc_entities": multi,
            "multi_doc_pct": round(100.0 * multi / max(1, len(ents)), 1)}


def status():
    con = store.connect()
    ents = G.load_entities()
    from collections import Counter
    src = Counter(e.get("source", "?").split(":")[0] for e in ents.values())
    kw = Counter(e.get("source", "") for e in ents.values()
                 if str(e.get("source", "")).startswith("kw:"))
    return {"entities": len(ents), "by_source": dict(src),
            "keyword_entities": dict(kw),
            "zotero_tagged_items": len(from_zotero(con)),
            "openalex_cached_with_topics": len(from_openalex())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["load", "status"])
    ap.add_argument("--sources", default="zotero,openalex,intext")
    ap.add_argument("--min-score", type=float, default=0.3)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    if a.command == "load":
        print(json.dumps(load(tuple(s.strip() for s in a.sources.split(",")),
                              min_score=a.min_score, limit=a.limit), indent=2))
    else:
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
