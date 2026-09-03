#!/usr/bin/env python3
"""
graphrag.py — a local, seedable GraphRAG layer over the scholar-rag corpus.

Pipeline (all local via ollama; $0, private):
  seed       import scholar-knowledge's existing entities/edges (872 papers,
             160 concepts, 861 edges) so extraction doesn't start from zero
  extract    gpt-oss:20b reads each paper's key sections -> entities + relations
             (1-few calls/paper, JSON-structured, resumable per doc)
  build      resolve/dedup entities -> igraph -> Leiden communities
  summarize  gpt-oss:120b writes a summary per community (global-search corpus)
  run        seed -> extract -> build -> summarize

Query:
  local <q>      entity-grounded: vector passages + the entities/neighbors of
                 the hit papers
  global <q>     map-reduce over community summaries -> synthesized answer
  neighbors <id> papers sharing entities / community / citations with <id>

Graph store (NDJSON + JSON under $SCHOLAR_RAG_DIR/graph/):
  entities.ndjson   {ent_id, name, type, aliases, doc_ids[], count, source}
  relations.ndjson  {src, rel, dst, doc_ids[], weight}
  communities.json  {ent_id: community_id}
  community_summaries.json  {community_id: {title, summary, entities[], size}}
"""
import os, re, sys, json, time, argparse, hashlib, glob
import store

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
# Model selection. Prefer a fast MoE/instruct model. gpt-oss:20b is the ideal
# extractor (fast MoE) and is preferred once its blob is current — a stale
# (long-cached) gpt-oss blob fails to load with an MoE "size overflow"; a fresh
# `ollama pull gpt-oss:20b` fixes it. It requires /api/chat (harmony template),
# which ollama_json/ollama_text use. gpt-oss:120b is only auto-picked if present
# AND freshly pulled. Fallbacks: qwen instruct, then a reasoning model.
_EXTRACT_PREF = ["gpt-oss:20b", "qwen2.5:7b-instruct", "qwen2.5:14b-instruct",
                 "qwen2.5:7b", "llama3.1:8b", "mistral-nemo", "deepseek-r1:32b"]
_SUMMARY_PREF = ["gpt-oss:20b", "qwen2.5:14b-instruct", "qwen2.5:7b-instruct",
                 "deepseek-r1:32b"]


def _available_models():
    try:
        import requests
        r = requests.get(OLLAMA + "/api/tags", timeout=5)
        return [m["name"] for m in r.json().get("models", [])] if r.ok else []
    except Exception:
        return []


def _pick_model(preferred, env_key):
    v = os.environ.get(env_key)
    if v:
        return v
    avail = _available_models()
    for p in preferred:
        for a in avail:
            if a == p or a.split(":")[0] == p.split(":")[0]:
                return a
    return avail[0] if avail else preferred[-1]


EXTRACT_MODEL = _pick_model(_EXTRACT_PREF, "RAG_GRAPH_MODEL")
SUMMARY_MODEL = _pick_model(_SUMMARY_PREF, "RAG_SUMMARY_MODEL")
MAX_EXTRACT_CHARS = int(os.environ.get("RAG_GRAPH_MAX_CHARS", "12000"))  # ~3k tokens
# Windows sampled per paper. 4 covers the priority ladder in _WINDOW_PRIORITY:
# head (abstract/intro) -> theory/literature/related work -> discussion/
# conclusion -> results/methods. At 1 the extractor only ever saw front matter.
GRAPH_WINDOWS = int(os.environ.get("RAG_GRAPH_WINDOWS", "4"))
GRAPH_NUM_PREDICT = int(os.environ.get("RAG_GRAPH_NUM_PREDICT", "1200"))  # cap output
# `author` is deliberately NOT an entity type: corpus.sqlite already holds
# authoritative authors_json per document, and LLM-scraped author names (mostly
# harvested out of reference lists) were ~14% of the graph and almost all
# singletons. Real author linkage belongs on doc edges, not extracted nodes.
ENT_TYPES = ["theory", "concept", "method", "dataset", "construct",
             "finding", "population"]

# Entities whose name normalizes to something this short/generic are dropped —
# they attach to everything and discriminate nothing.
_MIN_ENT_CHARS = 3


def gdir():
    d = os.path.join(store.rag_dir(), "graph")
    os.makedirs(d, exist_ok=True)
    return d


