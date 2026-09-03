# MODE 5 — Build the Gold Set

Turn the dev sample into trusted labels. Combine any of:

## (A) Multi-model LLM gold (fast, cheap, scalable)
Annotate with **two strong models** (e.g. Claude + GPT) via Batch APIs; **agreement = gold**,
disagreement → adjudication. Inter-model **Cohen κ** is your first reliability signal.

```bash
# submit both as managed batches (strategy=batch in each manifest)
python3 assets/annotate_engine.py annotate --manifest claude_dev.json   # provider.provider=anthropic
python3 assets/annotate_engine.py annotate --manifest gpt_dev.json      # provider.provider=openai
# reconcile ANY number of runs (different models AND/OR repeats) → gold + pairwise κ:
python3 assets/gold_reconcile.py --inputs annot_claude.csv annot_gpt.csv \
  --id-col video_id --on relevance,discourse_frame --mode unanimous \
  --attach devset.csv --text-cols title,description,tags \
  --gold devset_gold.csv --disagree disagreements.csv
```

### Only ONE provider available? Use ≥2 models of it.
If you have a single API key (e.g. only `OPENAI_API_KEY`), build the agreement gold from **two+
models of that same provider** — e.g. `gpt-5.6` + `gpt-4.1`. Run one manifest per model (change
`provider.model`), then `gold_reconcile.py` over both. Model diversity still filters the boundary
cases a single annotator flips on.

### Damp non-determinism with multi-run majority vote.
Some models forbid `temperature=0` — **GPT-5 / o-series force the default (1)**. Measured on this
task, `gpt-5.6` self-agreement is relevance κ ≈ 0.88 (~8% of items flip on boundary cases). To cut
that noise, run the SAME model **K times** and majority-vote: pass the K outputs as K `--inputs`
with `--mode majority`. Combine with multi-model for the cleanest gold. (The *deployment* model —
a local GLM via llama.cpp — supports `temperature=0` and stays fully reproducible regardless.)
Rules: **dual-LLM agreement is silver, not human-gold.** Eyeball a slice of the agreed set
AND the disagreements before trusting it. Human-adjudicate disagreements when the construct is
contested.

## (B) Human gold (the standard)
2 coders double-code the template from MODE 4 per the codebook → **Krippendorff's α (nominal)**
on each field; α ≥ 0.70 before consensus becomes gold. Reconcile disagreements by discussion;
unresolved → drop (or `UNCLEAR`).

## (C) Best practice
Use LLM gold to *build fast*, then validate it against a small **human** subset (κ). Report both
the human α (coder reliability) and the LLM–human κ (instrument validity). Archive model ids +
date + prompts (Lin & Zhang replicability). Batch APIs run temperature=0.

**Data transfer:** LLM gold on a cloud API sends the dev rows externally — small and usually
public, but log it (MODE 0 disclosure). For restricted data, build gold with a *local* model or humans.
