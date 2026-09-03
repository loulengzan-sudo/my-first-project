#!/bin/bash
# _lib.sh — shared shell helpers for the self-contained scholar-rag engine.
# Source this at the top of any scholar-rag asset shell script:
#     HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/_lib.sh"
#
# Provides: SCHOLAR_RAG_DIR, RAG_VENV, RAG_PY, ASSETS, and rag_trace()/rag_py().
# Zero dependency on the rest of the plugin — degrades gracefully if absent.

# Assets dir = the directory this file lives in.
ASSETS="$(cd "$(dirname "${BASH_SOURCE:-$0}")" && pwd)"

# Load .env from the plugin root if present (optional convenience only).
# `set -a` is load-bearing: .env lines are bare VAR="value" with no `export`, so sourcing
# them alone makes SHELL variables that child processes never see. Every knob documented
# as .env-configurable (SCHOLAR_ZOTERO_DIR, EMBED_MODEL, RAG_GRAPH_*, RAG_CITE_*,
# SCHOLAR_CROSSREF_EMAIL, OLLAMA_HOST) was therefore inert for the Python engine, and
# zotero_reader silently fell through to auto-detection no matter what was configured.
# SCHOLAR_RAG_DIR appeared to work only because it is explicitly re-exported below.
set -a
for _envf in "${SCHOLAR_SKILL_DIR:-}/.env" "$HOME/.claude/.env"; do
  [ -n "$_envf" ] && [ -f "$_envf" ] && . "$_envf" 2>/dev/null || true
done
set +a
unset _envf

# User-scoped store (cross-project). Sibling of ~/.claude/scholar-knowledge.
export SCHOLAR_RAG_DIR="${SCHOLAR_RAG_DIR:-$HOME/.claude/scholar-rag}"
export RAG_VENV="${RAG_VENV:-$SCHOLAR_RAG_DIR/.venv}"

# Resolve the venv python; fall back to system python3 (with a warning) so
# stdlib-only tools (zotero_reader.py) still run before the venv is built.
if [ -x "$RAG_VENV/bin/python" ]; then
  export RAG_PY="$RAG_VENV/bin/python"
else
  export RAG_PY="$(command -v python3 || command -v python)"
fi

# Run a python asset with the resolved interpreter.
rag_py() { "$RAG_PY" "$ASSETS/$1" "${@:2}"; }

# Append-only RAO trace (self-contained; no dependency on the plugin's
# emit-trace.sh). One JSON object per line under the store's logs/.
rag_trace() {
  # usage: rag_trace <step> <status ok|fail|skipped> <observation...>
  local step="$1" status="$2"; shift 2 || true
  local log="$SCHOLAR_RAG_DIR/logs/trace-scholar-rag.ndjson"
  mkdir -p "$SCHOLAR_RAG_DIR/logs"
  "$RAG_PY" - "$step" "$status" "$*" <<'PY' 2>/dev/null >>"$log" || true
import sys, json, time
step, status, obs = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                  "skill": "scholar-rag", "step": step,
                  "status": status, "observation": obs[:2000]}))
PY
}