def norm_name(name):
    """Canonical surface form used for entity identity.

    Case-, punctuation- and plural-insensitive so that "Large Language Models",
    "large language model" and "large-language models" collapse to one node.
    Unicode dashes/quotes are folded to ASCII first: COVID-19 vs COVID‑19 (U+2011)
    used to hash differently and split the single largest hub in the graph.
    """
    n = (name or "").strip().lower()
    n = n.replace("‐", "-").replace("‑", "-").replace("‒", "-")
    n = n.replace("–", "-").replace("—", "-").replace("’", "'")
    n = re.sub(r"[^a-z0-9]+", " ", n)          # punctuation -> space
    n = re.sub(r"\s+", " ", n).strip()
    # naive singularization, applied per word-final token only
    if n.endswith("ies") and len(n) > 4:
        n = n[:-3] + "y"
    elif n.endswith("sses") or n.endswith("ches") or n.endswith("shes"):
        n = n[:-2]
    elif n.endswith("s") and not n.endswith("ss") and not n.endswith("us") \
            and not n.endswith("is") and len(n) > 3:
        n = n[:-1]
    return n


def ent_id(name, typ=None):
    """Identity = normalized NAME only; `typ` is accepted but ignored.

    Type used to be part of the key, which meant the SAME concept fragmented
    into separate nodes whenever the LLM typed it differently across papers
    ("machine learning" was method(64) + concept(33) + population(1)). The type
    labels are not reliably distinguishable by the model — concept vs construct
    especially — so they are now a majority-vote attribute, not identity.
    """
    return hashlib.sha256(norm_name(name).encode("utf-8", "ignore")).hexdigest()[:16]


# ---- ollama ------------------------------------------------------------------

# Reasoning effort for gpt-oss/other thinking models. "low" is ~4x faster than
# default with no measured loss on this extraction task. Disabled automatically
# for models that don't accept `think` (e.g. qwen2.5-instruct).
THINK = os.environ.get("RAG_GRAPH_THINK", "low")
_think_ok = True


def _chat(model, prompt, temperature, want_json, timeout):
    global _think_ok
    import requests
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "stream": False, "options": {"temperature": temperature}}
    if want_json:
        body["format"] = "json"
        body["options"]["num_predict"] = GRAPH_NUM_PREDICT
    if THINK and _think_ok:
        body["think"] = THINK
    r = requests.post(OLLAMA + "/api/chat", json=body, timeout=timeout)
    if not r.ok:
        # a non-thinking model rejects `think` -> disable for the session, signal retry
        if _think_ok and "think" in (r.text or "").lower():
            _think_ok = False
            return None, True
        return "", False
    return r.json().get("message", {}).get("content", ""), False


def ollama_json(prompt, model=EXTRACT_MODEL, retries=2, temperature=0):
    # /api/chat applies the model's chat template (required for gpt-oss's harmony
    # format; correct for all instruct/chat models). format=json constrains output.
    for attempt in range(retries + 1):
        try:
            txt, retry = _chat(model, prompt, temperature, True, 600)
            if retry:
                continue
            return json.loads(txt) if (txt and txt.strip()) else {}
        except Exception as e:
            if attempt == retries:
                sys.stderr.write("  [ollama] %s\n" % e)
                return {}
            time.sleep(1.5)
    return {}


def ollama_text(prompt, model=SUMMARY_MODEL, temperature=0.2):
    try:
        for _ in range(2):
            txt, retry = _chat(model, prompt, temperature, False, 900)
            if retry:
                continue
            return txt or ""
    except Exception as e:
        sys.stderr.write("  [ollama] %s\n" % e)
    return ""


# ---- graph store I/O ---------------------------------------------------------

def _load_ndjson(path):
    out = []
    if os.path.isfile(path):
        for line in open(path):
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except Exception: pass
    return out


def load_entities():
    return {e["ent_id"]: e for e in _load_ndjson(os.path.join(gdir(), "entities.ndjson"))}


def load_relations():
    return _load_ndjson(os.path.join(gdir(), "relations.ndjson"))


def save_entities(ents):
    with open(os.path.join(gdir(), "entities.ndjson"), "w") as f:
        for e in ents.values():
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def save_relations(rels):
    with open(os.path.join(gdir(), "relations.ndjson"), "w") as f:
        for r in rels:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _merge_entity(ents, name, typ, doc_id, source="extract"):
    if not name or not name.strip():
        return None
    if len(norm_name(name)) < _MIN_ENT_CHARS:
        return None
    eid = ent_id(name)
    e = ents.get(eid)
    if not e:
        e = {"ent_id": eid, "name": name.strip(), "type": typ, "aliases": [],
             "type_counts": {}, "doc_ids": [], "count": 0, "source": source}
        ents[eid] = e
    # Type is a majority vote across every mention, not part of identity.
    tc = e.setdefault("type_counts", {})
    if typ:
        tc[typ] = tc.get(typ, 0) + 1
        e["type"] = max(tc.items(), key=lambda kv: (kv[1], kv[0]))[0]
    # Keep every distinct surface form we saw; the canonical `name` stays the
    # first one, the rest are searchable aliases.
    surface = " ".join((name or "").split())
    if surface and surface != e["name"] and surface not in e["aliases"]:
        e["aliases"].append(surface)
    if doc_id and doc_id not in e["doc_ids"]:
        e["doc_ids"].append(doc_id)
    e["count"] += 1
    return eid


