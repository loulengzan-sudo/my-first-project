#!/usr/bin/env python3
"""
citations.py — bibliographic (paper-to-paper) layer for scholar-rag.

Why this exists
---------------
Before this module the graph had 703 citation edges, all inherited from the
scholar-knowledge seed and covering ~12% of the corpus — and they were read
only by `graphrag neighbors`, never by `graphrag build`. Community detection
therefore ran on shared terminology alone, with zero bibliographic input.

This module builds the missing layer from OpenAlex, which returns each work's
full `referenced_works` list. Three derived relations, in increasing coverage:

  direct citation   A cites B, both in the library.
                    Highest precision, but needs BOTH ends in the corpus.
  bibliographic     A and B cite the same work. The shared reference does NOT
  coupling          need to be in the library, so this covers far more pairs
                    than direct citation, and is stable for recent papers.
  co-citation       A and B are both cited by some in-corpus paper. The classic
                    complement to coupling; reflects how the field groups work.

All three are computed from outbound reference lists only — no inbound-citation
crawl, so one API call per DOI is sufficient.

Store layout (under $SCHOLAR_RAG_DIR):
  raw/openalex/<doc_id>.json     cached OpenAlex record (resumable)
  graph/citations.ndjson         direct intra-corpus edges
  graph/paper_edges.ndjson       weighted union graph (direct+coupling+cocite)
  graph/paper_communities.json   {doc_id: community_id}

Usage:
  python3 citations.py fetch  [--limit N] [--sleep 0.12]
  python3 citations.py build  [--min-coupling 2] [--min-cocite 2]
  python3 citations.py status
  python3 citations.py neighbors <doc_id> [-k 10]
"""
import os, sys, json, time, argparse, urllib.parse
import store

EMAIL = (os.environ.get("SCHOLAR_CROSSREF_EMAIL")
         or os.environ.get("SCHOLAR_UNPAYWALL_EMAIL")
         or "research@example.com")
UA = {"User-Agent": "scholar-rag/0.1 (mailto:%s)" % EMAIL}


def gdir():
    d = os.path.join(store.rag_dir(), "graph")
    os.makedirs(d, exist_ok=True)
    return d


def cache_dir():
    d = os.path.join(store.rag_dir(), "raw", "openalex")
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(doc_id):
    return os.path.join(cache_dir(), "%s.json" % doc_id)


def _load_ndjson(p):
    if not os.path.isfile(p):
        return []
    out = []
    for line in open(p):
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _short_oa(oaid):
    """https://openalex.org/W123 -> W123 (compact keys, same identity)."""
    return (oaid or "").rsplit("/", 1)[-1]


# ---- fetch -------------------------------------------------------------------

def fetch(limit=None, sleep=0.12, refresh=False):
    """One OpenAlex call per DOI; caches {id, referenced_works, cited_by_count}.

    Resumable: a cached doc_id is skipped. Papers with no DOI are skipped
    entirely (OpenAlex title-matching is unreliable enough to inject false
    citation edges, which is worse than a missing one).
    """
    import requests
    con = store.connect()
    rows = [d for d in store.iter_documents(con, with_pdf=False)
            if (d["doi"] or "").strip()]
    todo = [d for d in rows
            if refresh or not os.path.isfile(_cache_path(d["doc_id"]))]
    sys.stderr.write("[citations.fetch] %d docs with DOI, %d to fetch\n"
                     % (len(rows), len(todo)))
    ok = miss = err = 0
    for i, d in enumerate(todo):
        if limit and i >= limit:
            break
        doi = d["doi"].strip()
        url = ("https://api.openalex.org/works/doi:%s?mailto=%s"
               % (urllib.parse.quote(doi), urllib.parse.quote(EMAIL)))
        rec = None
        for attempt in range(4):
            try:
                r = requests.get(url, headers=UA, timeout=30)
                if r.status_code == 429:           # polite backoff
                    time.sleep(2 ** attempt)
                    continue
                if r.status_code == 404:
                    rec = {"missing": True}
                    break
                if r.ok:
                    j = r.json()
                    # Topics/keywords come free with the same call. They are a
                    # curated, scored, cross-paper vocabulary — far higher
                    # precision than LLM-extracted entities and inherently
                    # multi-document, which is exactly what the entity graph
                    # is starved of. Discarding them would waste the request.
                    def _named(field, cap=12):
                        out = []
                        for x in (j.get(field) or [])[:cap]:
                            nm = (x or {}).get("display_name")
                            if nm:
                                out.append({"name": nm,
                                            "score": (x or {}).get("score")})
                        return out
                    rec = {"openalex_id": _short_oa(j.get("id")),
                           "referenced_works": [_short_oa(x) for x in
                                                (j.get("referenced_works") or [])],
                           "cited_by_count": j.get("cited_by_count"),
                           "publication_year": j.get("publication_year"),
                           "topics": _named("topics", 4),
                           "keywords": _named("keywords"),
                           "concepts": _named("concepts")}
                    break
                time.sleep(1.0 + attempt)
            except Exception:
                time.sleep(1.0 + attempt)
        if rec is None:
            err += 1
        else:
            json.dump(rec, open(_cache_path(d["doc_id"]), "w"))
            if rec.get("missing"):
                miss += 1
            else:
                ok += 1
        if (i + 1) % 100 == 0:
            sys.stderr.write("  ...%d/%d fetched (ok=%d missing=%d err=%d)\n"
                             % (i + 1, len(todo), ok, miss, err))
        time.sleep(sleep)
    return {"with_doi": len(rows), "attempted": min(len(todo), limit or len(todo)),
            "ok": ok, "not_in_openalex": miss, "errors": err}


