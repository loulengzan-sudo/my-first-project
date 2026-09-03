# MODE 7 companion — construct-match (HARD GATE, alongside κ)

**A measurement is not a number. It is a number plus the question that produced it.**

The κ gate validates an annotator against gold **for the task as prompted**. It structurally
cannot detect that the task as prompted is *broader or narrower than the model consuming the
number needs*. Both instruments can score κ = 0.95 against their own gold. Construct-match is
a separate question and it needs its own record.

---

## The failure this exists to prevent

A composite of the failure mode as it has appeared in large-corpus annotation runs. It is the
most consequential kind of error in this mode, and in the run that motivated this document it
survived four review iterations, a precision audit, and an orchestrator.

An analysis script hard-codes a sensitivity constant — `TARGET_RECALL <- 0.62` — together with a
breakdown of where the annotator's false negatives fell. Both come from an LLM audit whose
prompt asked about

> "the superordinate category in the sense used by domain experts"

and credited "an alternative or colloquial name". The consuming model implements a *lexicon*,
which deliberately does not fire on colloquial alternatives. A codebook-matched re-draw on the
**same item set** measures:

| | BROAD instrument | MATCHED instrument |
|---|---:|---:|
| target-class sensitivity | 0.61 | **0.94** [0.87, 0.98] |
| false negatives | many, concentrated in one surface form | **few, none in that form** |

The "surface-form asymmetry" that drove a declared sensitivity arm, a second arm, an
outcome-conditioned predecessor, and a planned manuscript caveat **is not present under the
matcher's own codebook.** The BROAD instrument scored a *design decision* as a
*measurement failure*.

**Why every existing check missed it — three reasons, each with its own remedy here:**

1. **The literal had no path to its source.** When the discredited column was renamed and
   `claim_supported = False` was written on every row, none of it reached
   `TARGET_RECALL <- 0.62`, *because a literal is not a read*. A number typed into source cannot
   be invalidated by fixing the artifact it came from. → Remedy: `design/measurement-constants.json`.
2. **A comment asserted the opposite.** The block header read *"NOTHING here is hard-coded
   from a count"* — true of the cell counts, false of the rate, and the rate was the
   load-bearing input. → Remedy: the gate reads the assignment, not the comment.
3. **Nobody asked what the annotator was asked.** Six `review-code-*` agents checked whether
   the number was *used* correctly. None checked whether it *measured the construct the model
   needed.* → Remedy: the construct-match record below, and the U17 line now in the
   `review-code-statistics` / `review-code-data-handling` briefs.

It was caught only because the estimator **refused**: one job in that DAG died with
`FATAL (SENS_ARM_1): singular information matrix`. A sensitivity estimate near zero with
specificity pinned at 1 is a
degenerate corner and the fit declined it. The job did not fail — it refused. Prefer a hard
refusal to a plausible value; the refusal is the diagnostic. A gate is cheaper than a refusal.

---

## What to write

One record per instrument, in `design/construct-match.json` (or `annotations/`, `compute/`,
`reports/` — the gate searches all four). Either a bare object, a list, or
`{"instruments": [...]}`.

```json
{"instruments": [
  {
    "instrument_id": "target-recall-v2",
    "asked":  "Does this comment fire names_target as the lexicon implements it — i.e. does it contain one of the listed surface forms?",
    "needed": "Does this comment fire names_target as the lexicon implements it?",
    "verdict": "MATCH",
    "consumers": ["scripts/06-naming-logits.R"],
    "codebook": "annotations/codebook-the target class-v2.md",
    "assessed_by": "human + re-draw on the same item set",
    "note": "v1 (target-recall-broad) asked about the superordinate category and credited colloquial alternatives; retired 2026-08-11, see amendment log."
  }
]}
```

**`asked` and `needed` are both required and must be stated in the annotator's own words.**
Paraphrasing them into agreement is how this check fails. If you cannot write `asked` without
consulting the prompt file, you are not yet in a position to issue a verdict.

### Verdicts

