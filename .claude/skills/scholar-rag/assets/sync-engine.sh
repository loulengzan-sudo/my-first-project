#!/usr/bin/env bash
# sync-engine.sh — refresh the LOCAL scholar-rag runtime from the dev repo.
#
# The store and its runtime engine live entirely on local disk:
#     ~/.claude/scholar-rag/          store (corpus.sqlite, index/, graph/, raw/)
#     ~/.claude/scholar-rag/engine/   runtime copy of this assets/ dir
#
# Edit the engine in the repo, then run this to push the change into the runtime.
# Nothing else re-points automatically. Two equivalent entry points:
#
#     bash <repo>/.claude/skills/scholar-rag/assets/sync-engine.sh   # syncs from itself
#     bash ~/.claude/scholar-rag/sync-engine.sh                      # syncs from DEFAULT_SRC
#
# Source resolution, in order:
#   1. $SCHOLAR_RAG_SRC, if set — a one-off override for any clone.
#   2. This script's own directory, when it is a repo assets/ dir (not the
#      runtime engine). Running the repo copy always syncs THAT clone, so the
#      script is correct in any checkout without editing DEFAULT_SRC.
#   3. DEFAULT_SRC below — used by the bootstrap copy at the store root, which
#      sits outside any clone and so cannot infer one from its location.
#
#
# WARNING: running `mcp-setup.sh` from a cloud-storage assets dir registers the
# MCP server against that cloud path, adding a mount dependency at query time.
# Always sync into the local engine and register from there — as step 3 does.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STORE="${SCHOLAR_RAG_DIR:-$HOME/.claude/scholar-rag}"
ENGINE="$STORE/engine"

# Fallback for the bootstrap copy at the store root, which sits outside any
# clone and so cannot infer one from its own location. Set SCHOLAR_RAG_SRC to
# the assets/ dir of the checkout you actually edit, or point this at it.
DEFAULT_SRC="${SCHOLAR_SKILL_DIR:+$SCHOLAR_SKILL_DIR/.claude/skills/scholar-rag/assets}"
DEFAULT_SRC="${DEFAULT_SRC:-$HOME/.claude/skills/scholar-rag/assets}"

if [ -n "${SCHOLAR_RAG_SRC:-}" ]; then
  SRC="$SCHOLAR_RAG_SRC"; SRC_ORIGIN="SCHOLAR_RAG_SRC override"
elif [ "$HERE" != "$ENGINE" ] && [ -f "$HERE/mcp_server.py" ]; then
  SRC="$HERE"; SRC_ORIGIN="this script's own clone"
else
  SRC="$DEFAULT_SRC"; SRC_ORIGIN="DEFAULT_SRC"
fi

echo "=== scholar-rag :: sync engine ==="
echo "  from : $SRC"
echo "         ($SRC_ORIGIN)"
echo "  to   : $ENGINE"
echo

[ -d "$SRC" ] || {
  echo "FATAL: dev source not found. Has the repo moved, or is Dropbox unmounted?"
  echo "       The RUNTIME does not need the repo — only this sync does."
  echo "       Set SCHOLAR_RAG_SRC to the assets/ dir of the clone you edit."
  exit 1
}

# Guard the rsync --delete below: refuse to mirror a directory that is not the
# scholar-rag assets tree, or the delete would gut the working engine.
for required in mcp_server.py store.py query.py _lib.sh; do
  [ -f "$SRC/$required" ] || {
    echo "FATAL: '$SRC'"
    echo "       does not look like scholar-rag/assets (missing $required)."
    echo "       Refusing to rsync --delete from it."
    exit 1
  }
done

echo "[1/4] Copying assets ..."
rsync -a --delete --exclude='__pycache__' --exclude='*.pyc' \
      --itemize-changes "$SRC/" "$ENGINE/" | grep -v '^\.d' || true
echo

echo "[2/4] Import smoke test ..."
"$STORE/.venv/bin/python" - "$ENGINE" <<'PY' || { echo "      *** IMPORTS FAILED — not re-registering. ***"; exit 1; }
import sys
sys.path.insert(0, sys.argv[1])
import store, query, graphrag, mcp_server   # noqa: F401
print("      imports OK; store ->", store.rag_dir())
PY
echo

echo "[3/4] Re-registering MCP server at the LOCAL path ..."
# Both hosts, named explicitly rather than relying on mcp-setup.sh's detect-all
# default. Passing --claude alone used to suppress the Codex branch entirely,
# which is why Codex went unregistered until 2026-08-10.
bash "$ENGINE/mcp-setup.sh" --claude --codex --scope user >/dev/null 2>&1 \
  && echo "      registered (claude + codex)" || echo "      registration failed — run engine/mcp-setup.sh by hand"
if command -v claude >/dev/null 2>&1; then
  claude mcp list 2>/dev/null | grep -i "scholar-rag" | sed 's/^/      [claude] /' || true
fi
if command -v codex >/dev/null 2>&1; then
  codex mcp list 2>/dev/null | grep -i "scholar-rag" | sed 's/^/      [codex]  /' || true
fi
echo

# Keep the store-root bootstrap copy current, so the ~/.claude entry point does
# not drift from the tracked version once this script itself changes.
echo "[4/4] Refreshing bootstrap copy at $STORE/sync-engine.sh ..."
if cmp -s "$SRC/sync-engine.sh" "$STORE/sync-engine.sh"; then
  echo "      already current"
else
  # Install via temp + rename, never cp-in-place: this script may BE the file
  # being replaced, and bash reads its source incrementally from the open inode.
  # rename() swaps the directory entry and leaves the running inode untouched;
  # an in-place cp would truncate the file mid-execution.
  _tmp="$STORE/.sync-engine.sh.new.$$"
  if cp "$SRC/sync-engine.sh" "$_tmp" && chmod 755 "$_tmp" && mv -f "$_tmp" "$STORE/sync-engine.sh"; then
    echo "      updated (takes effect next run)"
  else
    rm -f "$_tmp"
    echo "      copy failed — refresh it by hand"
  fi
fi
echo
echo "Done. Restart Claude Code to pick up engine changes."
