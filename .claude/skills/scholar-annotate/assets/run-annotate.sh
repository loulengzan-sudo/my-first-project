#!/bin/bash
# Headless wrapper for a long full-corpus scale run (MODE 8). Resumable — safe to re-run.
# Usage: bash run-annotate.sh <manifest.json> [extra engine args, e.g. --shard 0 --nshards 4]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
MAN="${1:?usage: run-annotate.sh <manifest.json> [engine args]}"; shift || true
LOG="${OUTPUT_ROOT:-output}/logs/annotate-run-$(date +%Y%m%d-%H%M%S).log"; mkdir -p "$(dirname "$LOG")"
echo "manifest=$MAN  log=$LOG"
nohup python3 "$HERE/annotate_engine.py" annotate --manifest "$MAN" --resume "$@" > "$LOG" 2>&1 &
echo "started PID $! — follow with:  tail -f $LOG"