| verdict | meaning | gate |
|---|---|---|
| `MATCH` | the prompted construct and the consumed construct are the same question | GREEN |
| `BROADER` | the annotator was asked a wider question than the model needs — it will score design decisions as measurement failures (**the §12 shape**) | RED |
| `NARROWER` | the annotator was asked a tighter question — the model silently generalises beyond what was measured | RED |
| `UNASSESSED` | nobody has compared them | RED |

There is **no default**. A missing `verdict` is `UNASSESSED`, which is RED — a companion field
that defaults to the passing value reintroduces the collapse one level up (see
`_shared/defect-class-registry.md`, shape D remedy constraint 2).

---

## Design parameters belong to the declared gate, not to CLI defaults

Separate register, same principle. From §11 of the same run: the PI authorised expanding a
precision audit from n=100 to n=400 (n=1,000 for a borderline variety) *specifically because* a
KEEP/DROP gate rested on overlapping Wilson intervals. Two days later a rebuild re-ran that node
at the wrapper's **default `PER_STRATUM=100`** and overwrote the expanded result. One decision
flipped: variety A 0.925 [0.895, 0.947] KEEP at n=400 became 0.83 [0.745, 0.891]
INDETERMINATE→DROP at n=100 — **a power artifact**, since the point estimate moved within
sampling noise. The expansion existed only as a command-line argument someone once typed.

Declare it instead, in `design/measurement-design.json`:

```json
{"measurements": [
  {"name": "precision-audit-variety",
   "artifact": "tables/precision_by_stratum.csv",
   "expect": {"n_requested": 400, "seed": 23, "strata_col": "variety"}}
]}
```

`annotate_engine.py sample` writes `<out>.design.json` with exactly these keys, and
`measurement-instrument-check.sh` R4 compares them. A rebuild that quietly reverts to a default
is now RED rather than invisible.

> **Generalisation worth carrying:** any default that silently determines the evidentiary
> strength of a shipped decision is a trap, not a default.

---

## Two definite states must not share one label

Also from §11/§3D, because it bites the artifacts this mode produces.
`precision_decisions.csv` printed `decision = DROP` for variety B under both audit
vintages. But at n=1,000 the Wilson **upper** bound sat below the 0.80 floor — *measured below
the floor*; at n=100 the interval merely **straddled** it — *could not tell, so the pre-stated
tie-break decided*. Same verdict, entirely different epistemic status, and `decision` alone
cannot distinguish them. Any prose describing variety B as "measured below the measurement floor"
is true of one vintage and false of the other.

**Rule.** A field that records a conclusion must also record **how the conclusion was reached**,
whenever more than one route can produce the same value. The reviewer-facing form needs no
domain knowledge:

> Count the branches that write a column; count the distinct values they can emit.
> **If branches > values, the column is lossy and needs a companion.**

Two constraints on the companion, both learned by watching them fail:

1. **Written by the deciding branch, never reconstructed afterwards.** Reconstruction is what
   `analysis_table_sha256` did — read from a manifest and stamped without hashing.
2. **No default.** A companion defaulting to `measured` reintroduces the same shape one level up.

`annotate_engine.py validate` follows this: every gold row excluded from κ carries
`excluded_by` naming the branch that excluded it (`annotator_absent` / `annotator_blank` /
`gold_empty`), and `cohen_kappa: null` is always accompanied by `kappa_undefined_reason`.

---

## Running the gate

```bash
bash "${SCHOLAR_SKILL_DIR:-.}/scripts/gates/measurement-instrument-check.sh" "$PROJ"
```

| RC | Meaning | Action |
|----|---------|--------|
| 0 GREEN | every declared constant binds to an instrument whose construct matches its consumer | proceed to MODE 8 |
| 1 RED | R1 undeclared instrument / R2 constant drift / R3 construct mismatch / R4 design drift | do NOT scale; fix the binding or re-measure under the matched instrument |
| 2 YELLOW | waived provenance and/or undeclared measurement-shaped literals | declare them; a waiver is a recorded absence, not a satisfied requirement |
| 3 INERT | no measurement artifacts, declarations, or literals — this project does not use the measurement path | n/a |

Full rule text and fixtures: `scripts/gates/measurement-instrument-check.sh` header,
`tests/smoke/test-measurement-instrument-check.sh`.
