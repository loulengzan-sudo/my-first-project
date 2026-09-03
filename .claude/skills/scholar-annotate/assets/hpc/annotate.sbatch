#!/bin/bash
# ============================================================
# scholar-annotate — full-corpus annotation on an HPC node with a LOCAL model.
# Starts an OpenAI-compatible server (llama.cpp llama-server | vLLM | ollama) on the node's
# GPUs, then runs the engine against http://127.0.0.1:PORT/v1. Data never leaves the node.
# Horizontal scale: submit as a JOB ARRAY (#SBATCH --array=0-N) — task i annotates shard i.
#
# EDIT the >>> SET values, then:  sbatch assets/hpc/annotate.sbatch
# Smoke test:  SMOKE=500 sbatch assets/hpc/annotate.sbatch
# ============================================================
#SBATCH --job-name=annotate
#SBATCH --nodes=1
#SBATCH --gres=gpu:4
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --time=24:00:00
#SBATCH --output=annotate-%A_%a.out
##SBATCH --partition=>>> SET
##SBATCH --account=>>> SET
##SBATCH --array=0-3

set -euo pipefail
# >>> SET
export PROJ="${PROJ:-$PWD}"                       # project root (staged data + manifest live here)
PYBIN="${PYBIN:-python3}"                          # a python with the engine's requirements
ENGINE="${ENGINE:-$PROJ/skills/scholar-annotate/assets/annotate_engine.py}"  # or wherever assets live
MANIFEST="${MANIFEST:-$PROJ/run-manifest.json}"    # engine annotate manifest (provider.strategy=local)
SERVER="${SERVER:-llama}"                          # llama | vllm | ollama
MODEL_PATH="${MODEL_PATH:->>> SET path to GGUF or HF model}"
MODEL_NAME="${MODEL_NAME:-local-model}"
NPAR="${NPAR:-16}"

SHARD="${SLURM_ARRAY_TASK_ID:-0}"; NSHARDS="${SLURM_ARRAY_TASK_COUNT:-1}"; PORT=$((8080+SHARD))
echo "[`date`] node=$SLURMD_NODENAME shard=$SHARD/$NSHARDS server=$SERVER port=$PORT"

case "$SERVER" in
  llama)
    module load llama.cpp 2>/dev/null || true; module load cuda 2>/dev/null || true
    llama-server -m "$MODEL_PATH" -ngl 999 -sm row -fa -c 65536 --parallel "$NPAR" \
       --host 127.0.0.1 --port "$PORT" & SPID=$! ;;
  vllm)
    python3 -m vllm.entrypoints.openai.api_server --model "$MODEL_PATH" \
       --tensor-parallel-size 4 --host 127.0.0.1 --port "$PORT" & SPID=$! ;;
  ollama)
    export OLLAMA_HOST="127.0.0.1:$PORT" OLLAMA_NUM_PARALLEL="$NPAR"
    ollama serve & SPID=$! ;;
esac
trap 'kill $SPID 2>/dev/null || true' EXIT

echo "[`date`] waiting for server /health ..."
for i in $(seq 1 240); do
  curl -sf "http://127.0.0.1:${PORT}/v1/models" >/dev/null 2>&1 && { echo ready; break; }
  sleep 10; kill -0 $SPID 2>/dev/null || { echo "server died"; exit 1; }
done

export GLM_API_BASE="http://127.0.0.1:${PORT}/v1" OPENAI_BASE_URL="http://127.0.0.1:${PORT}/v1"
LIM=""; [ -n "${SMOKE:-}" ] && LIM="--limit ${SMOKE}"
"$PYBIN" "$ENGINE" annotate --manifest "$MANIFEST" --resume --shard "$SHARD" --nshards "$NSHARDS" $LIM
echo "[`date`] shard $SHARD/$NSHARDS done — re-submit to resume if incomplete."
