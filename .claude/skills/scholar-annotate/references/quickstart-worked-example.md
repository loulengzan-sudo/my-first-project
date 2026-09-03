# Quickstart — a full worked example

End-to-end walkthrough of the whole pipeline on a representative task: measuring **topic
relevance** (is this item *about* the construct?) plus a discourse **frame**, over a large
scraped-metadata corpus, annotated by a **local open-weight model** on an HPC cluster.
Substitute your own corpus, columns, and labels — the shape stays the same.

Assume the skill assets are at `$SK` and your project root at `$PROJ`.
```bash
SK=.../.claude/skills/scholar-annotate/assets ; PROJ=/path/to/your/project
```

## 0. Setup + safety
Scraped platform metadata → **LOCAL_MODE** (treat as private). ⇒ serving strategy is forced to
`local` (on-prem model, no external transfer). Fields that are public on the source platform
(titles/descriptions/tags) may go to a cloud dual-LLM for the small dev-set gold pass (log it);
user comments or anything with PII would be anonymize-first. See `references/data-safety.md`.

## 1. Corpus spec (`corpus_spec.json`)
```json
{"category_glob": "items_by_category/*.csv",
 "big_file": "items_all.csv",
 "big_only": ["category_a","category_b","category_c"],
 "strata_col": "query_category"}
```

## 2. Profile (optional)
Run a topic/keyword profile (or `/scholar-compute` MODULE 1) → find the class prior and the
off-topic contamination. Feeds the codebook's OFFTOPIC rules.

## 3. Codebook (`codebook.md`)
Prose defining each relevance class and each frame, with an explicit decision order, plus the
schema block. A conditional field is only coded when the gating field takes a given value:
```json
{"out_fields":["relevance","frame","confidence"],
 "labels":{"relevance":["ON_TOPIC","ADJACENT","BACKGROUND","PROMOTIONAL","OFFTOPIC","UNCLEAR"],
   "frame":["frame_a","frame_b","frame_c","frame_d","frame_e","frame_f","none"],
   "confidence":["high","medium","low"]},
 "conditional":{"frame":{"only_if":{"relevance":"ON_TOPIC"},"else":"none"}}}
```

## 4. Dev sample (stratified + rare-class over-sampled)
`lexicon.json` maps a class to seed terms that over-sample it, e.g.
`{"ON_TOPIC":["<seed term 1>","<seed term 2>", ...]}` — use terms diagnostic of your construct.
```bash
python3 $SK/annotate_engine.py sample --corpus corpus_spec.json \
  --id-col item_id --text-cols title,description,tags --strata query_category \
  --n 3000 --pool 500 --oversample lexicon.json --target ON_TOPIC \
  --out output/tables/devset_3k.csv
```

## 5. Gold via dual-model Batch API (Claude + GPT → agreement)
Two manifests differing only in `provider`; each `"strategy":"batch"`:
```json
{"input":"output/tables/devset_3k.csv","id_col":"item_id","text_cols":["title","description","tags"],
 "codebook":"codebook.md","out_dir":"output/gold_claude",
 "provider":{"strategy":"batch","provider":"anthropic","model":"claude-opus-4-8","key_env":"ANTHROPIC_API_KEY"}}
```
```bash
ANTHROPIC_API_KEY=… python3 $SK/annotate_engine.py annotate --manifest gold_claude.json
OPENAI_API_KEY=…    python3 $SK/annotate_engine.py annotate --manifest gold_gpt.json   # provider=openai
# reconcile → devset_gold.csv (agreement) + disagreements (see gold-annotation.md), report cross-model κ
```

## 6. Optimize the prompt (DSPy)
```bash
python3 $SK/dspy_optimize.py --gold output/tables/devset_gold.csv --codebook codebook.md \
  --text-cols title,description,tags --provider openai --model gpt-4.1-mini \
  --out output/models/annotator_program.json
```

## 7. HARD GATE — validate the deployment annotator vs gold
First annotate the gold rows with the *deployment* model (the local one you will scale with), then:
```bash
python3 $SK/annotate_engine.py validate --pred output/tables/local_gold_pred.csv \
  --gold output/tables/devset_gold.csv --on relevance,frame --gate 0.70 \
  --out output/tables/validation_report.json    # exit 2 blocks scaling if κ<0.70
```
Validate the model you will actually deploy — a κ earned by a cloud model does not transfer to the
local model that annotates the corpus.

## 8. Scale — annotate the full corpus on the cluster (local model)
Slim + ship, then run the sbatch (server + engine, job-array shardable):
```bash
python3 $SK/hpc/prep-mirror.py --corpus corpus_spec.json --id-col item_id \
  --text-cols title,description,tags --out-root ./xfer
bash $SK/hpc/rsync-helper.sh                       # edit USER/HOST/DEST
# on the cluster (run-manifest.json has provider.strategy=local, api_base=localhost):
SMOKE=500 sbatch skills/scholar-annotate/assets/hpc/annotate.sbatch
sbatch skills/scholar-annotate/assets/hpc/annotate.sbatch   # full; --array=0-3 to fan out
```
Run manifest (`run-manifest.json`):
```json
{"input":"corpus via engine iterator","id_col":"item_id","text_cols":["title","description","tags"],
 "strata_col":"query_category","codebook":"codebook.md","out_dir":"data/annotations",
 "provider":{"strategy":"local","provider":"local","model":"<local-model-id>","api_base":"http://127.0.0.1:8080/v1"},
 "fewshot":{"gold":"output/tables/devset_gold.csv","k":2},"workers":16,"shards":64}
```

## 9. (Optional) Distill for re-scoring / DSL
```bash
python3 $SK/annotate_engine.py distill --labels data/annotations/annot*.csv \
  --sample output/tables/annotation_sample.csv --corpus corpus_spec.json \
  --text-cols title,description,tags --target-col relevance --target ON_TOPIC \
  --gold output/tables/devset_gold.csv --out output/tables/labels_full.csv
```

## 10. Report
Merge shards → parquet; `relevance==ON_TOPIC` is the analyzable sub-corpus and `frame` is the
substantive lens. Write the Methods/Results packet (κ, cost, model id + date, disclosures — see
`references/reporting-templates.md`) and hand the labeled variable to `/scholar-analyze`.