# ---- seed from scholar-knowledge --------------------------------------------

def _kg_dir():
    return os.environ.get("SCHOLAR_KNOWLEDGE_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude", "scholar-knowledge")


def _norm_title(t):
    return re.sub(r"[^a-z0-9 ]", " ", (t or "").lower())


def _kg_paperid_map(con, kg):
    """Map scholar-knowledge paper ids -> our doc_ids via DOI, then title."""
    doi2doc, title2doc = {}, {}
    for row in con.execute("SELECT doc_id, doi, title FROM documents"):
        if row["doi"]:
            doi2doc[row["doi"].strip().lower()] = row["doc_id"]
        if row["title"]:
            title2doc[re.sub(r"\s+", " ", _norm_title(row["title"])).strip()] = row["doc_id"]
    kgid2doc = {}
    for p in _load_ndjson(os.path.join(kg, "papers.ndjson")):
        did = None
        if p.get("doi"):
            did = doi2doc.get(p["doi"].strip().lower())
        if not did and p.get("title"):
            did = title2doc.get(re.sub(r"\s+", " ", _norm_title(p["title"])).strip())
        if did:
            kgid2doc[p["id"]] = did
    return kgid2doc


def seed():
    """Import scholar-knowledge concepts (as entities) and paper->paper edges
    (mapped to our doc_ids, as citation-style doc edges for neighbors())."""
    kg = _kg_dir()
    ents = load_entities()
    n_c = 0
    concepts = _load_ndjson(os.path.join(kg, "concepts.ndjson"))
    for c in concepts:
        typ = c.get("category", "concept")
        if typ not in ENT_TYPES:
            typ = "concept"
        eid = _merge_entity(ents, c.get("name", ""), typ, None, source="kg")
        if eid:
            e = ents[eid]
            for a in c.get("aliases", []):
                if a and a not in e["aliases"]:
                    e["aliases"].append(a)
            n_c += 1
    save_entities(ents)
    # paper->paper edges mapped to our doc_ids -> doc_edges.ndjson
    con = store.connect()
    kgid2doc = _kg_paperid_map(con, kg)
    doc_edges, n_e, n_map = [], 0, 0
    for ed in _load_ndjson(os.path.join(kg, "edges.ndjson")):
        s, d = ed.get("source_id"), ed.get("target_id")
        rel = ed.get("relationship") or "related"
        if not s or not d:
            continue
        n_e += 1
        sd, dd = kgid2doc.get(s), kgid2doc.get(d)
        if sd and dd and sd != dd:
            doc_edges.append({"src_doc": sd, "dst_doc": dd, "rel": rel,
                              "note": ed.get("note", ""), "source": "kg"})
            n_map += 1
    with open(os.path.join(gdir(), "doc_edges.ndjson"), "w") as f:
        for e in doc_edges:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return {"seeded_concepts": n_c, "kg_edges_seen": n_e,
            "doc_edges_mapped": n_map, "kg_papers_matched": len(kgid2doc),
            "entities_total": len(ents)}


# ---- extraction --------------------------------------------------------------

_EXTRACT_PROMPT = """You are building a knowledge graph of social-science research.
From the paper excerpt below, extract the key scholarly entities and the
relationships between them. Return STRICT JSON with this shape:
{"entities":[{"name":"...","type":"one of %s"}],
 "relations":[{"subject":"...","relation":"short verb phrase","object":"..."}]}
Rules: entity names are canonical noun phrases (e.g. "spatial assimilation theory",
"difference-in-differences", "Common Core of Data"); 5-20 entities; only relations
whose subject and object both appear in entities; no prose outside the JSON.

PAPER: %s (%s)
EXCERPT:
%s
""" % (ENT_TYPES, "%s", "%s", "%s")


# What each window should try to capture, in descending value for a knowledge
# graph. Theory/literature carries the named frameworks; discussion/conclusion
# carries the contribution and implications — both were previously unreachable
# because sampling never got past the first ~24k chars of a ~74k-char paper.
_WINDOW_PRIORITY = [
    ("abstract", "introduction", "front"),   # framing, core constructs
    ("literature",),                         # theory / related work / frameworks
    ("discussion", "conclusion"),            # contribution, implications
    ("results", "methods"),                  # findings, designs
]


