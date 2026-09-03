# MODE 6 — DSPy Prompt Optimization

Stop hand-tuning prompts. `dspy_optimize.py` builds a **typed ChainOfThought** annotator from
the codebook and few-shot-optimizes it against the gold set.

## Why DSPy here
- **Typed signature** from the codebook's `out_fields`/`labels` → structured outputs, fewer
  parse failures.
- **ChainOfThought** → each label carries a reasoning trace (auditable = Lin & Zhang validity).
- **BootstrapFewShot / MIPRO** → the framework *selects* the demonstrations that maximize gold
  accuracy, instead of you guessing which examples to paste.

## Command
```bash
python3 assets/dspy_optimize.py \
  --gold output/tables/devset_gold.csv --codebook codebook.md \
  --text-cols title,description,tags \
  --provider openai --model gpt-4.1-mini \
  --out output/models/annotator_program.json --demos 8
# local server:  --provider local --api-base http://127.0.0.1:8080/v1 --model your-model
```
Prints held-out exact-match on the first two fields and a program hash. temperature=0.

## Deploy the optimized prompt at scale
The compiled program is a JSON of the system prompt + selected demos. Two ways to use it in
MODE 8:
1. **DSPy-native** (small/mid runs): `assets/dspy_run.py --program annotator_program.json …` loads
   the compiled program and annotates a CSV or corpus-spec, threaded/sharded/resumable, writing the
   same CSV shape as the engine — so what you validated is exactly what runs.
2. **Frozen-prompt** (huge/local runs): export the optimized system prompt + demos into the
   engine's codebook/few-shot so the robust batch/local runner deploys it without DSPy per-call
   overhead. Recommended for the full-corpus pass — optimize with DSPy, deploy the frozen prompt.

## Findings on LOCAL reasoning models (GLM-5.2 etc.) — see `local-model-scale-lessons.md`
- **CoT often buys nothing; the DEMOS do.** A/B `--no-cot` (`dspy.Predict`) vs `ChainOfThought` on
  gold — on a reasoning model they scored the *same* κ (0.705 vs 0.703), because the model reasons
  internally regardless of the DSPy module. Frame κ went 0.31→0.76 from DSPy's **demo selection**,
  not the reasoning field.
- **Cut per-row tokens for throughput:** `dspy_run.py --json-adapter` (compact JSON vs verbose
  `[[ ## field ## ]]` ChatAdapter markers, ~200→~70 tokens) and `--max-tokens N` (but too low
  truncates → JSON parse failures). Stacked with a low llama.cpp `--reasoning-budget`, expect ~2×,
  not 10× — a reasoning model has a hard output-token floor and slow decode.

## Archive (replicability)
Save the program + hash + model id + date. Re-optimize only if the codebook changes.