# ---- build -------------------------------------------------------------------

def _load_cache():
    """doc_id -> record, for every cached, resolvable work."""
    out = {}
    for fn in os.listdir(cache_dir()):
        if not fn.endswith(".json"):
            continue
        doc_id = fn[:-5]
        try:
            rec = json.load(open(os.path.join(cache_dir(), fn)))
        except Exception:
            continue
        if rec.get("missing") or not rec.get("openalex_id"):
            continue
        out[doc_id] = rec
    return out


def build(min_coupling=2, min_cocite=2, w_direct=3.0, w_couple=1.0, w_cocite=1.0,
          w_semantic=1.0, dup_sim=0.995, resolution=None):
    """Derive citation / coupling / co-citation edges and cluster the papers.

    Weights favour direct citation because it is an assertion by the author,
    whereas coupling and co-citation are statistical. Thresholds keep the
    graph from degenerating: a single shared reference is weak evidence
    (two papers can share one methods citation and be unrelated).
    """
    cache = _load_cache()
    if not cache:
        return {"error": "no OpenAlex cache; run `citations.py fetch` first"}
    oa2doc = {r["openalex_id"]: d for d, r in cache.items()}
    refs = {d: set(r.get("referenced_works") or []) for d, r in cache.items()}

    # 1) direct intra-corpus citation
    direct = []
    for d, rs in refs.items():
        for r in rs:
            tgt = oa2doc.get(r)
            if tgt and tgt != d:
                direct.append({"src_doc": d, "dst_doc": tgt, "rel": "cites",
                               "source": "openalex"})
    with open(os.path.join(gdir(), "citations.ndjson"), "w") as f:
        for e in direct:
            f.write(json.dumps(e) + "\n")

    # 2) bibliographic coupling — shared outbound references (in or out of corpus)
    by_ref = {}
    for d, rs in refs.items():
        for r in rs:
            by_ref.setdefault(r, []).append(d)
    coupling = {}
    for r, ds in by_ref.items():
        if len(ds) < 2 or len(ds) > 200:      # a 200+-way shared ref is boilerplate
            continue
        ds = sorted(set(ds))
        for i in range(len(ds)):
            for j in range(i + 1, len(ds)):
                k = (ds[i], ds[j])
                coupling[k] = coupling.get(k, 0) + 1
    coupling = {k: v for k, v in coupling.items() if v >= min_coupling}

    # 3) co-citation — two works cited together by the same in-corpus paper.
    #    Only pairs where BOTH ends are in the corpus are useful to us.
    cocite = {}
    for d, rs in refs.items():
        inc = sorted({oa2doc[r] for r in rs if r in oa2doc})
        if len(inc) < 2 or len(inc) > 200:
            continue
        for i in range(len(inc)):
            for j in range(i + 1, len(inc)):
                k = (inc[i], inc[j])
                cocite[k] = cocite.get(k, 0) + 1
    cocite = {k: v for k, v in cocite.items() if v >= min_cocite}

    # 4) weighted union graph
    edges = {}
    for e in direct:
        k = tuple(sorted((e["src_doc"], e["dst_doc"])))
        edges[k] = edges.get(k, 0.0) + w_direct
    for k, v in coupling.items():
        edges[k] = edges.get(k, 0.0) + w_couple * v
    for k, v in cocite.items():
        edges[k] = edges.get(k, 0.0) + w_cocite * v

    # Semantic kNN edges from semantic.py docs. Bibliographic signal alone
    # cannot see a paper with no DOI (those never enter the OpenAlex cache at
    # all) nor a pair that is plainly about the same thing but shares no
    # references. Weight is deliberately below w_direct: embedding similarity
    # is evidence of topic, not of intellectual lineage.
    n_sem = 0
    sem_path = os.path.join(gdir(), "doc_semantic.ndjson")
    if w_semantic > 0 and os.path.isfile(sem_path):
        for e in _load_ndjson(sem_path):
            a, b = e.get("a"), e.get("b")
            if not a or not b or a == b:
                continue
            # a near-1.0 pair is the same work twice; linking it says nothing
            if e.get("sim", 0) >= dup_sim:
                continue
            k = (a, b) if a < b else (b, a)
            if k not in edges:
                n_sem += 1
            edges[k] = edges.get(k, 0.0) + w_semantic * float(e.get("sim", 0))
    with open(os.path.join(gdir(), "paper_edges.ndjson"), "w") as f:
        for (a, b), w in edges.items():
            f.write(json.dumps({"a": a, "b": b, "weight": round(w, 3)}) + "\n")

    # 5) Leiden over the paper graph -> paper-level communities
    comm, n_comm, largest = {}, 0, 0
    if edges:
        import igraph as ig
        import leidenalg as la
        ids = sorted({x for k in edges for x in k})
        idx = {d: i for i, d in enumerate(ids)}
        g = ig.Graph(n=len(ids),
                     edges=[(idx[a], idx[b]) for a, b in edges.keys()])
        g.es["weight"] = list(edges.values())
        # Resolution, not edge-pruning, is the right granularity knob on a
        # citation graph. Coupling makes this graph genuinely dense (a 4.2k-paper
        # corpus yields ~100k coupling pairs), and at resolution 1.0 Leiden
        # returns ~15 giant blobs. Raising resolution keeps all the evidence and
        # still resolves real subfields; raising thresholds instead just throws
        # citation data away and barely moves the community count.
        res = resolution
        if res is None:
            res = float(os.environ.get("RAG_CITE_RESOLUTION", "8.0"))
        part = la.find_partition(g, la.RBConfigurationVertexPartition,
                                 weights="weight", resolution_parameter=res,
                                 seed=42)
        comm = {ids[v]: part.membership[v] for v in range(len(ids))}
        sizes = {}
        for c in comm.values():
            sizes[c] = sizes.get(c, 0) + 1
        n_comm, largest = len(sizes), max(sizes.values())
    json.dump(comm, open(os.path.join(gdir(), "paper_communities.json"), "w"))

    return {"papers_with_openalex": len(cache),
            "direct_citation_edges": len(direct),
            "semantic_edges_added": n_sem,
            "coupling_pairs": len(coupling),
            "cocitation_pairs": len(cocite),
            "paper_graph_nodes": len({x for k in edges for x in k}),
            "paper_graph_edges": len(edges),
            "paper_communities": n_comm, "largest_community": largest,
            "resolution": resolution if resolution is not None
            else float(os.environ.get("RAG_CITE_RESOLUTION", "8.0"))}