def _section_windows(full, n_windows):
    """Windows chosen by detected section, best-effort. [] if detection is weak.

    `references` is always excluded: extracting from a bibliography yields
    author and journal names, which are noise in a concept graph.
    """
    try:
        import chunk_embed as CE
        spans = CE.detect_sections(full)
    except Exception:
        return []
    by_label = {}
    for lab, s, e in spans:
        if lab == "references" or e - s < 300:
            continue
        # a label can appear once; keep the longest span for it
        if lab not in by_label or (e - s) > (by_label[lab][1] - by_label[lab][0]):
            by_label[lab] = (s, e)
    # Detection is only trustworthy when it found real body structure, not just
    # a lone "front" span — otherwise fall back to positional sampling.
    if not ({"literature", "discussion", "conclusion", "results", "methods"}
            & set(by_label)):
        return []
    wins, used = [], []
    for gi, group in enumerate(_WINDOW_PRIORITY):
        if len(wins) >= n_windows:
            break
        for lab in group:
            if lab in by_label:
                s, e = by_label[lab]
                if any(s < ue and e > us for us, ue in used):
                    continue
                seg = full[s:min(e, s + MAX_EXTRACT_CHARS)]
                if len(seg) >= 200:
                    # group 0 is the head under either strategy, so it shares
                    # the "head" signature and is never re-extracted
                    wins.append(("head" if gi == 0 else "sec:%s" % lab, seg))
                    used.append((s, e))
                break
    return wins


def _extraction_windows(doc, n_windows=None):
    """Text windows spanning the WHOLE document, not just its prefix.

    The original implementation took `full[:12000]`, but the median paper here
    is ~74k chars, so ~84% of every paper was never seen by the extractor —
    which is why `finding` was the rarest entity type.

    Two strategies, in order:
      1. Section-aware. Uses chunk_embed.detect_sections to target abstract/intro,
         then theory/literature, then discussion/conclusion, then results/methods.
         Only ~10-13% of this corpus has reliably detected body sections, so it
         is best-effort.
      2. Positional fallback. Head window, then windows spread across the body
         with the LAST one anchored at the tail. Anchoring the tail matters: an
         "evenly spaced" scheme with a single extra window puts it right after
         the head (~16-32% of the paper), so discussion and conclusion — which
         sit at ~70-95% — were still never sampled.
    """
    if n_windows is None:
        n_windows = GRAPH_WINDOWS
    tp = doc.get("text_path")
    if not tp or not os.path.isfile(tp):
        return []
    pages = json.load(open(tp)).get("pages", [])
    full = "\n".join(p["text"] for p in pages)
    W = MAX_EXTRACT_CHARS
    if len(full) <= W:
        return [("head", full)]

    sec = _section_windows(full, n_windows)
    if len(sec) >= min(2, n_windows):
        return sec[:n_windows]

    # Positional fallback. Trim a trailing references estimate so the tail
    # window lands on discussion/conclusion rather than the bibliography.
    body_end = len(full)
    m = None
    for rx in (r"\nReferences\b", r"\nREFERENCES\b", r"\nBibliography\b",
               r"\nWorks Cited\b"):
        for mm in re.finditer(rx, full):
            if mm.start() > 0.40 * len(full):
                m = mm if m is None else m
                break
        if m:
            break
    if m:
        body_end = m.start()
    wins = [("head", full[:W])]
    extra = max(0, n_windows - 1)
    lo, hi = W, max(W, body_end - W)
    if extra and hi > lo:
        if extra == 1:
            starts = [hi]                       # tail: discussion/conclusion
        else:
            step = (hi - lo) / float(extra - 1)
            starts = [int(lo + i * step) for i in range(extra)]
        for s in starts:
            seg = full[s:s + W]
            if len(seg) >= 200:
                wins.append(("pos:%.2f" % (s / float(len(full))), seg))
    elif extra and body_end > W:
        seg = full[W:body_end][:W]
        if len(seg) >= 200:
            wins.append(("pos:%.2f" % (W / float(len(full))), seg))
    return wins


def _extracted_docs_path():
    return os.path.join(gdir(), "extracted_docs.json")


def _load_extract_progress():
    """doc_id -> set of window SIGNATURES already extracted.

    Signatures ("head", "sec:discussion", "pos:0.81") rather than a count,
    because the window *strategy* evolves: a plain counter silently means the
    wrong thing the moment window 1 stops being "the early body" and starts
    being "the discussion". With signatures, changing strategy re-extracts
    exactly the newly-reachable regions and nothing else.

    Back-compat, both older formats:
      list            -> one head window per doc
      {doc: <int>}    -> head window done (later indices under the OLD
                         positional scheme have no counterpart here; whatever
                         they captured is already banked in entities.ndjson)
    """
    p = _extracted_docs_path()
    if not os.path.isfile(p):
        return {}
    try:
        raw = json.load(open(p))
    except Exception:
        return {}
    if isinstance(raw, list):
        return {d: {"head"} for d in raw}
    out = {}
    for k, v in (raw or {}).items():
        if isinstance(v, (int, float)):
            out[k] = {"head"} if v else set()
        elif isinstance(v, list):
            out[k] = set(v)
    return out


