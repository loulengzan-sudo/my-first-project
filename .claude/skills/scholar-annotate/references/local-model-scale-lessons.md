# Field Lessons — Scaling a LOCAL reasoning model on HPC

Hard-won findings from a real MODE 6→9 production run: a 6-way topic-relevance label plus a 7-way
discourse frame over ~10M+ rows of scraped platform metadata, using a **local GLM-class reasoning
model at Q3 quantization** on a SLURM cluster (4×RTX-PRO-6000 and 4×H200 nodes). These are the
things that were NOT obvious up front and cost the most time. Read this before committing GPU-days
to a local-model MODE 8.

---

## 1. The throughput wall is the model's DECODE, and reasoning models make it worse

- Modern local models (GLM-4.5/5, Qwen-QwQ, DeepSeek-R1, …) are **reasoning models**: they emit a
  long internal thinking trace *before* the answer. Server logs show `reasoning-budget: activated`
  and `eval time = … / 800–1100 tokens`. At Q3 across 4 GPUs, **decode ran ~4–9 tok/s**, so a single
  row took **60–150 s**. With `--parallel 8–16` that's only **~0.1–0.4 rows/s/node**.
- **Prefill is NOT the bottleneck** — llama.cpp caches the shared prefix (system + few-shot), so
  per-row prompt-eval is ~200–380 tokens (~1 s) even with a 2,700-token prompt. The cost is entirely
  the long generation × slow decode.
- **Implication:** for a corpus of millions on a local reasoning model, a direct full pass is often
  **days-to-weeks per the QOS you can hold**. Budget for this in MODE 1, and plan MODE 9 (distill)
  from the start — do not assume "local = cheap enough to brute-force."

## 2. Cutting output tokens: what works and what doesn't

Tried, in order, to shrink the ~600–1,100 output tokens/row (decode is linear in output length):

| Lever | Effect | Verdict |
|---|---|---|
| Drop the free-text `rationale` field (codebook) | ~220 → ~40 *for a non-reasoning path* | Big win when the model isn't reasoning |
| DSPy `Predict` instead of `ChainOfThought` | **~no change** on a reasoning model | The model reasons internally regardless of the DSPy module |
| `--reasoning-budget 0` (llama.cpp) | caps the 1,000-token spikes, but the model still emits ~70–200 tokens of preamble | Partial; not a 10× |
| DSPy `JSONAdapter` vs default `ChatAdapter` | ~200 → ~70–100 tokens (compact JSON vs `[[ ## field ## ]]` markers) | ~2× — real but modest |
| `--max-tokens 100` hard cap | truncates → **~12% invalid-JSON parse failures** | Too tight; raise to ~150 |

**Net:** stacked, these give ~2×, not 10×. A local reasoning model has a **hard floor** (~70–100
output tokens even for 3 labels) and slow decode. If you need real speed at scale → **distill (§4)**.

## 3. CoT bought nothing here — the DSPy *demos* did

MODE 6 took frame κ from **0.31 (hand-tuned few-shot) → 0.76 (DSPy)**. An A/B on the same held-out
split showed **ChainOfThought κ=0.705 ≈ Predict(no-CoT) κ=0.703** — the entire gain was DSPy
**selecting good demonstrations**, not the reasoning field. Two lessons:
- Don't assume ChainOfThought is worth its token cost; **A/B `Predict` vs `ChainOfThought`** on gold.
- The reason hand-tuning failed: the naive `k=2 per relevance class` few-shot showed **only 1–2 of 7
  frames**, so the never-demonstrated frames had **0 recall**. Ensure every rare class is
  demonstrated (see `build_fewshot(..., cover=<field>, cover_k=N)` in `providers.py`) — but note that
  stuffing demos can overflow the per-slot context (§5).

## 4. Distillation is the real scale answer for a slow local model (MODE 9)

When the LLM pass over N is too slow, **GLM-label a sample → train a cheap classifier → score the
rest**. Ranked by what actually worked on this task:

1. **Fine-tuned multilingual BERT (XLM-RoBERTa)** — best. Relevance: **κ=0.77 vs gold** from 38k GLM
   labels, trained in ~9 min, then scored all 1.87M in ~15 min on one GPU. Turned a ~23-day GLM pass
   into ~1 hour.
2. **Frozen multilingual embeddings + logistic reg** (BAAI/bge-m3) — κ=0.706. Good, dependency-light,
   no fine-tuning fragility; use when labels are few.
3. **TF-IDF + logistic reg** — κ=0.607, **failed the gate**. Bag-of-words isn't enough for a 6-way
   semantic task. (And its analyzer must handle CJK — see §6.)

