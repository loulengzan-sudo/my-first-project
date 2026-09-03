#!/bin/bash
# run-graph.sh — resumable GraphRAG build: seed -> extract -> build -> summarize.
# Run after the vector build (extract needs raw/text/*). Resumable per document
# via graph/extracted_docs.json, so a kill == re-run.
#
# Usage:  bash run-graph.sh            (foreground)
#         bash run-graph.sh --detach   (nohup, survives the session)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/_lib.sh"
mkdir -p "$SCHOLAR_RAG_DIR/logs"
LOG="$SCHOLAR_RAG_DIR/logs/graph-$(date +%Y%m%d-%H%M%S).log"

if [ "${1:-}" = "--detach" ]; then
  nohup bash "$HERE/run-graph.sh" > "$LOG" 2>&1 &
  echo "detached PID $! — follow:  tail -f $LOG"
  exit 0
fi

ts() { date +%H:%M:%S; }
echo "[$(ts)] seed"      ; "$RAG_PY" "$HERE/graphrag.py" seed
echo "[$(ts)] extract"   ; "$RAG_PY" "$HERE/graphrag.py" extract
echo "[$(ts)] build"     ; "$RAG_PY" "$HERE/graphrag.py" build
echo "[$(ts)] summarize" ; "$RAG_PY" "$HERE/graphrag.py" summarize
"$RAG_PY" "$HERE/graphrag.py" status
rag_trace "run-graph" "ok" "graphrag build complete"
echo "[$(ts)] DONE"
