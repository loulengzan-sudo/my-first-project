# Process Logging Protocol — Reasoning · Action · Observation (RAO) Trace

Every `/scholar-*` skill run MUST leave a transparent, auditable trace of **what it reasoned, what it did, and what it observed** — a ReAct-style record. This is enforced: `trace-coverage-check.sh` RED-fails a phase/skill that produced no valid trace.

**Source of truth = an append-only NDJSON trace.** The human-readable `process-log-*.md` is now a *rendered view* of it — do not hand-write table rows. One writer helper (`emit-trace.sh`) and one renderer (`render-trace.sh`) do the work; skills just call them.

> What "reasoning" means: the harness cannot log a model's hidden chain-of-thought. Log the **stated rationale** — a short "why" you can articulate — alongside the action and the observation.

---

## The sink

```
${OUTPUT_ROOT}/logs/trace-<skill>-<YYYY-MM-DD>.ndjson     # append-only, one record per step
${OUTPUT_ROOT}/logs/process-log-<skill>-<YYYY-MM-DD>.md   # rendered markdown view (generated)
```

Record schema (written by `emit-trace.sh`; you never hand-author these):

```json
{"ts","seq","run_id","skill","phase","agent","agentId","session_id","step",
 "reasoning","action","observation","refs":[...],"status"}
```

Required per record: `skill`, `step`, and **≥1 non-empty of** `{reasoning, action, observation}`. A well-formed step carries all three.

`session_id` is auto-derived from `$CLAUDE_CODE_SESSION_ID` (null under a Codex host or a bare runner) and needs no caller action; it records which session produced the step. It is deliberately *not* in `trace-coverage-check.sh`'s required set: that gate asserts the required fields are a **subset** of a record's keys, so adding a field never retroactively REDs an older trace.

---

## STEP — emit one record per meaningful step

After each meaningful step (a decision, a tool/script run, a subagent dispatch, a gate call), append a record. **Shell variables do NOT persist across Bash tool calls** — but you do not need to track any state: `emit-trace.sh` re-derives the path and the monotonic `seq` from the file itself. Just call it:

```bash
bash "${SCHOLAR_SKILL_DIR:-.}/scripts/gates/emit-trace.sh" \
  --skill scholar-XXXX --phase "<phase-or-omit>" --step "<label>" \
  --reasoning "<why — the stated rationale, 1–2 lines>" \
  --action    "<what you did — tool/script/gate/subagent call + key args>" \
  --observation "<what came back — verdict/metric/count/error/file>" \
  --refs "<comma-separated artifact paths, or omit>" \
  --status ok            # ok | fail | skipped
```

- **step**: the skill's own step id (e.g. `A0-parse-args`, `5B-main-models`).
- **reasoning**: the *why* (`"units nested within states → cluster SEs at state level"`). Do not restate the action.
- **action**: the *what* (`"feols(y ~ x | state, cluster=~state)"`).
- **observation**: the *result* — aggregate metrics, verdicts, counts, or file refs.
- On failure/skip, still emit the record with `--status fail|skipped` and put the reason in `--observation`.

`--phase` is for orchestrated runs (multi-phase pipelines like scholar-auto-research); standalone skills may omit it.

---

## END — render the human-readable log

At the end of the run (Save Output section), render the markdown view from the trace:

```bash
OUTPUT_ROOT="${OUTPUT_ROOT:-output}"; TRACE="${OUTPUT_ROOT}/logs/trace-scholar-XXXX-$(date +%Y-%m-%d).ndjson"
bash "${SCHOLAR_SKILL_DIR:-.}/scripts/gates/render-trace.sh" "$TRACE"
# Optional self-check (a phase gate can run this too):
bash "${SCHOLAR_SKILL_DIR:-.}/scripts/gates/trace-coverage-check.sh" "$OUTPUT_ROOT" --skill scholar-XXXX
```

The rendered `process-log-<skill>-<date>.md` carries the columns `# | Time | Phase | Agent | Step | Reasoning | Action | Observation | Refs | Status`. Do not hand-edit it — re-render from the trace instead.

---

## Subagents (Task/Agent dispatches)

Dispatched agents (peer-reviewer-*, review-code-*, verify-*, …) mostly have **no Bash tool**, so they cannot append to the NDJSON directly. They emit a **sidecar** `<report>.trace.ndjson` and echo `TRACE: <path>`; the dispatching skill folds it into the master trace. See **`_shared/agent-trace-contract.md`** for the agent side and use `ingest-agent-trace.sh` on the orchestrator side:

```bash
bash "${SCHOLAR_SKILL_DIR:-.}/scripts/gates/ingest-agent-trace.sh" \
  --sidecar "<report>.trace.ndjson" --skill scholar-XXXX \
  --agent "<subagent_type>" --agentId "<id-from-Task>" --phase "<phase>" --proj "$PROJ"
```

This also cross-checks the `agentId` against `logs/dispatch-manifest.jsonl` (the dispatch-provenance manifest, when a run produces one, is unchanged; the trace references it, it does not replace it).

---

## Privacy (C-01 / LOCAL_MODE) — MANDATORY

The trace is a durable, shareable artifact. `observation` and `reasoning` carry **aggregate metrics, verdicts, counts, and file references ONLY — never raw data rows, quotes, or PII** (names, emails, IDs, DOBs, addresses). Under `LOCAL_MODE`, log derived aggregates, never row-level values. `emit-trace.sh` warns on obvious email/SSN-like values, but the discipline is yours: describe *that* you observed something and its shape, not the sensitive value itself.

**Scope clarification (Evidence Ledger):** this no-verbatim-quotes rule applies to *traces* (`logs/trace-*.ndjson`). The Evidence Ledger artifacts under `${PROJ}/evidence/` (`claim-anchors.ndjson`, `claim-faithfulness-audit-*.ndjson`, dossiers, briefs) are the **sanctioned home for verbatim source passages** — quote-capped, replication-excluded, and governed by `_shared/evidence-ledger.md` §6. A trace step that captures an anchor references it by `anchor_id` in `refs[]`; it never inlines the quote. Do not flag `evidence/*.ndjson` as a trace-privacy violation.

---

## Notes

- One trace file per skill per day (records accumulate; `seq` stays monotonic; `render-trace.sh` orders by `seq`). Distinguish same-day reruns with `--run-id` if needed.
- Orchestrator skills that call sub-skills: emit a trace record at each phase boundary (`--phase`), and `ingest-agent-trace.sh` every dispatched agent's sidecar so the phase's subagent reasoning lands in the master trace. Sub-skills still write their own trace.
- The trace/log file itself is not listed among a run's "Output Files".
