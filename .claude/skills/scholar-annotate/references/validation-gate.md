# MODE 7 — Validation (HARD GATE)

**No full-corpus run until this passes.** LLM labels are a measurement; report their reliability
**and** the question that produced them. Two checks, both blocking.

## 1. Reliability — κ, coverage, and instrument identity

```bash
python3 assets/annotate_engine.py validate \
  --pred output/tables/llm_dev_pred.csv --gold output/tables/devset_gold.csv \
  --id-col video_id --on relevance,discourse_frame --gate 0.70 \
  --manifest output/annotations/run.json --program-hash "$(sha256sum compiled_program.json | cut -c1-16)" \
  --out output/tables/validation_report.json
```

Reports, per field: `n_gold_labeled`, `n_scored`, `coverage`, **Cohen κ (LLM vs gold)**,
**macro-F1**, an `excluded_by` breakdown, and a classification report.
**Exit 2 blocks the pipeline.**

### The bar

- **κ ≥ 0.70** on every gated field. κ, not accuracy — it corrects for the class prior (a
  90%-one-class corpus makes accuracy meaningless).
- **coverage ≥ 0.95** (tunable via `--min-coverage`). *Gold is the denominator, not the
  intersection.* Documents the annotator failed on stay in the denominator; below this
  threshold the κ describes a self-selected subsample rather than the annotator.
- **an undefined κ is never a pass.** A degenerate column (fewer than two distinct labels
  across gold and pred) reports `cohen_kappa: null` with a `kappa_undefined_reason`, and REDs.
- **every field in `--on` is gated** by default. `--primary-only` restores first-field-only
  gating and records that choice in the report.
- **instrument provenance is required.** Pass `--manifest`; `--no-instrument` records an
  explicit waiver, which downstream reads as YELLOW — a recorded absence, not a satisfied
  requirement.
- Report per-class F1: a high overall κ can still hide a failing rare class (often the target).
  If the target class F1 is weak, iterate even if overall κ passes.

> **Why these are gates and not advice.** Run against a fixture where the annotator failed 40%
> of documents, the pre-2026-08-11 engine printed "PASS — cleared for MODE 8" and exited 0:
> the failures had been inner-joined out of the denominator, the resulting single-label column
> made κ NaN, and `nan < 0.70` is False. Fixtures:
> `tests/smoke/test-annotate-engine-gate.sh`.

## 2. Construct-match — did the annotator answer the question the consumer needs?

```bash
bash "${SCHOLAR_SKILL_DIR:-.}/scripts/gates/measurement-instrument-check.sh" "$PROJ"
```

The κ gate validates an annotator against gold **for the task as prompted**. It structurally
cannot detect that the prompt is broader or narrower than the model consuming the number needs
— both instruments score well against their own gold. Write the construct-match record and
declare every measurement-derived constant; see **`references/construct-match.md`** for the
schema, the verdict table (`MATCH` / `BROADER` / `NARROWER` / `UNASSESSED`, no default), and the
§12 case that cost the most in the 2026-08 run.

| RC | Meaning |
|----|---------|
| 0 GREEN | every declared constant binds to an instrument whose construct matches its consumer |
| 1 RED | undeclared instrument, constant drift, construct mismatch, or design drift |
| 2 YELLOW | waived provenance and/or undeclared measurement-shaped literals |
| 3 INERT | the measurement path was not used |

## Lin & Zhang (2025) four risks — assess all four

1. **Validity** — does the annotator measure the intended construct? (inspect CoT rationales on
   a sample). The *cross-construct* half of this is check 2 above, and it is mechanical.
2. **Reliability** — temperature=0; report run-to-run κ on a 50-doc re-annotation.
3. **Replicability** — exact model id + version + date + prompts/program hash archived. The
   `instrument` block in `validation_report.json` now carries these; `--manifest` populates it.
4. **Transparency** — prompts reproduced in the appendix; limitations stated.

## Design parameters are declared, not defaulted

A measurement's sample size, stratification, and stopping rule are properties of the declared
gate — they belong in `design/measurement-design.json`, read by the wrapper and stamped on the
artifact, never CLI defaults decided by omission. `annotate_engine.py sample` writes
`<out>.design.json`; `measurement-instrument-check.sh` R4 compares the two. This is what stops
a rebuild from silently re-running a gate at the wrapper default and overwriting an authorised
expansion (the variety A n=400 → n=100 regression, §11).

## If it fails

Do NOT scale. Iterate the cheapest lever first: sharpen the codebook definitions/boundary rules
→ re-optimize few-shot (MODE 6) → re-validate. Consider a stronger model for the target class.
If the failure is a **construct mismatch** rather than low agreement, re-prompting will not fix
it — re-measure under a codebook-matched instrument and retire the old one in place with a
revision note. Only a passing gate unlocks MODE 8.
