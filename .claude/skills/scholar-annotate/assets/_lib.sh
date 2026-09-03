#!/bin/bash
# Bundled fallback helpers so scholar-annotate is FULLY self-contained: used when the shared
# scripts/gates/emit-trace.sh is unavailable. Sub-commands: trace.
cmd="${1:-}"; shift || true
if [ "$cmd" = "trace" ]; then
  skill="${1:-scholar-annotate}"; shift || true
  OUTDIR="${OUTPUT_ROOT:-output}/logs"; mkdir -p "$OUTDIR"
  F="$OUTDIR/trace-${skill}-$(date +%Y-%m-%d).ndjson"
  SEQ=$(( ( [ -f "$F" ] && wc -l < "$F" || echo 0 ) + 1 ))
  step=""; reasoning=""; action=""; observation=""; status="ok"
  while [ $# -gt 0 ]; do case "$1" in
    --step) step="$2"; shift 2;; --reasoning) reasoning="$2"; shift 2;;
    --action) action="$2"; shift 2;; --observation) observation="$2"; shift 2;;
    --status) status="$2"; shift 2;; *) shift;; esac; done
  python3 - "$F" "$SEQ" "$skill" "$step" "$reasoning" "$action" "$observation" "$status" <<'PY'
import sys,json
f,seq,skill,step,reasoning,action,observation,status=sys.argv[1:9]
open(f,"a").write(json.dumps({"seq":int(seq),"skill":skill,"step":step,"reasoning":reasoning,
  "action":action,"observation":observation,"status":status},ensure_ascii=False)+"\n")
PY
  echo "traced seq=$SEQ step=$step"
fi
