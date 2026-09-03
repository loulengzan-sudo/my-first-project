# MODE 8 — Scale Engine (full-corpus annotation)

Only after MODE 7 passes. All scaled work runs through `annotate_engine.py annotate` — never a
hand-rolled loop. Driven by a run manifest.

## Run manifest
```json
{"input":"corpus_or_sample.csv","id_col":"video_id","text_cols":["title","description","tags"],
 "strata_col":"query_category","codebook":"codebook.md","out_dir":"output/annotations",
 "provider":{"strategy":"local","provider":"local","model":"glm-5.2",
             "api_base":"http://127.0.0.1:8080/v1","key_env":"GLM_API_KEY"},
 "fewshot":{"gold":"output/tables/devset_gold.csv","k":2},"workers":16,"shards":64}
```

## Three strategies (`provider.strategy`)
- **`batch`** — managed OpenAI/Anthropic Batch API. 50% cheaper, async (minutes–24h). The engine
  submits, polls, and collects; `provider.provider` = `openai|anthropic`.
- **`async`** — concurrent live requests (`workers` threads). For mid-size, need-it-now runs.
- **`local`** — async against an OpenAI-compatible on-prem server (llama.cpp/ollama/vLLM). The
  privacy-preserving path; pair with `assets/hpc/` for SLURM.

## Durability (built in)
- **Resumable** — per-id checkpoint (`processed*.txt`); re-run skips done. In-run **stable-hash
  dedup** (a corpus mirror often has duplicate ids — don't pay to re-annotate them).
- **Sharded output** — `annot_###.csv` (no single giant file). **Job-array sharding**
  (`--shard i --nshards N`) splits the corpus across cluster nodes with disjoint, contention-free
  files.
- **Cost ledger** + `--dry-run` pre-flight estimate.

## HPC (local model on GPUs)
`assets/hpc/annotate.sbatch` starts the server (llama.cpp/vLLM/ollama), waits for `/v1/models`,
then runs the engine against `localhost`. Scale horizontally with `#SBATCH --array=0-N`.
Transfer a slim corpus mirror first: `assets/hpc/prep-mirror.py` + `rsync-helper.sh`.
```bash
SMOKE=500 sbatch assets/hpc/annotate.sbatch   # smoke-test, eyeball output
sbatch assets/hpc/annotate.sbatch             # full run, resumable
```

## Script freeze during a live DAG — pin by CONTENT, never by permission

A multi-day DAG reads its scripts long after submission. If anything edits a script mid-run, the
node that reads it next dies — or worse, succeeds against half-old code.

**Observed:** a DSL node failed 37s in with `Error: unexpected symbol`; the script's mtime was
**16 seconds before the failure**. An agent was editing an analysis script while a 12-hour DAG
was reading it. Cascade: six nodes. A sibling audit found its own margin was 10 minutes on one
file — it held by luck.

**The obvious fix is wrong, and was disproved from the author's own hands.** Copying `scripts/`
into `_scriptpin_<id>/` and running `chmod -R a-w` prevents nothing: `chmod a-w` is **advisory
against the file's owner**, and every job in the DAG runs as the owner. The immutability test
written to *demonstrate* the pin instead modified the pinned file (`pin_mtime` moved forward),
and `touch` on a second pinned file SUCCEEDED. What actually protected the resubmitted job was a
parse-check job printing `sha=7b174349c0f34889` and the job executing that same content. **The
chmod was decoration; the digest was the guarantee.**

> **A pin must be verified by content at job start, never trusted by permission.**
>
> - **Strong form** — a content-addressed store: the pinned path *is* the digest
>   (`_pinned/<sha256>/`), so "has it moved?" becomes unanswerable-by-construction rather than
>   checked.
> - **Weak form** — the runner re-hashes the entry script and its first-party imports
>   immediately before `exec` and refuses on mismatch.
> - **An unset pin must REFUSE, not pass.** Otherwise every hand-submitted job silently opts
>   out — and hand-submitted jobs are exactly the ones most likely to be racing an edit.

Pinning prevents an *accident*, which is the failure mode that actually killed the node. It
prevents nothing an owner can do deliberately. Verification is the load-bearing half; pinning is
the convenience that makes verification stable. (Registry entry: `_shared/defect-class-registry.md`
DC-07 — and note the pin is itself an instance of DC-02, a guard verified in the state it was
designed for and not in the state it would meet.)

## Build-vintage guards — `require_file` checks presence; the defect is vintage

Wrapper preflights typically assert that an input **exists**. Existence is not the property you
need: a stale artifact from a previous build satisfies it.

**Observed:** a resubmission of an FDR node was refused by a track that noticed the required
input existed *from the previous build*. The node would not have failed — it would have
**succeeded**, writing a freshly-timestamped corrections table holding families 1/3/4 from build
63032 and family 2 from the current build. Internally mixed, mtime-current, and invisible to per-row
vintage machinery because the registry never reads inside it.

```bash
# NOT this — presence only
require_file() { [ -f "$1" ] || { echo "FATAL: required input missing: $1" >&2; exit 2; }; }

# This — presence PLUS the artifact's own recorded build_id matching the current manifest
require_artifact_of_build() {   # <path> <expected_build_id>
  [ -f "$1" ] || { echo "FATAL: required input missing: $1" >&2; exit 2; }
  got=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]+".design.json")).get("build_id",""))' "$1" 2>/dev/null)
  [ -n "$got" ] || { echo "FATAL: $1 carries no build_id — vintage unverifiable" >&2; exit 2; }
  [ "$got" = "$2" ] || { echo "FATAL: $1 is from build $got, current build is $2" >&2; exit 2; }
}
```

An artifact with **no** recorded build_id must fail, not pass — absent provenance and matching
provenance are different states (DC-01). Presence checks are the default idiom throughout HPC
wrapper code and every one of them has this hole; sweep for `[ -f ` preflights when auditing a
DAG. Registry entry: DC-06.

## Safe path is the default

A flag whose *absence* costs GPU-hours or swaps a measurement instrument is a trap, not a
default. One wrapper defaulted to a full-pool relabel of ~90k comments (8h, 4 GPUs, under a
*different quant* than the shipped labels) unless `AUDIT_ONLY=1` was set; the rebuild driver
simply never set it, and it was caught by an agent reading the wrapper, not by any check. Invert
such flags, or require the caller to state the choice explicitly. The engine follows this:
`--allow-unreadable` defaults to 0, so a corpus read that silently lost files fails closed.
Registry entry: DC-05.

## Hand-off
Merge shards → parquet; `relevance==TARGET` rows are the analyzable subset. Then MODE 10 (report)
or straight to `/scholar-analyze` / `/scholar-compute` on the labeled variable.
