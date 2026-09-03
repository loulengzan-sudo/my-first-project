# Evidence Ledger Protocol (claim-anchor/v1)

**Purpose.** Whenever a scholar skill reads a source passage and uses it to write a
literature-based claim, the passage must be written down next to the claim — verbatim,
with a locator — so (1) a human can later directly compare "what the source said" vs
"what we wrote" (the Evidence Dossier), (2) `/scholar-write` drafts from anchored
evidence instead of memory, and (3) the Phase-8 faithfulness audit joins against
write-time evidence instead of re-retrieving everything.

Canonical record schema: `schema/claim-anchor.schema.json` (claim-anchor/v1).
Ledger location: `${PROJ}/evidence/claim-anchors.ndjson` — append-only NDJSON, one
undated file per project. Rendered artifacts: `evidence/evidence-dossier-*.md` and
`evidence/evidence-brief-*.md` via `scripts/render-evidence-dossier.py`.

---

## 1. WHEN to capture — the two-stage rule

- **Discovery is NOT capture.** Search results (a 25-hit `scholar_search`, a wave of
  WebSearch hits) belong in the search log, as today. Do NOT append ledger records for
  search hits.
- **Capture happens at claim finalization.** Append ONE record per (claim, passage)
  pair at the moment you finalize an output claim that a specific source passage
  supports, contradicts, or qualifies: a landscape-map cell, an effect magnitude, a
  mechanism-status judgment, a hypothesis derivation, a drafted sentence, an inserted
  citation on an empirical claim.
- **Tier-honest capture.** Anchor with the best evidence ALREADY IN CONTEXT. If all you
  have is a CrossRef/WebSearch abstract snippet, record it as `access_tier: "T3_abstract"`,
  `evidence_form: "abstract_verbatim"` — that is a legal, honest anchor. Do NOT go fetch
  full text just to anchor; escalation to T0–T2 is the Phase-8 audit's job. If you have
  NOTHING quotable (metadata-only citation), record `evidence_quote: null`,
  `evidence_form: "metadata_only"`, `access_tier: "T3_abstract"` or `"T4_none"` — that
  makes "cited on metadata alone" visible instead of silent.
- **KG honesty.** Knowledge-graph `findings[]`/`mechanisms[]` text is a prior paraphrase:
  record it as `evidence_form: "kg_paraphrase"` with `access_tier: "T0_kg_fulltext"`.
  Never present KG text as a source quote.

Quote caps: `evidence_quote` ≤ 60 words; `evidence_context` (optional ±1–2 surrounding
sentences, RECOMMENDED for direction/magnitude/population claims) ≤ 150 words.

## 2. Read-site → capture mapping

| Read site | retrieval.tool | evidence_form | access_tier |
|---|---|---|---|
| `rag_search` / scholar-rag `query.py` hit (carry `doc_id`, `chunk_id`, `text_sha256` from the hit) | `rag_search` | `source_verbatim` | `T1_fulltext` (local PDF corpus) |
| `pdftotext` on a Zotero/Mendeley PDF | `zotero_pdf` | `source_verbatim` | `T1_fulltext` |
| Open-access full text via WebFetch (S2 `openAccessPdf`, arXiv, Europe PMC) | `webfetch` | `source_verbatim` | `T2_oa_fulltext` |
| `kg_get_paper` / `kg_search_papers` extraction text | `kg` | `kg_paraphrase` | `T0_kg_fulltext` |
| CrossRef/S2 abstract, WebSearch snippet | `websearch` | `abstract_verbatim` | `T3_abstract` |
| Nothing retrievable | (omit) | `metadata_only` | `T4_none` |

Crosswalk with `_shared/source-integrity.md` Part B (which predates this protocol):
its "Tier 1 (Zotero PDF)" = `T1_fulltext`; "Tier 2 (API metadata/abstract)" =
`T2_oa_fulltext` when full text was fetched, else `T3_abstract`. **One retrieval serves
both protocols** — when source-integrity Part B has you read a PDF to check a claim,
capture the passage you judged against as an anchor in the same step; do not retrieve
twice.

## 3. Identity and dedup

`anchor_id = <cite_key lowercase>-<first 8 hex of sha1(cite_key|quote_sha256|source_loc)>`

- Identity is **source-side**: rewriting the prose does not mint a new anchor; one
  passage reused by several sentences is ONE anchor referenced by several bindings.
- `quote_sha256` = sha256 of the **normalized** quote (lowercased, whitespace collapsed);
  for `evidence_quote: null` records use the empty-string hash.
- The append helper **dedups on anchor_id — first capture wins**. A re-capture of the
  same passage with different `claim_text` is the same observation; claim linkage lives
  in the bindings (§4), not in the snapshot fields.