# ---- query -------------------------------------------------------------------

def neighbors(doc_id, k=10):
    """Most bibliographically-related papers to doc_id, with the reason why."""
    con = store.connect()
    titles = {r["doc_id"]: r["title"]
              for r in con.execute("SELECT doc_id, title FROM documents")}
    score = {}
    for e in _load_ndjson(os.path.join(gdir(), "paper_edges.ndjson")):
        if e["a"] == doc_id:
            score[e["b"]] = e["weight"]
        elif e["b"] == doc_id:
            score[e["a"]] = e["weight"]
    cites, cited_by = set(), set()
    for e in _load_ndjson(os.path.join(gdir(), "citations.ndjson")):
        if e["src_doc"] == doc_id:
            cites.add(e["dst_doc"])
        elif e["dst_doc"] == doc_id:
            cited_by.add(e["src_doc"])
    out = []
    for d, w in sorted(score.items(), key=lambda kv: -kv[1])[:k]:
        why = []
        if d in cites:
            why.append("cited by this paper")
        if d in cited_by:
            why.append("cites this paper")
        if not why:
            why.append("shared references / co-cited")
        out.append({"doc_id": d, "score": round(w, 3),
                    "title": (titles.get(d) or "")[:120], "why": "; ".join(why)})
    return {"doc_id": doc_id, "title": (titles.get(doc_id) or "")[:120],
            "neighbors": out}


def status():
    cache = _load_cache()
    n_cached = len([f for f in os.listdir(cache_dir()) if f.endswith(".json")])
    comm_p = os.path.join(gdir(), "paper_communities.json")
    comm = json.load(open(comm_p)) if os.path.isfile(comm_p) else {}
    sizes = {}
    for c in comm.values():
        sizes[c] = sizes.get(c, 0) + 1
    return {"cached_records": n_cached, "resolvable_in_openalex": len(cache),
            "direct_citation_edges": len(_load_ndjson(
                os.path.join(gdir(), "citations.ndjson"))),
            "paper_graph_edges": len(_load_ndjson(
                os.path.join(gdir(), "paper_edges.ndjson"))),
            "papers_clustered": len(comm), "paper_communities": len(sizes)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["fetch", "build", "status", "neighbors"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--sleep", type=float, default=0.12)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--min-coupling", type=int, default=2)
    ap.add_argument("--min-cocite", type=int, default=2)
    ap.add_argument("--resolution", type=float, default=None,
                    help="Leiden resolution; higher = more, smaller communities")
    ap.add_argument("-k", type=int, default=10)
    a = ap.parse_args()
    if a.command == "fetch":
        print(json.dumps(fetch(limit=a.limit, sleep=a.sleep, refresh=a.refresh), indent=2))
    elif a.command == "build":
        print(json.dumps(build(min_coupling=a.min_coupling,
                               min_cocite=a.min_cocite,
                               resolution=a.resolution), indent=2))
    elif a.command == "neighbors":
        print(json.dumps(neighbors(a.arg, k=a.k), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
