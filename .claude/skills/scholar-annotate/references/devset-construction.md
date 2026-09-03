# MODE 4 — Dev / Gold Set Construction

Build the labeled set that (a) optimizes the prompt (few-shot pool) and (b) validates the
annotator (held-out split). Two competing needs → one design.

## The tension, resolved
- **Validation split → representative.** A held-out set must mirror the real class prior, or
  your κ is inflated. Do NOT over-balance it.
- **Few-shot pool → rare classes present.** BootstrapFewShot needs enough examples of the
  *target* class, which may be ~10% of the corpus. Over-sample it in the pool only.

So: stratify by the profile's strata for breadth, and over-sample the target class (via a
weak-supervision lexicon prior) for depth — but keep the validation split representative.

## Command
```bash
python3 assets/annotate_engine.py sample \
  --corpus corpus_spec.json --id-col video_id --text-cols title,description,tags \
  --strata query_category --n 3000 --pool 500 \
  --oversample lexicon.json --target ON_TOPIC \
  --out output/tables/devset.csv
```
`lexicon.json` = `{"CLASS": ["marker1","marker2",...], ...}` — cheap keyword priors, ONLY for
stratification (never shown to the annotators). Output carries a `_prior` column for analysis.

## Sizing
- Dev/gold ≈ 300–3,000 depending on the number of classes and how fine the frames are.
- Aim for **≥ ~40 gold examples per class** after annotation (few-shot + per-class F1). If a
  frame comes out thin post-annotation, re-run `sample` with a class-specific lexicon — the
  read is cheap once the pool is cached.

## Human-label template (if using human gold)
Emit the same rows with blank `relevance/…/confidence` columns for 2 coders → reconcile in MODE 5.

> Gotcha we hit: missing text fields arrive as float NaN even under `dtype=str`; the engine
> coerces with `isinstance` checks. Don't slice a possibly-NaN field without guarding it.
