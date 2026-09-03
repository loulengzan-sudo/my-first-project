# scholar-rag engine reference (`assets/`)

Self-contained Python engine. All modules import `store.py` for the corpus
manifest; nothing depends on the rest of the plugin. Run everything with the
venv interpreter (`$SCHOLAR_RAG_DIR/.venv/bin/python`, aliased `rag_py` / `RAG_PY`).

## Module map

| Module | Role | Key deps |
|---|---|---|
| `store.py` | `corpus.sqlite` schema + CRUD; store layout; hashing; manifest | stdlib only |
| `zotero_reader.py` | read `zotero.sqlite` (immutable-ro, no copy) → items + resolved PDF paths | stdlib only |
| `ingest.py` | stage 1: source (Zotero/folder) → document rows | stdlib only |
| `extract.py` | stage 2: PDF → page-mapped text (PyMuPDF → pdftotext → vision-OCR) | fitz |
| `chunk_embed.py` | stage 3: section-aware chunk + bge-m3 → LanceDB + BM25 index | sentence-transformers, lancedb |
| `fetch.py` | locate open-access PDFs for `no_pdf` items (Unpaywall → OpenAlex → arXiv) | requests |
| `query.py` | dense + hybrid (RRF) + rerank retrieval | sentence-transformers, lancedb |
| `graphrag.py` | seed(KG) → LLM extract → Leiden → summaries; local/global/neighbors | ollama HTTP, igraph, leidenalg |
| `citations.py` | OpenAlex bibliographic layer: direct cites, coupling, co-citation, paper communities | requests, igraph, leidenalg |
| `keywords.py` | fold Zotero tags / OpenAlex topics / in-text keyword lines into the entity graph | stdlib only |
| `semantic.py` | paper kNN + entity synonym edges from existing chunk vectors; duplicate detection | numpy, pyarrow, lancedb |
| `mcp_server.py` | stdio MCP server (FastMCP successor `MCPServer`) | mcp |
| `setup-venv.sh` / `run-ingest.sh` / `run-graph.sh` / `mcp-setup.sh` / `_lib.sh` | provisioning + resumable build + registration + shell helpers | uv, bash |
| `sync-engine.sh` | push edits from this `assets/` dir into the runtime engine, smoke-test imports, re-register MCP | rsync, bash |

## Store layout (`$SCHOLAR_RAG_DIR`, default `~/.claude/scholar-rag/`)

```
corpus.sqlite   documents(doc_id PK, zotero_key, doi, title, authors_json, year,
                journal, item_type, source, pdf_path, pdf_sha256, extract_method,
                ocr_used, n_pages, n_chars, text_path, quality, status, error, …)
                chunks(chunk_id PK, doc_id, ord, section, page_start, page_end,
                       char_start, char_end, n_chars, text_sha256, embedded)
raw/text/<doc_id>.json      page-mapped extracted text (cache; enables re-chunk/re-embed)
raw/meta/<doc_id>.json      full bibliographic record
raw/openalex/<doc_id>.json  cached OpenAlex record: referenced_works + topics/keywords
index/                      LanceDB table `chunks` (vector + metadata + FTS/BM25)
graph/
  entities.ndjson           {ent_id, name, type, type_counts, aliases, doc_ids[], count, source}
  relations.ndjson          {src, rel, dst, doc_ids[], weight}
  extracted_docs.json       {doc_id: [window signatures]} — extraction progress
  doc_edges.ndjson          paper→paper edges seeded from scholar-knowledge
  citations.ndjson          direct intra-corpus citations (OpenAlex)
  paper_edges.ndjson        weighted paper graph (citation + coupling + co-cite + semantic)
  paper_communities.json    {doc_id: community_id}
  doc_semantic.ndjson       paper↔paper kNN edges
  entity_semantic.ndjson    entity↔entity near-synonym edges
  communities.json          {ent_id: community_id}
  community_summaries.json  {community_id: {title, summary, entities[], size}}
manifest.json               embed model + dim + chunk params + source + build stats
logs/                       trace-scholar-rag.ndjson + ingest-run-*.log
.venv/                      self-contained CPython 3.12 environment
```

`status` lifecycle per document: `new → extracted → embedded` (or `no_pdf` / `failed`). Each stage only processes rows not yet advanced, so the whole build is resumable.

## Data flow