**Distillation caveats learned the hard way:**
- **The teacher caps the student.** Frame distillation topped out at **κ≈0.67** — the GLM frame teacher
  itself is only ~0.70–0.76, and a 7-way nuanced task with rare classes (few labels) + noisy silver
  labels can't be distilled past the teacher. Relevance (6-way, cleaner, κ=0.90 teacher) distilled far
  better. **Distill the easy/high-teacher-κ variables; keep the LLM for the hard ones if you can.**
- **Tuning ≠ more data.** Oversampling rare classes + more epochs *regressed* frame κ (0.671→0.648,
  overfit). If the miss is small and the failure mode is under-predicted classes, try it — but the
  real fix is usually more labels or a better teacher, not hyperparameters.
- Silver labels are ~teacher-accurate; **hold gold OUT of training** and validate the student vs gold.
- Score on the **same trimmed text** the student trained on (train/serve skew is real).

**Two-pass pattern that this run converged on:** distill the cheap primary variable (relevance) over
all N fast, filter to the positive subset (in that run ~25% on-topic, a few hundred thousand rows), then spend the expensive
LLM (or a second distiller) only on that subset for the harder secondary variable (frame).

## 5. SLURM / llama.cpp gotchas (each cost a failed job)

- **QOS caps are PER-QOS and they stack.** `MaxJobsPU` limited us to 2 concurrent jobs on
  `b40x4-long`, but `b40x4` (base, 8h) allows 3 more and `h200x*-long` another 2 each — **submit
  disjoint shard ranges to multiple QOS lanes** to raise concurrency. Use a **fixed `NSHARDS` env**
  (not `SLURM_ARRAY_TASK_COUNT`) so one shard-set splits cleanly across partitions and checkpoints
  resume. Base (8h) shards must fit 8h or be resumable.
- **Model load dominates fine sharding.** Q3 (~320 GB) took **~18 min to load** per job. 64 tiny
  shards = ~19 h of pure load overhead. Prefer coarse shards that fit the time limit; each reload is
  expensive.
- **`-fa on`, not bare `-fa`** — newer llama.cpp requires a value (`on|off|auto`); bare `-fa` swallows
  the next arg and the server dies at startup.
- **Unique high server port.** Shared GPU nodes → port 8080 collides with other users. Derive it from
  the job id (`10000 + SLURM_JOB_ID % 40000`) and have the engine read `GLM_API_BASE` (providers.py
  now honors it) so the client hits the right port.
- **Per-slot context = `-c` ÷ `--parallel`.** `-c 65536 --parallel 16` = 4096/slot; a long
  few-shot/CoT prompt (5k tokens) overflows it and **every request 400s** (`exceeds context size`).
  Raise `-c` or lower `--parallel` for long prompts.
- **Model↔partition fit:** Q4XL (436 GB) needs 4×H200 (564 GB); Q3XL (320 GB) fits 4×RTX-6000
  (389 GB). Pick the quant your available partition can hold; the biggest quant may sit in a
  perpetually-queued partition.
- **Don't over-resume.** A transient empty `squeue` was misread as "lane died" → duplicate array jobs
  fighting for the same QOS. Verify with `sacct` before auto-resubmitting.

## 6. Data-handling robustness (LOCAL_MODE + messy corpora)

- **Giant fields break the pandas C parser** (`Buffer overflow caught`) and `usecols` doesn't help
  (the C tokenizer scans the whole line). Read pathological corpora with **stdlib `csv` +
  `csv.field_size_limit(sys.maxsize)`** (or `engine="python"`), sanitize (strip embedded
  newlines/tabs), then the cleaned output is C-parser-safe for the fast path.
- **jieba is often absent on compute nodes.** Give the TF-IDF analyzer a **CJK character-bigram
  fallback** so Chinese isn't silently dropped (jieba-blind analyzer → κ collapses).
- **`validate` must `fillna("")`** on preds/gold — blank/None predictions become NaN and
  `cohen_kappa_score` throws `'<' not supported between 'float' and 'str'`.
- **DSPy typed `Literal` outputs return `None`** on parse failure; coerce `None`→`""` before scoring,
  and expect a small failure rate (~1–12% depending on `max_tokens`) that needs retry or is counted
  as wrong.

## 7. What to put in MODE 1 (plan) for a local reasoning model

- Estimate **rows/sec/node from a real smoke** (not the token math) — reasoning + slow decode make it
  ~5–20× slower than a cloud mini model.
- Check the **QOS `MaxJobsPU` and per-partition time limits** before promising a timeline; compute
  wall-clock as `N / (rows_per_sec_per_node × concurrent_nodes_you_can_actually_hold)`.
- Default to a **distill plan** for the primary variable; reserve the direct LLM pass for a filtered
  subset and/or the hardest variable. Ship the distilled layer with an honest per-class κ caveat
  rather than waiting weeks for a marginal (0.67→0.76) gain on a secondary variable.
