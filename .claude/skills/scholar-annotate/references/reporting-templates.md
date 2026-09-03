# MODE 10 — Reporting & Deliverables

Assemble the labeled dataset plus a reproducible Methods/Results packet.

## Artifacts
- `output/tables/…labeled.csv` / `.parquet` — the full labeled dataset (id + fields + rationale).
- `codebook.md` (versioned), `annotator_program.json` (+ hash), `validation_report.json`.
- `<sample>.design.json` — the design sidecar (n, pool, seed, strata, lexicon sha256, unreadable count).
- `design/construct-match.json` + `design/measurement-constants.json` — instrument provenance.
- `cost_ledger.json`, AI-use/data-transfer disclosures (from the audit log).

## Constants leaving this skill carry their instrument

Any number produced here that will be **typed into a downstream script** — a recall rate, a
sensitivity, a false-negative split, a base rate — must be declared in
`design/measurement-constants.json` with the `instrument_id` it was measured under, and that
instrument must carry a `MATCH` construct-match verdict against the consuming model. See
`construct-match.md`.

**Why this is a reporting rule and not just a coding rule.** A number typed into source has no
path back to its artifact: when the 2026-08 run retracted a discredited measurement, renamed the
column, and wrote `claim_supported = False` on every row, none of it reached
`TARGET_RECALL <- 0.62`, *because a literal is not a read*. The retraction was thorough on the
artifact side and the manuscript still shipped the retracted claim.

**The same applies to prose fields inside artifacts.** Estimand strings, `*_NOTE` columns,
drafter instructions, and arm inventories are **outputs**, and some are lifted verbatim into the
manuscript. All six `review-code-*` agents read code; the `verify-*` agents read the manuscript
against the tables; nobody reads the prose *inside* the analysis artifacts. When a finding is
retracted or an arm retired, grep the emitting scripts for narrative strings referencing it, not
only for code paths. Estimand strings destined for a manuscript should be **generated from the
same artifact the numbers come from**, never typed alongside them — a typed estimand string is
`TARGET_RECALL <- 0.62` in prose form.

## Methods prose (drop-in) — fill every bracket
> We measured [construct] over [N] [units] using an LLM annotator. A codebook ([K] classes;
> [sub-codes]) was compiled into a typed prompt and output schema. We built a [size]-item gold
> set via [human double-coding (Krippendorff α = [x]) / dual-model agreement (Claude+GPT κ = [x])],
> optimized the prompt with DSPy (ChainOfThought + BootstrapFewShot, [k] demonstrations), and
> validated against a held-out gold split: **Cohen κ = [x]**, macro-F1 = [x] (per-class F1 in
> Table [n]). Annotation ran at temperature 0 on [model id] ([date]) via [managed Batch API /
> local llama.cpp server on <cluster>]; prompts, program hash, and model id are archived. [If
> distilled:] labels were distilled to a [TF-IDF+logistic] classifier (κ = [x] vs gold) to score
> the full corpus; predicted labels entering regressions were DSL-bias-corrected.

## Results reporting
- Class/frame distribution over the corpus (Table).
- Reliability: κ (+ per-class F1) **and coverage** — the share of the gold set the annotator
  actually answered. A κ reported without its coverage describes a subsample of unknown
  selectivity, not the annotator.
- The construct the annotator was asked about, in its own words, where it differs at all from
  the construct the downstream model uses.
- Cost + compute; data-transfer disclosure.
- Honest framing: this is a **measurement** with error — report the κ as the measurement's
  reliability and (for distillation) treat outputs as predicted-with-error.

## Save
Two files (version-checked): internal log + this Methods/Results doc → also `.docx/.tex/.pdf`
via `_shared/pandoc-multiformat.md`. Hand the labeled variable to `/scholar-analyze` or
`/scholar-compute` for downstream modeling.