```
Zotero/PDFs ──ingest──▶ documents(new)
   PDF ──extract(pymupdf|pdftotext|vision-ocr)──▶ raw/text + documents(extracted)
   text ──chunk(section-aware,~2000ch,overlap 300)+bge-m3──▶ LanceDB + documents(embedded)
   query ──bge-m3 q-embed + cosine (+BM25 RRF +rerank)──▶ cited passages

   ENTITY GRAPH
     docs ──seed(scholar-knowledge)──▶ entities/relations
     docs ──LLM extract over N section windows──▶ entities/relations
     Zotero tags + OpenAlex topics + in-text keywords ──▶ entities (same identity)
     entity names ──bge-m3──▶ entity_semantic.ndjson (synonym edges)
     ──build: relations + co-occurrence + citation projection + synonyms
       ──Leiden(resolution)──▶ communities ──LLM──▶ summaries ──▶ local/global

   PAPER GRAPH
     DOIs ──OpenAlex──▶ raw/openalex (referenced_works + topics)
     ──build: direct citation + bibliographic coupling + co-citation + semantic kNN
       ──Leiden(resolution)──▶ paper_communities ──▶ citations neighbors

   MCP server wraps query.py + graphrag.py for Claude Code / Codex
```

## Entity identity (the single most consequential design decision)

`ent_id = sha256(norm_name(name))[:16]` — **the normalized name only.**

Type used to be part of the key. That fragmented exactly the hubs that matter,
because the LLM types the same concept inconsistently across papers: "machine
learning" existed as three separate nodes (method / concept / population).
`type` is now a majority vote across mentions, stored alongside `type_counts`.

`norm_name()` folds case, punctuation, unicode dashes and plurals. The dash
folding is not cosmetic — `COVID-19` and `COVID‑19` (U+2011) hashed apart and
split the largest hub in a real corpus.

Consequences to respect:

- **Keywords merge into existing nodes rather than duplicating them**, because
  they use the same identity function. That is why `keywords.py` has no output
  file of its own.
- **`author` is deliberately not an entity type.** `corpus.sqlite` already holds
  authoritative `authors_json`; LLM-scraped author names (mostly harvested from
  reference lists) were ~14% of one real graph and almost entirely single-paper.
- Normalization collapses variants of the *same string*. Different strings that
  mean the same thing ("residential segregation" / "neighborhood segregation")
  need `semantic.py entities`, which links them as edges rather than merging —
  so a bad pairing distorts clustering instead of destroying a node.

## Graph assembly: signals and their failure modes

`graphrag.py build` unions four edge sources. Each has a characteristic way of
going wrong, and every threshold below exists because of one:

| Source | Env knob | Failure mode it guards |
|---|---|---|
| LLM relations | — | Sparse: most are paper-local, so pruning one endpoint deletes the edge. |
| Co-occurrence | `RAG_GRAPH_COOC_MIN` (2) | At 1 the graph collapses into a few blobs; at 3 it starves. |
| Citation projection | `RAG_GRAPH_CITE_WEIGHT` (1.0), `RAG_GRAPH_CITE_MIN_SUPPORT` (8) | **Quadratic.** One citation between two 15-entity papers mints ~225 pairs; 17k citations produced 1.5M pairs and buried every other signal. Weight normalization alone does not fix this — it caps weight, not edge *count*. |
| Entity synonyms | `RAG_GRAPH_SEM_WEIGHT` (3.0) | Threshold-sensitive; see calibration below. |

`RAG_GRAPH_MIN_DOCS` (2) prunes entities seen in a single paper — they cannot
link anything and only form isolated within-paper cliques.

`RAG_GRAPH_RESOLUTION` (6.0) is the granularity knob, and the right one to reach
for first. The graph is dense by construction, and broad keyword nodes (OpenAlex
topics like "Political science" tag hundreds of papers) act as hubs bridging
unrelated areas. At Leiden's default resolution of 1.0 that yields a handful of
giant communities. Raising resolution separates real themes **without discarding
evidence** — prefer it to raising thresholds, which throws data away and moves
the community count far less. The same applies to `RAG_CITE_RESOLUTION` (8.0)
for the paper graph.

## Similarity thresholds are not transferable

bge-m3 similarity distributions differ sharply by input length, so a cutoff
tuned on one does not transfer to the other:

- **Paper vectors** (mean-pooled chunks) are compressed high — on a real
  5.5k-paper corpus the median top-10 neighbour scored **0.886**. A 0.55 floor
  keeps essentially everything, including nonsense pairings. Default is **0.90**.
- **Entity names** (short phrases) sit at 0.3–0.6 when unrelated; genuine
  synonyms are above ~0.85. Default is **0.80**.

