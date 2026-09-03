# TextGrad demo — can you really *optimize* a prompt?

A runnable demonstration of TextGrad's **"backpropagation, in English"** idea. It answers a
concrete question — *can we actually use this to optimize a prompt?* — with a real experiment,
run **fully local via Ollama** (no API key, no cost).

## The task (same data as the DSPy demo next door)

Label a short description of an internet activity as **CAPITAL** vs **LEISURE** (a CFPS-style
digital-divide coding example). The catch: the codebook is deliberately **narrow** —
only *transactional / official / economic actions* (applying, filing, paying, booking,
sending work mail) count as CAPITAL; **all information-seeking and self-directed learning**
(reading the news, looking things up, watching a tutorial, practicing on an app) is LEISURE.

Why narrow? A 7B model already labels the *intuitive* productive-vs-recreational split at
~100% from a vague prompt — nothing to optimize. The narrow codebook creates a real gap
between the model's intuition and the target, so there is something to learn. See
`CODEBOOK.md`.

## What TextGrad does here (the mechanism)

- **loss** = a string, `CORRECT` / `INCORRECT` + *why*, judged against **gold labels**
  (`StringBasedFunction` — the rigorous, gold-label version).
- **`backward()`** = the engine writes a *textual gradient*: what to change in the prompt, and why.
- **`step()`** = the engine **rewrites the system-prompt Variable** using that gradient,
  under a constraint that keeps the one-word output format.

Protocol: vague prompt → one TGD step on a contrastive batch of train items → keep the
best checkpoint on a validation split → report accuracy on an untouched test split.

## Result (qwen2.5:7b, local, 1 step, 3 items/class)

| prompt | test accuracy |
|---|---|
| initial (vague) | **0.667** |
| TextGrad-optimized | **0.750**  (+0.083; val 0.750 → 0.875) |

TextGrad **rewrote the instruction** — it narrowed the CAPITAL definition on its own:

> *initial:*  "…CAPITAL (**capital-enhancing use**) or LEISURE (recreational use)…"
> *optimized:* "…CAPITAL (**professional or business-related use**) or LEISURE (personal or recreational use)…"

That edit correctly moved *"looking up the symptoms of high blood pressure"* from CAPITAL to
LEISURE (a fixed counterintuitive item). It did **not** fix every hard case in one step
(news / documentary / product-reviews are still mislabeled) — an honest picture of a small
local model doing one gradient step.

**Bottom line:** yes — TextGrad genuinely optimizes a prompt against gold
labels; it improved test accuracy by rewriting the instruction. The caveat holds:
one local step is a nudge, not a miracle; more steps / a stronger backward engine go further.

## Run it

```bash
# 0) a local model server
ollama serve &            # then: ollama pull qwen2.5:7b

# 1) isolated env (Python 3.12 recommended)
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt

# 2) build the synthetic data, then optimize
python make_data.py
python optimize_prompt.py            # ~20 min on a 7B (local textual gradients are slow)

# knobs
TG_MODEL=qwen2.5:7b TG_STEPS=2 TG_NPER=3 python optimize_prompt.py
```

> Use **one** model for forward+backward (default). Mixing two models makes Ollama swap
> weights on every call and is dramatically slower.

## Files

| file | what |
|---|---|
| `make_data.py` | writes the 40-item hand-labeled dataset (deterministic gold labels) |
| `synthetic_data.csv` | the data: `id,text,label,counterintuitive,split` (train/val/test) |
| `CODEBOOK.md` | the narrow labeling rule + the counterintuitive items |
| `optimize_prompt.py` | the TextGrad optimization (this is the demo) |
| `outputs/initial_prompt.txt`, `outputs/optimized_prompt.txt` | prompt before/after |
| `outputs/predictions_initial.csv`, `outputs/predictions_optimized.csv` | per-item test predictions |
| `outputs/trajectory.json`, `outputs/summary.json`, `outputs/run_log.txt` | metrics + full log |
