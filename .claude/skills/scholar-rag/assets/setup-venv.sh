#!/bin/bash
# setup-venv.sh — provision the self-contained scholar-rag Python environment.
#
# Creates a CPython 3.12 venv in $SCHOLAR_RAG_DIR/.venv (default
# ~/.claude/scholar-rag/.venv) and installs assets/requirements.txt. Prefers
# `uv` (fast, provisions the interpreter itself); falls back to system venv.
#
# Usage:  bash setup-venv.sh [--no-graph] [--prefetch-models]
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; . "$HERE/_lib.sh"

NO_GRAPH=0; PREFETCH=0
for a in "$@"; do
  case "$a" in
    --no-graph) NO_GRAPH=1 ;;
    --prefetch-models) PREFETCH=1 ;;
    *) echo "unknown arg: $a" >&2; exit 2 ;;
  esac
done

PYVER="${RAG_PYVER:-3.12}"
mkdir -p "$SCHOLAR_RAG_DIR"
echo "[setup] store   = $SCHOLAR_RAG_DIR"
echo "[setup] venv    = $RAG_VENV  (python $PYVER)"

# ---- 1. create the venv ------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  echo "[setup] using uv $(uv --version)"
  uv python install "$PYVER" >/dev/null 2>&1 || true       # provision interpreter if missing
  uv venv --python "$PYVER" "$RAG_VENV"
  PIP_INSTALL() { uv pip install --python "$RAG_VENV/bin/python" "$@"; }
else
  echo "[setup] uv not found — falling back to system python venv"
  BASEPY="$(command -v "python$PYVER" || command -v python3)"
  echo "[setup] base interpreter: $BASEPY ($("$BASEPY" --version 2>&1))"
  "$BASEPY" -m venv "$RAG_VENV"
  "$RAG_VENV/bin/python" -m pip install --upgrade pip >/dev/null
  PIP_INSTALL() { "$RAG_VENV/bin/python" -m pip install "$@"; }
fi

# ---- 2. install requirements -------------------------------------------------
REQ="$HERE/requirements.txt"
if [ "$NO_GRAPH" = "1" ]; then
  # strip the M4 graph block (everything under the GraphRAG header) for a lean install
  TMPREQ="$(mktemp)"
  awk '/GraphRAG \(M4\)/{skip=1} /optional reranking/{skip=0} skip!=1' "$REQ" > "$TMPREQ"
  echo "[setup] installing requirements (core, no graph extras)"
  PIP_INSTALL -r "$TMPREQ"
  rm -f "$TMPREQ"
else
  echo "[setup] installing requirements (full, incl. GraphRAG)"
  PIP_INSTALL -r "$REQ"
fi

# ---- 3. verify the stack imports --------------------------------------------
echo "[setup] verifying imports..."
"$RAG_VENV/bin/python" - <<'PY'
import importlib, sys
mods = ["fitz", "lancedb", "sentence_transformers", "numpy", "tqdm", "requests", "mcp"]
ok = True
for m in mods:
    try:
        importlib.import_module(m); print(f"  ok   {m}")
    except Exception as e:
        ok = False; print(f"  FAIL {m}: {e}")
try:
    import torch
    print(f"  ok   torch {torch.__version__}  MPS={torch.backends.mps.is_available()}")
except Exception as e:
    print(f"  FAIL torch: {e}")
sys.exit(0 if ok else 1)
PY

# ---- 4. optional: prefetch the embedding model -------------------------------
if [ "$PREFETCH" = "1" ]; then
  echo "[setup] prefetching BAAI/bge-m3 (~2.3GB, one-time)..."
  "$RAG_VENV/bin/python" - <<'PY'
from sentence_transformers import SentenceTransformer
m = SentenceTransformer("BAAI/bge-m3")
print("  bge-m3 dim =", m.get_sentence_embedding_dimension())
PY
fi

echo "[setup] done. Interpreter: $RAG_VENV/bin/python"
rag_trace "setup-venv" "ok" "venv=$RAG_VENV python=$PYVER no_graph=$NO_GRAPH prefetch=$PREFETCH"