def _save_extract_progress(prog):
    json.dump({k: sorted(v) for k, v in prog.items()},
              open(_extracted_docs_path(), "w"))


def extract(limit=None, model=EXTRACT_MODEL, force=False, n_windows=None):
    """Extract entities/relations per document, one LLM call per text window.

    Progress is tracked per (doc, window) so raising RAG_GRAPH_WINDOWS only
    costs the *additional* windows on already-processed docs — it does not
    redo work already banked.
    """
    if n_windows is None:
        n_windows = GRAPH_WINDOWS
    con = store.connect()
    prog = {} if force else _load_extract_progress()
    ents = load_entities()
    rels = load_relations()
    docs = [d for d in store.iter_documents(con, with_pdf=True)
            if d["status"] in ("embedded", "extracted", "chunked")]
    # Plan first, so the reported call count reflects signature-level work
    # remaining rather than a doc count.
    plan = {}
    for d in docs:
        done = prog.get(d["doc_id"], set())
        wins = _extraction_windows(d, n_windows)
        todo = [(sig, txt) for sig, txt in wins
                if sig not in done and len(txt) >= 200]
        if todo:
            plan[d["doc_id"]] = (d, todo)
    todo_calls = sum(len(v[1]) for v in plan.values())
    sys.stderr.write("[graph.extract] %d docs needing work, ~%d LLM calls "
                     "(windows=%d, model=%s)\n"
                     % (len(plan), todo_calls, n_windows, model))
    n = 0
    for doc_id, (d, todo) in plan.items():
        if limit and n >= limit:
            break
        title = (d["title"] or "")[:200]
        year = d["year"] or ""
        for sig, text in todo:
            res = ollama_json(_EXTRACT_PROMPT % (title, year, text), model=model)
            if not isinstance(res, dict):    # LLM may return a bare list/str
                res = {}
            name2eid = {}
            for ent in (res.get("entities") or [])[:40]:
                if not isinstance(ent, dict):  # tolerate malformed items
                    continue
                nm, ty = ent.get("name"), (ent.get("type") or "concept")
                if ty not in ENT_TYPES:
                    ty = "concept"
                eid = _merge_entity(ents, nm, ty, d["doc_id"])
                if eid:
                    name2eid[norm_name(nm)] = eid
            for rel in (res.get("relations") or [])[:60]:
                if not isinstance(rel, dict):  # some models emit relations as strings
                    continue
                se = name2eid.get(norm_name(rel.get("subject")))
                oe = name2eid.get(norm_name(rel.get("object")))
                if se and oe and se != oe:
                    rels.append({"src": se, "rel": (rel.get("relation") or "related")[:40],
                                 "dst": oe, "doc_ids": [d["doc_id"]], "weight": 1,
                                 "source": "extract"})
            prog.setdefault(doc_id, set()).add(sig)
        n += 1
        if n % 25 == 0:
            save_entities(ents); save_relations(rels)
            _save_extract_progress(prog)
            sys.stderr.write("  ...%d/%d docs, %d entities\n"
                             % (n, len(plan), len(ents)))
    save_entities(ents); save_relations(rels)
    _save_extract_progress(prog)
    return {"extracted_docs": n, "entities_total": len(ents),
            "relations_total": len(rels), "windows": n_windows}


# ---- build communities -------------------------------------------------------

