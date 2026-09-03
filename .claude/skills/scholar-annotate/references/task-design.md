# MODE 1 — Task Design & Serving Strategy

Fix the measurement before touching a model.

## Define the measurement
- **Construct** — what latent variable are you measuring? (relevance, frame, stance, sentiment, an extracted field). Name it in one sentence.
- **Unit** — one row = one document (video/title/post/utterance/article). State it.
- **Label space** — the classes, plus optional conditional sub-codes (e.g. a `frame` only when `relevance==TARGET`). Draft it here; formalize in MODE 3 (codebook).
- **Ground truth source** — human, multi-LLM agreement, or existing labels. This becomes the gold in MODE 5.

## Choose the serving strategy (decision tree)
| Situation | Strategy | Why |
|---|---|---|
| Sensitive / `LOCAL_MODE` / PII corpus | **local** | data never leaves the machine (on-prem OpenAI-compatible server); the ONLY compliant path for restricted data |
| Public text, ≤ ~1M docs, budget for API | **batch** | managed Batch API = 50% cheaper, async, robust |
| Public text, need results now, small N | **async** | concurrent live calls |
| Huge corpus (≫ few M) any provenance | **local + distill (MODE 9)** | LLM-label a sample, distill to a cheap classifier for the rest |

Privacy dominates: if the data is restricted, `local` is forced regardless of scale/budget (see `data-safety.md`).

## Pre-flight estimate (always)
```bash
python3 assets/annotate_engine.py annotate --manifest run.json --dry-run
```
Prints docs × tokens × price → $ estimate (Batch ≈ half; local = $0 compute-only). If the full-corpus API cost is untenable → switch to `local` or add MODE 9 distillation.

## Output
A one-paragraph task spec (construct, unit, label space, gold source, strategy) + the dry-run estimate. `TRACE` the strategy decision and its rationale (privacy/scale/budget).
