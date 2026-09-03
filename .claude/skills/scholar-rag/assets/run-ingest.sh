#!/bin/bash
# run-ingest.sh — resumable end-to-end corpus build (ingest -> extract -> embed).
#
# Safe to re-run: each stage only touches documents not yet at that stage, so a
# killed run resumes where it stopped. Designed for a long unattended full-
# library build; logs to $SCHOLAR_RAG_DIR/logs/.
#
# Usage:
#   bash run-ingest.sh [--source zotero|folder] [--folder DIR]
#                      [--limit N] [--ocr] [--no-embed] [--batch 48]
#   bash run-ingest.sh --background            # detach with nohup
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/_lib.sh"

SOURCE="zotero"; FOLDER=""; LIMIT=""; OCR=""; NOEMBED=0; BATCH="48"; BG=0
while [ $# -gt 0 ]; do
  case "$1" in
    --source) SOURCE="$2"; shift 2;;
    --folder) FOLDER="$2"; shift 2;;
    --limit)  LIMIT="--limit $2"; shift 2;;
    --ocr)    OCR="--ocr"; shift;;
    --no-embed) NOEMBED=1; shift;;
    --batch)  BATCH="$2"; shift 2;;
    --background) BG=1; shift;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

LOG="$SCHOLAR_RAG_DIR/logs/ingest-run-$(date +%Y%m%d-%H%M%S).log"
mkdir -p "$SCHOLAR_RAG_DIR/logs"

if [ "$BG" = "1" ]; then
  # relaunch self detached, without --background, streaming to LOG
  args=(--source "$SOURCE"); [ -n "$FOLDER" ] && args+=(--folder "$FOLDER")
  [ -n "$LIMIT" ] && args+=($LIMIT); [ -n "$OCR" ] && args+=(--ocr)
  [ "$NOEMBED" = "1" ] && args+=(--no-embed); args+=(--batch "$BATCH")
  nohup bash "$HERE/run-ingest.sh" "${args[@]}" > "$LOG" 2>&1 &
  echo "started PID $! — follow with:  tail -f $LOG"
  exit 0
fi

echo "[run-ingest] py=$RAG_PY store=$SCHOLAR_RAG_DIR log=$LOG"
if [ ! -x "$RAG_VENV/bin/python" ]; then
  echo "[run-ingest] venv missing — run setup-venv.sh first" >&2; exit 3
fi

echo "== stage 1: ingest ($SOURCE) =="
if [ "$SOURCE" = "folder" ]; then
  [ -n "$FOLDER" ] || { echo "folder source needs --folder DIR" >&2; exit 2; }
  "$RAG_PY" "$HERE/ingest.py" folder "$FOLDER" $LIMIT
else
  "$RAG_PY" "$HERE/ingest.py" zotero --with-pdf-only $LIMIT
fi
rag_trace "ingest" "ok" "source=$SOURCE"

echo "== stage 2: extract =="
"$RAG_PY" "$HERE/extract.py" run $LIMIT $OCR
rag_trace "extract" "ok" "ocr=${OCR:-off}"

if [ "$NOEMBED" = "0" ]; then
  echo "== stage 3: chunk + embed =="
  "$RAG_PY" "$HERE/chunk_embed.py" run --batch "$BATCH"
  rag_trace "embed" "ok" "batch=$BATCH"
fi

echo "== done =="
"$RAG_PY" "$HERE/ingest.py" status