Always check the distribution before trusting a cutoff. Pairs at ≥0.995 are
usually the *same work ingested twice* — `semantic.py duplicates` reports them,
and they are excluded from the paper graph since linking a work to itself
asserts nothing. A `diff-doi` pair at ~1.0 means two items with different
metadata resolving to identical text, i.e. the wrong PDF is attached to one.

## Extraction windows

Extraction reads `RAG_GRAPH_WINDOWS` (4) windows per paper, each capped at
`RAG_GRAPH_MAX_CHARS` (12k chars ≈ 3k tokens), in priority order:

1. head (abstract + introduction)
2. theory / literature / related work
3. discussion + conclusion
4. results + methods

`references` is always excluded — a bibliography yields author and journal
names, which are noise in a concept graph.

Two strategies: section-aware (via `chunk_embed.detect_sections`, which succeeds
for ~84% of papers) and a positional fallback whose **last window is anchored at
the tail**. That anchoring matters: an evenly-spaced scheme with one extra window
places it at ~16–32% of the paper, so discussion and conclusion — at ~70–95% —
stay invisible no matter how many windows you add.

Progress is tracked per **window signature** (`head`, `sec:discussion`,
`pos:0.81`) in `extracted_docs.json`, not as a count. A plain counter silently
means the wrong thing the moment window 1 stops being "the early body" and
starts being "the discussion". With signatures, changing the strategy
re-extracts only the newly-reachable regions. The loader still accepts both
legacy formats (a flat list, or `{doc_id: <int>}`).

## Design decisions & gotchas

- **Dev tree and runtime engine are separate copies.** The MCP server runs
  `$SCHOLAR_RAG_DIR/engine/`, never this repo — so queries do not depend on a
  cloud-storage mount being available. Edits here therefore do NOT take effect
  until `sync-engine.sh` mirrors them across (then restart the host to reload the
  server). Run the repo copy of the script and it syncs the clone it lives in;
  the bootstrap copy at the store root falls back to `DEFAULT_SRC` — set
  `SCHOLAR_RAG_SRC` to the checkout you actually edit. Registering MCP from a
  cloud-storage `assets/` dir re-introduces the mount dependency at query time —
  always register from the local engine, which is what `sync-engine.sh` step 3 does.
- **`text_path` / `pdf_path` are absolute.** Moving `$SCHOLAR_RAG_DIR` silently
  breaks every downstream stage: `extract` reports success having read nothing,
  and marks the documents done. Rewrite them after any relocation:
  `UPDATE documents SET text_path = replace(text_path,'<old>','<new>') WHERE text_path LIKE '<old>%'`
  (same for `pdf_path`).
- **Background stages need `SCHOLAR_RAG_DIR` in their own environment.**
  `nohup … python graphrag.py summarize &` without it resolves the store to the
  default path and dies instantly with `{"error": "run build first"}`. Use
  `nohup env SCHOLAR_RAG_DIR="$STORE" …`.
- **Community ids are reassigned on every `build`.** Summaries generated against
  a previous structure silently mis-key `global` search. Always re-run
  `summarize` after `build`.
- **Zotero auto-detection is a trap.** `detect_zotero_dir()` probes only
  `~/Zotero`, `~/Documents/Zotero`, a snap path, and `~/Library/CloudStorage/*`.
  A data directory anywhere else is invisible, and the failure is silent and
  wrong — it falls through to a stale copy. Set `SCHOLAR_ZOTERO_DIR` explicitly
  and verify with `zotero_reader.py stats` before any long build. The
  authoritative source is Zotero's own
  `~/Library/Application Support/Zotero/Profiles/*/prefs.js` → `extensions.zotero.dataDir`.
- **LanceDB accumulates version fragments.** After a large write run
  `tbl.optimize(cleanup_older_than=timedelta(0))`; an uncompacted table held
  8,204 versions at 3.0 GB that compacted to 2 versions at 1.5 GB.
- **Python 3.12 venv via `uv`.** The host may run a Python newer than torch ships
  wheels for; `setup-venv.sh` provisions 3.12 with `uv` (no system Python touched).
- **bge-m3, offline at query time.** Instruction-free; queries and passages embed
  identically, normalized (cosine = dot). `query.py`/`mcp_server.py` set
  `HF_HUB_OFFLINE=1` so the cached model loads with no network round-trip. First
  use of an *uncached* reranker needs `HF_HUB_OFFLINE=0` once (or prefetch it).