def build(min_docs=None):
    """Cluster the entity graph into Leiden communities.

    min_docs (default 2, or $RAG_GRAPH_MIN_DOCS) prunes entities mentioned in
    fewer than that many papers. A single-paper entity cannot link papers — it
    only forms an isolated within-paper clique that inflates the community
    count without adding cross-paper structure. Seeded (scholar-knowledge)
    entities are exempt: they carry no doc_ids but are curated vocabulary.
    """
    if min_docs is None:
        min_docs = int(os.environ.get("RAG_GRAPH_MIN_DOCS", "2"))
    ents = load_entities()
    rels = load_relations()
    if not ents:
        return {"error": "no entities; run seed/extract first"}
    import igraph as ig
    import leidenalg as la
    keep = {eid for eid, e in ents.items()
            if e.get("source") == "kg" or len(e.get("doc_ids", [])) >= min_docs}
    n_pruned = len(ents) - len(keep)
    rels = [r for r in rels if r["src"] in keep and r["dst"] in keep]

    # Co-occurrence edges. LLM relations alone are too sparse once single-paper
    # entities are pruned (they are mostly paper-local, so pruning one endpoint
    # kills the edge — ~80% of them here). Two entities appearing together in
    # several papers is strong, LLM-free evidence of a real association, and it
    # is what gives community detection enough structure to find fine-grained
    # themes rather than a handful of giant blobs.
    # 2 measured best on this corpus: 1 collapses the graph into 40 blobs
    # (largest 1210), 3 starves it (median community size 2).
    cooc_min = int(os.environ.get("RAG_GRAPH_COOC_MIN", "2"))
    cooc = {}
    if cooc_min > 0:
        bydoc = {}
        for eid in keep:
            for d in ents[eid].get("doc_ids", []):
                bydoc.setdefault(d, []).append(eid)
        for d, es in bydoc.items():
            if len(es) < 2 or len(es) > 60:   # skip degenerate/huge docs
                continue
            es = sorted(es)
            for i in range(len(es)):
                for j in range(i + 1, len(es)):
                    k = (es[i], es[j])
                    cooc[k] = cooc.get(k, 0) + 1
        cooc = {k: v for k, v in cooc.items() if v >= cooc_min}

    # Bibliographic coupling of the ENTITY graph: if paper P cites paper Q, the
    # concepts in P and the concepts in Q belong nearer each other. This is the
    # only route by which citation structure reaches entity clustering — without
    # it, communities are built from shared wording alone. Built from
    # citations.ndjson (OpenAlex) plus the scholar-knowledge doc_edges seed.
    cite_w = float(os.environ.get("RAG_GRAPH_CITE_WEIGHT", "1.0"))
    cite_pairs = {}
    if cite_w > 0:
        doc2ents = {}
        for eid in keep:
            for d in ents[eid].get("doc_ids", []):
                doc2ents.setdefault(d, []).append(eid)
        cite_edges = []
        for fn, sk, dk in (("citations.ndjson", "src_doc", "dst_doc"),
                           ("doc_edges.ndjson", "src_doc", "dst_doc")):
            for e in _load_ndjson(os.path.join(gdir(), fn)):
                if e.get(sk) and e.get(dk):
                    cite_edges.append((e[sk], e[dk]))
        for sd, dd in cite_edges:
            se, de = doc2ents.get(sd), doc2ents.get(dd)
            if not se or not de or len(se) * len(de) > 900:
                continue
            # Normalize: one citation contributes ~1.0 of total weight spread
            # over the entity pairs it induces, so a citation between two
            # entity-rich papers cannot outvote every co-occurrence edge. Without
            # this, 703 seed citations already generated 12k entity edges — at
            # OpenAlex scale (~30k citations) the citation signal would swamp
            # the graph and collapse it into a few blobs.
            share = 1.0 / (len(se) * len(de)) ** 0.5
            for a in se:
                for b in de:
                    if a != b:
                        k = (a, b) if a < b else (b, a)
                        w, c = cite_pairs.get(k, (0.0, 0))
                        cite_pairs[k] = (w + share, c + 1)
        # Support threshold. Projection is quadratic in entities-per-paper, so
        # a single citation between two ~15-entity papers mints ~225 pairs:
        # 17k citations produced 1.5M distinct pairs, dwarfing the 94k
        # co-occurrence edges and collapsing the graph to 34 blobs. Requiring
        # several independent citations behind a pair keeps the signal and
        # discards the combinatorial noise.
        min_sup = int(os.environ.get("RAG_GRAPH_CITE_MIN_SUPPORT", "8"))
        cite_pairs = {k: w for k, (w, c) in cite_pairs.items() if c >= min_sup}

    # Near-synonym edges from semantic.py entities. Normalization collapses
    # surface variants of the SAME string; embeddings collapse different strings
    # that mean the same thing ("residential segregation" vs "neighborhood
    # segregation"). Without this the two sit in the graph as unrelated nodes.
    # Weighted high — a synonym pair is stronger evidence of relatedness than a
    # single co-occurrence — but kept as an edge rather than a hard merge, so a
    # bad pairing distorts clustering instead of destroying a node.
    sem_w = float(os.environ.get("RAG_GRAPH_SEM_WEIGHT", "3.0"))
    sem_pairs = {}
    sem_path = os.path.join(gdir(), "entity_semantic.ndjson")
    if sem_w > 0 and os.path.isfile(sem_path):
        for e in _load_ndjson(sem_path):
            a, b = e.get("a"), e.get("b")
            if a and b and a != b and a in keep and b in keep:
                k = (a, b) if a < b else (b, a)
                sem_pairs[k] = max(sem_pairs.get(k, 0.0), float(e.get("sim", 0)))

    ids = sorted({e for r in rels for e in (r["src"], r["dst"])}
                 | {e for k in cooc for e in k}
                 | {e for k in cite_pairs for e in k}
                 | {e for k in sem_pairs for e in k})
    idx = {e: i for i, e in enumerate(ids)}
    edges = {}
    for r in rels:
        if r["src"] in idx and r["dst"] in idx and r["src"] != r["dst"]:
            key = tuple(sorted((idx[r["src"]], idx[r["dst"]])))
            edges[key] = edges.get(key, 0) + r.get("weight", 1)
    n_rel_edges = len(edges)
    for (a, b), w in cooc.items():
        if a in idx and b in idx and a != b:
            key = tuple(sorted((idx[a], idx[b])))
            edges[key] = edges.get(key, 0) + w
    n_cite_edges = 0
    for (a, b), w in cite_pairs.items():
        if a in idx and b in idx and a != b:
            key = tuple(sorted((idx[a], idx[b])))
            if key not in edges:
                n_cite_edges += 1
            edges[key] = edges.get(key, 0) + cite_w * w
    n_sem_edges = 0
    for (a, b), s in sem_pairs.items():
        if a in idx and b in idx and a != b:
            key = tuple(sorted((idx[a], idx[b])))
            if key not in edges:
                n_sem_edges += 1
            edges[key] = edges.get(key, 0) + sem_w * s
    if not edges:
        return {"error": "no usable edges among entities"}
    g = ig.Graph(n=len(ids), edges=list(edges.keys()))
    g.es["weight"] = [edges[k] for k in edges.keys()]
    # Resolution is the granularity knob. The graph is dense by construction —
    # co-occurrence alone yields ~94k edges, and broad keyword nodes (OpenAlex
    # topics like "Political science" tag hundreds of papers) act as hubs that
    # bridge unrelated areas. At the Leiden default of 1.0 that produces a few
    # giant blobs; raising resolution separates real themes without discarding
    # any evidence.
    res = float(os.environ.get("RAG_GRAPH_RESOLUTION", "6.0"))
    part = la.find_partition(g, la.RBConfigurationVertexPartition,
                             weights="weight", resolution_parameter=res,
                             seed=42)
    comm = {ids[v]: part.membership[v] for v in range(len(ids))}
    json.dump(comm, open(os.path.join(gdir(), "communities.json"), "w"))
    sizes = {}
    for c in comm.values():
        sizes[c] = sizes.get(c, 0) + 1
    return {"entities_in_graph": len(ids), "edges": len(edges),
            "edges_from_relations": n_rel_edges, "edges_from_cooccurrence": len(cooc),
            "edges_from_citations": n_cite_edges,
            "edges_from_synonyms": n_sem_edges,
            "communities": len(sizes),
            "largest": max(sizes.values()) if sizes else 0,
            "pruned_below_min_docs": n_pruned, "min_docs": min_docs,
            "resolution": res}


