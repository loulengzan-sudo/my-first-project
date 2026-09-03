# MODE 9 — Distillation (for very large corpora)

When even a local LLM pass over N documents is too slow/costly, annotate a stratified subset
with the validated LLM, then **distill** to a cheap classifier that scores the rest on-device
for ~free.

## When to use
- N in the tens of millions, or repeated re-scoring needed, or a strict compute budget.
- The LLM annotator has passed MODE 7 (distillation inherits its ceiling — never better than the
  teacher).

## Command
```bash
python3 assets/annotate_engine.py distill \
  --labels output/annotations/llm_labels.csv --sample output/tables/annotation_sample.csv \
  --text-cols title,description,tags --id-col video_id --target-col relevance \
  --gold output/tables/devset_gold.csv \
  --corpus corpus_spec.json --target ON_TOPIC \
  --model-out output/models/distilled.joblib --out output/tables/labels_full.csv
```
- Trains TF-IDF + regularized logistic regression (jieba-aware for CJK) on the LLM labels.
- If `--gold` given, reports **distilled-vs-gold κ / F1** before final fit — the distilled model
  must clear your bar too, else annotate more with the LLM.
- Streams the full corpus and writes `id,pred`. The target subset = `pred==TARGET`.

## Stronger distillers (when TF-IDF fails the gate)

TF-IDF+LR is the fast default but tops out on semantic multi-class tasks. Ranked by what actually
cleared the gate on a 6-way relevance task (production run; see `references/local-model-scale-lessons.md`
§4): **fine-tuned multilingual BERT (κ=0.77) > frozen embeddings + LR (κ=0.71) > TF-IDF (κ=0.61,
failed)**. Two additional distillers ship for this:

```bash
# (a) fine-tuned multilingual BERT (XLM-RoBERTa) — strongest; needs a GPU + transformers/torch
python3 assets/finetune_distill.py train --labels llm_labels.csv --sample sample_with_text.csv \
   --gold devset_gold.csv --out-dir output/models/relevance_ft --epochs 3 \
   [--target-col discourse_frame --filter-col relevance --filter-val ON_TOPIC] [--oversample]
python3 assets/finetune_distill.py apply --model-dir output/models/relevance_ft \
   --sample corpus_all.csv --out labels_full.csv --shard $i --nshards $N

# (b) frozen multilingual embeddings (bge-m3) + logistic head — robust with few labels, no fine-tune
python3 assets/distill_embed.py train --labels llm_labels.csv --sample sample_with_text.csv \
   --gold devset_gold.csv --out-dir output/models
python3 assets/distill_embed.py apply --model output/models/relevance_distill_embed.joblib \
   --sample corpus_all.csv --out labels_full.csv --shard $i --nshards $N
```
Both hold gold OUT of training, report distilled-vs-gold κ (gate-exit code 2 on fail), and score the
full corpus (sharded). Column defaults are `video_id`/`title`/`description` — adapt for other corpora.

**Hard-won caveats:** (1) the **teacher caps the student** — a nuanced 7-way task distilled from a
κ≈0.70 teacher topped out at κ≈0.67; distill the high-teacher-κ variables, keep the LLM for the hard
ones. (2) **Tuning ≠ more data** — oversampling + more epochs can *overfit and regress* κ; if the miss
is small, the real fix is usually more/better teacher labels. (3) Score on the **same trimmed text**
the student trained on. See `references/local-model-scale-lessons.md` §4.

## DSL — using predicted labels in a regression (bias correction)
Predicted labels carry error; plugging them into a downstream regression **biases** the
coefficient. Apply **design-supervised learning** (Egami et al.): use a *random* human/gold
subset (≥ ~200) to bias-correct the estimate. Report the corrected estimate vs. the naive
plug-in. Never treat predicted labels as ground truth in inference without this.

## Report
Distilled κ vs gold, class distribution over the full corpus, and the LLM-labeled fraction
(what the classifier learned from). This is a *measurement-error* pipeline — say so.
