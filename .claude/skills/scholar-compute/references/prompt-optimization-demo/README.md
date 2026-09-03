# Prompt-Optimization Demo (MODULE 7, Step 4b)

A minimal, **self-contained, runnable** companion to *Step 4b — Prompt Optimization for
Annotation* in [`../module-07-llm-analysis.md`](../module-07-llm-analysis.md). It turns the
section's claim — *optimizing the instruction beats selecting demonstrations on a codebook
that contradicts the model's prior* — into a measured result you can reproduce.

Both demos run **fully local via [Ollama](https://ollama.com)** (`qwen2.5:7b`, a 7B model) —
**no API key, no cost.** They share the *same task, data, and codebook*, so DSPy and TextGrad
are directly comparable.

## The task
A deliberately counterintuitive binary codebook (`CODEBOOK.md`, identical in both demos):
only transactional/official actions are `CAPITAL`; all information-seeking and self-directed
learning is `LEISURE`. The "contradicts the model's prior" cases (e.g. *"Looking up the
symptoms of high blood pressure"* → `LEISURE`, not `CAPITAL`) are what a good optimizer must
fix. Metric = exact match on the gold label (the κ gate, per-item).

## Result (`qwen2.5:7b`, local; 20 train / 8 val / 12 test)

| program | what it tuned | test accuracy |
|---|---|---|
| baseline (zero-shot) | — | **0.667** |
| DSPy · `BootstrapFewShot` | demonstrations | 0.667 (+0.000) |
| DSPy · `MIPROv2(auto="light")` | **instruction** | **0.750** (+0.083) |
| TextGrad (1 step) | **instruction** | **0.750** (+0.083) |

**The lesson:** the two instruction optimizers each flipped a hard, prior-contradicting item
and moved the score; `BootstrapFewShot` added 8 demonstrations but all *easy* ones, so it
never touched the hard cases. Modest numbers (a 7B model, 20 items, a few steps) — the point
is the *mechanism*, not a benchmark. Verified outputs are checked in under each `outputs/`.

## Run it

```bash
# prerequisites: `ollama serve` running, then `ollama pull qwen2.5:7b`
cd dspy-demo     && pip install -r requirements.txt && python dspy_demo.py
cd ../textgrad-demo && pip install -r requirements.txt && python make_data.py && python optimize_prompt.py
```

See each subfolder's own `README.md` for details. TextGrad is slow locally (~20 min here);
DSPy finishes in well under a minute.

## Files
- `dspy-demo/` — `dspy_demo.py`, `CODEBOOK.md`, `synthetic_data.csv`, `requirements.txt`,
  `outputs/` (compiled prompts, per-item predictions, `summary.json`, `run_log.txt`).
- `textgrad-demo/` — `optimize_prompt.py`, `make_data.py`, `CODEBOOK.md`, `synthetic_data.csv`,
  `requirements.txt`, `outputs/` (initial/optimized prompt, predictions, `summary.json`,
  `trajectory.json`, `run_log.txt`).

*A minimal, reproducible teaching demo for MODULE 7, Step 4b — safe to run, ship, and cite.*