- **`pandas` is not a dependency.** Read LanceDB via pyarrow batches
  (`ds.to_batches(columns=…)`), not `.to_pandas()`.
- **Zotero attachment paths.** `itemAttachments.path` is only the filename
  (`storage:foo.pdf`); the real file is `storage/<attachment-item-key>/<filename>`,
  where the key is the attachment *item's* own `items.key`. `zotero_reader.py`
  resolves stored, base-dir (`attachments:`), and linked-file attachments.
- **Author formatting.** Authors are stored `"Last, First; Last, First"` (joined by
  `;`, since each name has an internal comma) so the citation formatter can count
  authors and emit `Surname (Year)` / `Surname et al. (Year)` correctly.
- **LanceDB API drift.** `list_tables()` returns a `ListTablesResponse` (`.tables`),
  not a list; both `chunk_embed.py` and `query.py` normalize this and fall back to
  the deprecated `table_names()` on older lancedb.
- **MCP SDK.** Newer SDKs expose `mcp.server.MCPServer` (the FastMCP successor:
  same `.tool()` decorator, `.run()` stdio default); `mcp_server.py` falls back to
  `mcp.server.fastmcp.FastMCP` on older SDKs.

## Network use

Everything analytical is local. Two opt-in stages make outbound calls, both
metadata-only — no passage text, PDF, or note is ever transmitted:

- `fetch.py` — Unpaywall / OpenAlex / arXiv lookups for open-access PDFs of items
  with no local file.
- `citations.py fetch` — one request per DOI to `api.openalex.org`, carrying the
  DOI and `SCHOLAR_CROSSREF_EMAIL` (polite pool). Papers without a DOI are
  skipped deliberately: OpenAlex title-matching is unreliable enough to inject
  false citation edges, which is worse than a missing one.

## GraphRAG model selection

`ollama` is the local LLM backend. **The installed ollama build determines what
runs.** `graphrag.py` auto-selects the first available of `gpt-oss:20b` →
`qwen2.5:7b-instruct` → … → `deepseek-r1:32b`; override with `RAG_GRAPH_MODEL`
(and `RAG_SUMMARY_MODEL` for community summaries).

- `gpt-oss:20b` / `gpt-oss:120b` are the ideal (fast MoE) extractors but fail with
  `tensor "blk.0.ffn_down_exps.weight" size overflow` on older ollama builds.
  Fix: `brew upgrade ollama` (or re-pull the blob), restart the server.
- `deepseek-r1:32b` / `:70b` **run** and honor `format=json`, but are *reasoning*
  models — slow for bulk extraction.
- **Recommended when gpt-oss is unavailable:** a fast instruct model, e.g.
  `ollama pull qwen2.5:7b-instruct` (strong JSON adherence, multilingual).
- Vision-OCR uses `llama3.2-vision:90b` (`RAG_OCR_MODEL`).
- `RAG_GRAPH_THINK` (default `low`) sets reasoning effort for thinking models;
  it is auto-disabled for models that reject the `think` parameter.

Output is capped by `RAG_GRAPH_NUM_PREDICT`. Extraction over a full library is
many hours of local inference — ollama serves one request at a time, so two
concurrent stages simply halve each other's throughput. Run detached and let it
resume.

## Troubleshooting

- *"no index yet"* on query → run the build (mode 1) first; check `ingest.py status`.
- *`extract` finishes instantly having done nothing* → stale absolute paths in
  `documents.text_path` (see gotchas). Check that one path actually exists on disk.
- *`{"error": "run build first"}` from a backgrounded stage* → `SCHOLAR_RAG_DIR`
  not exported into the subprocess environment.
- *`global` search returns incoherent themes* → `community_summaries.json` is
  keyed to a superseded `communities.json`; re-run `summarize`.
- *A handful of giant communities* → raise `RAG_GRAPH_RESOLUTION` (or
  `RAG_CITE_RESOLUTION`) before touching any threshold.
- *Most entities appear in one paper only* → raise `RAG_GRAPH_WINDOWS`, and run
  `keywords.py load` — curated vocabulary is inherently multi-document and costs
  no LLM calls.
- *HF network chatter on MCP start* → model not yet cached; run one online build, or
  `setup-venv.sh --prefetch-models`.
- *many `no_pdf`* → Zotero items whose attachment file isn't synced locally; run
  `fetch.py run` for open-access copies, or sync Zotero storage.
- *GraphRAG extraction stalls* → wrong/absent ollama model; check `ollama list` and
  the model-selection notes above.
