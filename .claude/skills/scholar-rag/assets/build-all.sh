#!/bin/bash
# build-all.sh — one resumable job: OA-fetch + finish vector build + full GraphRAG.
#
# Stages A-D finish the searchable vector DB (the high-value part); Stage E is the
# LLM-bound GraphRAG (hours, resumable per doc). NOT `set -e`: a hiccup in one
# stage must not abort the rest. Every stage is resumable, so a kill == re-run.
#
# Usage:  bash build-all.sh            (foreground)
#         bash build-all.sh --detach   (nohup, survives the session)
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/_lib.sh"
mkdir -p "$SCHOLAR_RAG_DIR/logs"
LOG="$SCHOLAR_RAG_DIR/logs/build-all-$(date +%Y%m%d-%H%M%S).log"

if [ "${1:-}" = "--detach" ]; then
  nohup bash "$HERE/build-all.sh" > "$LOG" 2>&1 &
  echo "detached PID $! — follow:  tail -f $LOG"
  exit 0
fi

ts() { date +%H:%M:%S; }
PY="$RAG_PY"
EMAIL="${SCHOLAR_UNPAYWALL_EMAIL:-${SCHOLAR_CROSSREF_EMAIL:-research@example.com}}"

echo "[$(ts)] === A: ingest (Zotero) ==="
"$PY" "$HERE/ingest.py" zotero --with-pdf-only

echo "[$(ts)] === B: open-access fetch for missing PDFs ==="
"$PY" "$HERE/fetch.py" run --email "$EMAIL" || echo "  (fetch stage had issues; continuing)"

echo "[$(ts)] === C: extract text ==="
"$PY" "$HERE/extract.py" run

echo "[$(ts)] === D: chunk + embed ==="
"$PY" "$HERE/chunk_embed.py" run --batch 64
"$PY" "$HERE/ingest.py" status
rag_trace "build-all:vector" "ok" "vector + fetch stage complete"

echo "[$(ts)] === E: GraphRAG (seed -> extract -> build -> summarize) ==="
echo "[$(ts)] E1 seed"      ; "$PY" "$HERE/graphrag.py" seed
echo "[$(ts)] E2 extract"   ; "$PY" "$HERE/graphrag.py" extract
echo "[$(ts)] E3 build"     ; "$PY" "$HERE/graphrag.py" build
echo "[$(ts)] E4 summarize" ; "$PY" "$HERE/graphrag.py" summarize
"$PY" "$HERE/graphrag.py" status
rag_trace "build-all:graph" "ok" "graphrag stage complete"

echo "[$(ts)] === DONE ==="