# ---- community summaries -----------------------------------------------------

_SUMMARY_PROMPT = """You are summarizing a thematic cluster of a social-science
literature knowledge graph. Given the entities below (concepts, theories,
methods, findings that co-occur across papers), write: (1) a 4-8 word TITLE for
the theme, then (2) a 3-5 sentence SUMMARY of what this cluster is about and the
kind of claims it supports. Return STRICT JSON: {"title":"...","summary":"..."}.

ENTITIES:
%s
"""


def summarize(model=SUMMARY_MODEL, max_communities=None, min_size=3):
    comm_path = os.path.join(gdir(), "communities.json")
    if not os.path.isfile(comm_path):
        return {"error": "run build first"}
    comm = json.load(open(comm_path))
    ents = load_entities()
    groups = {}
    for eid, c in comm.items():
        groups.setdefault(c, []).append(eid)
    out = {}
    items = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    n = 0
    for c, eids in items:
        if len(eids) < min_size:
            continue
        if max_communities and n >= max_communities:
            break
        names = [ents[e]["name"] for e in eids if e in ents][:40]
        res = ollama_json(_SUMMARY_PROMPT % "\n".join("- " + x for x in names),
                          model=model)
        out[str(c)] = {"title": res.get("title", "cluster %s" % c),
                       "summary": res.get("summary", ""),
                       "entities": names[:25], "size": len(eids)}
        n += 1
        if n % 10 == 0:
            json.dump(out, open(os.path.join(gdir(), "community_summaries.json"), "w"),
                      ensure_ascii=False, indent=1)
    json.dump(out, open(os.path.join(gdir(), "community_summaries.json"), "w"),
              ensure_ascii=False, indent=1)
    return {"communities_summarized": len(out)}


# ---- query: neighbors / local / global --------------------------------------

def neighbors(doc_id, k=8):
    """Papers related to doc_id: shared graph entities + seeded citation edges."""
    ents = load_entities()
    score, why = {}, {}
    for e in ents.values():
        if doc_id in e.get("doc_ids", []):
            for other in e.get("doc_ids", []):
                if other != doc_id:
                    score[other] = score.get(other, 0) + 1
                    why.setdefault(other, set()).add("shared:" + e["name"])
    # citation edges from the scholar-knowledge seed (both directions)
    for ed in _load_ndjson(os.path.join(gdir(), "doc_edges.ndjson")):
        pair = None
        if ed.get("src_doc") == doc_id:
            pair = ed.get("dst_doc")
        elif ed.get("dst_doc") == doc_id:
            pair = ed.get("src_doc")
        if pair:
            score[pair] = score.get(pair, 0) + 2          # citations weigh more
            why.setdefault(pair, set()).add("cite:" + (ed.get("rel") or "related"))
    con = store.connect()
    out = []
    for other, sc in sorted(score.items(), key=lambda kv: -kv[1])[:k]:
        row = con.execute("SELECT title, year, doi FROM documents WHERE doc_id=?",
                          (other,)).fetchone()
        if row:
            out.append({"doc_id": other, "title": row["title"],
                        "year": row["year"], "doi": row["doi"],
                        "score": sc, "why": sorted(why.get(other, []))[:5]})
    return out


