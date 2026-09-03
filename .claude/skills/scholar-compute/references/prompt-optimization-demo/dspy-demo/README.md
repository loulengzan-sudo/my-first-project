# DSPy demo — "Signatures, not strings," compiled

A runnable demonstration of DSPy's **"signatures, not strings"** idea — and of what a
*compiled prompt* actually looks like. It uses the **same task, data, and local model** as
the TextGrad demo next door, so the two are directly comparable. Runs **fully local via
Ollama** (litellm `ollama_chat/...`) — no API key, no cost.

## The DSPy idea

You never write a prompt string. You **declare** a typed `Signature` (inputs → outputs), pick
a `Module` (`dspy.Predict`), and an **optimizer compiles** a prompt for it against a metric.
We test two optimizers that optimize *different things*:

- **`BootstrapFewShot`** — keeps the instruction, **selects demonstrations** (few-shot
  examples) by running the program and keeping traces that pass the metric.
- **`MIPROv2`** — **proposes better instructions** (and demos), then Bayesian-searches over
  the candidates on a validation split. This is the DSPy analogue of what TextGrad does.

Metric = exact match on the gold label (same narrow codebook as the TextGrad demo — only
transactional/official actions are CAPITAL; all information-seeking and self-directed
learning is LEISURE; see `CODEBOOK.md`).

## Result (qwen2.5:7b, local)

| program | test accuracy | what it changed |
|---|---|---|
| baseline (zero-shot `Predict`) | **0.667** | — |
| `BootstrapFewShot` | **0.667** (+0.000) | added 8 demos — but all *easy* ones |
| `MIPROv2` (auto="light") | **0.750** (+0.083) | **rewrote the instruction** |

**Why BootstrapFewShot didn't move:** it bootstraps demonstrations from traces the
zero-shot model *already gets right*. Those are the easy transaction/entertainment items — it
never selected a single counterintuitive "info-seeking → LEISURE" example, so the few-shot
prompt just reinforces intuition. Demo-bootstrapping can't teach a rule the model is
currently getting wrong. (See `outputs/bootstrap_demos.json` — every demo is a transaction or
a game.)

**Why MIPROv2 did:** it *proposed a new instruction* and searched for the best on the val
split. Its winner (`outputs/mipro_instruction.txt`) reframes CAPITAL as "capital management
or financial transactions" with two inline examples — narrower than the vague baseline —
which flipped *"watching a documentary explainer"* to LEISURE. (Note the proposer, a local
7B, actually mis-summarized the codebook as *financial*; it still helped, but a stronger
proposer model would propose a cleaner rule.)

## The cross-demo takeaway (DSPy ↔ TextGrad)

On this deliberately **counterintuitive** codebook, on the same local 7B:

| approach | optimizes | Δ test acc |
|---|---|---|
| DSPy · BootstrapFewShot | the **demonstrations** | +0.000 |
| DSPy · MIPROv2 | the **instruction** (+demos) | **+0.083** |
| TextGrad | the **instruction** | **+0.083** |

**Editing the instruction is what moved the needle; selecting demonstrations did not** —
because the task's boundary contradicts the model's prior, and bootstrapped demos only echo
that prior. Both instruction-optimizers landed the same gain by different search strategies
(TextGrad: a natural-language "gradient" rewrite; MIPRO: propose-and-Bayesian-search).

*Fairness note:* this task was designed adversarially (to give an instruction-optimizer
headroom). On ordinary tasks BootstrapFewShot is often the cheapest win; here it is the
teaching foil. Bigger `auto=` budgets, a stronger proposer/task model, or more data would all
raise DSPy's ceiling.

## Run it

```bash
ollama serve &                         # then: ollama pull qwen2.5:7b
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt        # dspy + optuna (MIPRO's search backend)
python dspy_demo.py                     # ~4 min (BootstrapFewShot + MIPROv2 light)
```

The dataset is shared with the TextGrad demo (`synthetic_data.csv`, `CODEBOOK.md` are copies).

## Files

| file | what |
|---|---|
| `dspy_demo.py` | the demo: Signature + Predict + BootstrapFewShot + MIPROv2 |
| `synthetic_data.csv`, `CODEBOOK.md` | shared data (identical to the TextGrad demo) |
| `outputs/bootstrap_demos.json` | the demos BootstrapFewShot selected (all easy) |
| `outputs/mipro_instruction.txt` | the instruction MIPROv2 wrote |
| `outputs/rendered_prompt_{baseline,bootstrap,mipro}.txt` | **the fully-assembled prompt DSPy actually sends** (instruction + demos + field formatting) — "what a compiled prompt looks like" |
| `outputs/predictions_{baseline,bootstrap,mipro}.csv` | per-item test predictions |
| `outputs/summary.json`, `outputs/run_log.txt` | metrics + full log |