- Corrections: append a new record with `supersedes: "<old anchor_id>"`. Consumers take
  the latest record per anchor_id.

## 4. Bindings (claim ↔ anchor joins — all many-to-many)

1. **Drafts (prose):** HTML comment immediately after the evidence-bearing sentence:
   `<!--ev: autor2013-3f9a1c2e-->` or `<!--ev: autor2013-3f9a1c2e, card2020-9c01ab2f-->`.
   Exact grammar (used by stripper, resolver, gates, tests — do not improvise):
   `<!--\s*ev:\s*ID(\s*,\s*ID)*\s*-->` where `ID = [a-z0-9_-]+-[0-9a-f]{8}`.
   Tags are lowercase, single-line, and must never contain `lit:`, `design:`,
   `anchor:`, or a decimal number. Tags are disposable navigation hints: they are
   stripped from submission renders; the ledger and audit survive.
2. **Landscape map (no inline tags):** `evidence/claim-inventory.json` rows carry
   `anchor_ids: []` (see `/scholar-lit-review` Phase 4i-evidence).
3. **Audit:** `claim-faithfulness-audit-*.ndjson` records carry `anchor_refs: []`.

`claim_text`/`claim_loc` inside anchor records are advisory first-use snapshots only.

## 5. Bash helpers (eval-load this block, same pattern as refmanager-backends.md)

```bash
# ── Evidence Ledger helpers (claim-anchor/v1) ──────────────────────────
# Requires: jq. Hashing: shasum (macOS) or sha1sum/sha256sum (Linux).

_ev_sha256() { if command -v shasum >/dev/null 2>&1; then printf '%s' "$1" | shasum -a 256 | cut -d' ' -f1; else printf '%s' "$1" | sha256sum | cut -d' ' -f1; fi; }
_ev_sha1()   { if command -v shasum >/dev/null 2>&1; then printf '%s' "$1" | shasum -a 1   | cut -d' ' -f1; else printf '%s' "$1" | sha1sum   | cut -d' ' -f1; fi; }

# Normalize for hashing: lowercase, collapse all whitespace runs to single spaces, trim.
ev_normalize() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -s '[:space:]' ' ' | sed 's/^ //; s/ $//'; }

# Ledger path. Uses $PROJ if set, else $OUTPUT_ROOT, else output/. Creates evidence/.
ev_ledger_path() {
  local root="${PROJ:-${OUTPUT_ROOT:-output}}"
  mkdir -p "$root/evidence"
  printf '%s' "$root/evidence/claim-anchors.ndjson"
}

# ev_anchor_id <cite_key> <quote-or-empty> <source_loc-or-empty>
ev_anchor_id() {
  local ck qs h
  ck=$(ev_normalize "$1")
  qs=$(_ev_sha256 "$(ev_normalize "${2:-}")")
  h=$(_ev_sha1 "${ck}|${qs}|$(ev_normalize "${3:-}")")
  printf '%s-%s' "$(printf '%s' "$ck" | tr -cd 'a-z0-9_-')" "${h:0:8}"
}

# ev_capture — build + append one record. Args via environment variables:
#   EV_CITE_KEY (required)  EV_CLAIM_KIND (required)  EV_STANCE (default supports)
#   EV_QUOTE  EV_CONTEXT  EV_FORM (required)  EV_TIER (required)  EV_SOURCE_LOC
#   EV_LOCATOR_TYPE  EV_CLAIM_TEXT  EV_CLAIM_LOC  EV_DOI  EV_TITLE  EV_HYP_ID
#   EV_TOOL  EV_DOC_ID  EV_CHUNK_ID  EV_TEXT_SHA  EV_PRODUCED_BY (required)  EV_PHASE
#   EV_SUPERSEDES
# Prints the anchor_id (existing or new). Dedup: first capture wins.
ev_capture() {
  [ -n "${EV_CITE_KEY:-}" ] && [ -n "${EV_CLAIM_KIND:-}" ] && [ -n "${EV_FORM:-}" ] \
    && [ -n "${EV_TIER:-}" ] && [ -n "${EV_PRODUCED_BY:-}" ] || {
    echo "ev_capture: missing required EV_ vars (CITE_KEY/CLAIM_KIND/FORM/TIER/PRODUCED_BY)" >&2; return 1; }
  local ledger id qsha ts
  ledger=$(ev_ledger_path)
  id=$(ev_anchor_id "$EV_CITE_KEY" "${EV_QUOTE:-}" "${EV_SOURCE_LOC:-}")
  if [ -f "$ledger" ] && grep -qF "\"anchor_id\":\"$id\"" "$ledger" 2>/dev/null; then
    printf '%s\n' "$id"; return 0
  fi
  qsha=$(_ev_sha256 "$(ev_normalize "${EV_QUOTE:-}")")
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  EV_ID="$id" EV_QSHA="$qsha" EV_TS="$ts" jq -cn '
    def nn(v): if v == "" then null else v end;
    {schema:"claim-anchor/v1",
     anchor_id:env.EV_ID,
     cite_key:env.EV_CITE_KEY,
     doi:nn(env.EV_DOI // ""), source_title:nn(env.EV_TITLE // ""),
     claim_kind:env.EV_CLAIM_KIND,
     stance:(env.EV_STANCE // "supports"),
     claim_text:nn(env.EV_CLAIM_TEXT // ""), claim_loc:nn(env.EV_CLAIM_LOC // ""),
     hypothesis_id:nn(env.EV_HYP_ID // ""),
     evidence_quote:nn(env.EV_QUOTE // ""), evidence_context:nn(env.EV_CONTEXT // ""),
     evidence_form:env.EV_FORM,
     quote_sha256:env.EV_QSHA,
     source_loc:nn(env.EV_SOURCE_LOC // ""),
     locator_type:(env.EV_LOCATOR_TYPE // "none"),
     access_tier:env.EV_TIER,
     retrieval:{tool:nn(env.EV_TOOL // ""), doc_id:nn(env.EV_DOC_ID // ""),
                chunk_id:nn(env.EV_CHUNK_ID // ""), text_sha256:nn(env.EV_TEXT_SHA // "")},
     produced_by:env.EV_PRODUCED_BY,
     phase:nn(env.EV_PHASE // ""),
     ts:env.EV_TS,
     supersedes:nn(env.EV_SUPERSEDES // "")}
  ' >> "$ledger" && printf '%s\n' "$id"
}

# Subagent sidecar mode: set EV_SIDECAR=<report>.anchors.ndjson before loading and
# ev_capture appends there instead (orchestrator ingests with ev_ingest_sidecar).
if [ -n "${EV_SIDECAR:-}" ]; then
  ev_ledger_path() { mkdir -p "$(dirname "$EV_SIDECAR")"; printf '%s' "$EV_SIDECAR"; }
fi

# ev_ingest_sidecar <sidecar.ndjson> — single-writer fold-in with dedup.
ev_ingest_sidecar() {
  local sc="$1" ledger line id
  [ -f "$sc" ] || return 0
  EV_SIDECAR="" ledger="${PROJ:-${OUTPUT_ROOT:-output}}/evidence/claim-anchors.ndjson"
  mkdir -p "$(dirname "$ledger")"
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    id=$(printf '%s' "$line" | jq -r '.anchor_id // empty' 2>/dev/null) || continue
    [ -n "$id" ] || continue
    grep -qF "\"anchor_id\":\"$id\"" "$ledger" 2>/dev/null && continue
    printf '%s\n' "$line" >> "$ledger"
  done < "$sc"
}

# ev_count — record count in the project ledger (0 if absent).
ev_count() { local l="${PROJ:-${OUTPUT_ROOT:-output}}/evidence/claim-anchors.ndjson"; [ -f "$l" ] && wc -l < "$l" | tr -d ' ' || echo 0; }
```

