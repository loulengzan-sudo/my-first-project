#!/bin/bash
# Push the slim mirror + this skill's assets + the run manifest to a cluster.
# EDIT the three >>> SET values, then: bash rsync-helper.sh
set -euo pipefail
USER=">>> SET"; HOST=">>> SET"; DEST=">>> SET"     # e.g. amehmood / login-node / /lustre/home/you/proj
LOCAL_MIRROR="${LOCAL_MIRROR:-./xfer}"
SKILL_ASSETS="${SKILL_ASSETS:-$(cd "$(dirname "$0")/.." && pwd)}"   # scholar-annotate/assets
MANIFEST="${MANIFEST:-./run-manifest.json}"
ssh "$USER@$HOST" "mkdir -p '$DEST/skills/scholar-annotate'"
rsync -avz --partial --progress "$LOCAL_MIRROR/"  "$USER@$HOST:$DEST/"
rsync -avz --partial --progress "$SKILL_ASSETS"   "$USER@$HOST:$DEST/skills/scholar-annotate/"
rsync -avz --partial "$MANIFEST" "$USER@$HOST:$DEST/run-manifest.json" 2>/dev/null || true
cat <<EOF

On the cluster:
  export PROJ=$DEST
  # edit skills/scholar-annotate/assets/hpc/annotate.sbatch (partition/account/MODEL_PATH/PYBIN)
  cd \$PROJ && SMOKE=500 sbatch skills/scholar-annotate/assets/hpc/annotate.sbatch   # smoke test
  sbatch skills/scholar-annotate/assets/hpc/annotate.sbatch                          # full (resumable)
EOF