def local(q, k=8):
    """Entity-grounded local search: vector passages + their graph context."""
    import query as Q
    hits = Q.search(q, k=k, hybrid=True)
    ents = load_entities()
    doc_ids = {h["doc_id"] for h in hits}
    ctx_ents = sorted({e["name"] for e in ents.values()
                       if doc_ids & set(e.get("doc_ids", []))})
    return {"passages": hits, "entities_in_context": ctx_ents[:40]}


def global_search(q, model=SUMMARY_MODEL, top=8):
    """Map-reduce over community summaries for a corpus-level answer."""
    sp = os.path.join(gdir(), "community_summaries.json")
    if not os.path.isfile(sp):
        return {"error": "no community summaries; run build + summarize first"}
    summaries = json.load(open(sp))
    # rank communities by lexical overlap with the query (cheap, offline)
    qter = set(re.findall(r"\w+", q.lower()))
    scored = []
    for cid, s in summaries.items():
        blob = (s.get("title", "") + " " + s.get("summary", "") + " " +
                " ".join(s.get("entities", []))).lower()
        overlap = sum(1 for w in qter if w in blob)
        scored.append((overlap, cid, s))
    scored.sort(key=lambda t: -t[0])
    chosen = [s for ov, cid, s in scored[:top] if ov > 0] or [s for _, _, s in scored[:3]]
    context = "\n\n".join("THEME: %s\n%s" % (s["title"], s["summary"]) for s in chosen)
    ans = ollama_text(
        "Using ONLY the thematic summaries of a literature knowledge graph "
        "below, answer the question. Be specific about which themes support "
        "which points.\n\nQUESTION: %s\n\nTHEMES:\n%s\n\nANSWER:" % (q, context),
        model=model)
    return {"answer": ans, "themes_used": [s["title"] for s in chosen]}


def status():
    ents = load_entities()
    rels = load_relations()
    comm = {}
    cp = os.path.join(gdir(), "communities.json")
    if os.path.isfile(cp):
        comm = json.load(open(cp))
    sp = os.path.join(gdir(), "community_summaries.json")
    n_sum = len(json.load(open(sp))) if os.path.isfile(sp) else 0
    ncomm = len(set(comm.values())) if comm else 0
    return {"entities": len(ents), "relations": len(rels),
            "communities": ncomm, "community_summaries": n_sum}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["seed", "extract", "build", "summarize",
                                        "run", "neighbors", "local", "global",
                                        "status"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--model", default=None)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--windows", type=int, default=None,
                    help="text windows sampled per paper (default $RAG_GRAPH_WINDOWS or 2)")
    ap.add_argument("--min-docs", type=int, default=None,
                    help="build: drop entities seen in fewer than N papers (default 2)")
    ap.add_argument("-k", type=int, default=8)
    args = ap.parse_args()

    if args.command == "seed":
        print(json.dumps(seed(), indent=2))
    elif args.command == "extract":
        print(json.dumps(extract(limit=args.limit,
                                 model=args.model or EXTRACT_MODEL,
                                 force=args.force,
                                 n_windows=args.windows), indent=2))
    elif args.command == "build":
        print(json.dumps(build(min_docs=args.min_docs), indent=2))
    elif args.command == "summarize":
        print(json.dumps(summarize(model=args.model or SUMMARY_MODEL,
                                   max_communities=args.limit), indent=2))
    elif args.command == "run":
        print(json.dumps({"seed": seed(),
                          "extract": extract(limit=args.limit,
                                             model=args.model or EXTRACT_MODEL,
                                             n_windows=args.windows),
                          "build": build(min_docs=args.min_docs),
                          "summarize": summarize(model=SUMMARY_MODEL,
                                                 max_communities=args.limit)},
                         indent=2))
    elif args.command == "neighbors":
        print(json.dumps(neighbors(args.arg, k=args.k), indent=2))
    elif args.command == "local":
        print(json.dumps(local(args.arg, k=args.k), ensure_ascii=False, indent=2))
    elif args.command == "global":
        print(json.dumps(global_search(args.arg, model=args.model or SUMMARY_MODEL),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(status(), indent=2))


if __name__ == "__main__":
    main()