**Concurrency rule:** main-context skills append via `ev_capture` (single line, atomic
in practice). Task subagents must NOT write the ledger directly — set
`EV_SIDECAR="<report>.anchors.ndjson"` in the agent prompt and have the orchestrating
skill call `ev_ingest_sidecar` afterward (same idiom as `agent-trace-contract.md`).

## 6. Privacy / copyright / sharing

- Verbatim source passages live ONLY under `evidence/` (ledger, audit, dossier, briefs).
  RAO traces (`logs/trace-*.ndjson`) stay quote-free and reference anchors by id in
  `refs[]` — the trace privacy rule in `_shared/process-logger.md` is unchanged for
  traces; `evidence/*.ndjson` is the sanctioned home for quotes.
- `evidence/` is EXCLUDED from replication packages by default (quotes are for internal
  audit and drafting, not redistribution).
- Respect the quote caps (§1). The dossier renderer elides over-long quotes.

## 7. Producer integration pattern (3 lines per skill — do not inline this protocol)

Every producer skill adds exactly:
1. One pointer: *"Evidence Ledger (MANDATORY): load `_shared/evidence-ledger.md` and
   capture anchors at claim finalization per its §1–§2."*
2. One sentence naming that skill's qualifying claims and read sites.
3. One output-contract line: the skill's log/report MUST include the row
   `Evidence anchors: N created / M reused` (from `ev_capture` output and `ev_count`).

Orchestrated runs are additionally gate-enforced (`scripts/gates/evidence-anchor-check.sh`);
standalone runs are enforced by the required log row — the capture duty itself is never
optional.
