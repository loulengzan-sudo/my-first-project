#!/usr/bin/env bash
# ingest-agent-trace.sh — fold a dispatched agent's RAO trace sidecar into the
# run's master trace sink.
#
# WHY THIS EXISTS
#   Most agents (peer-reviewer-*, review-code-*) have NO Bash tool, so they
#   cannot append to a shared NDJSON file. Instead each agent, in one Write,
#   emits a sidecar `<report>.trace.ndjson` (RAO steps; schema in
#   _shared/agent-trace-contract.md) and echoes `TRACE: <sidecar>`. The
#   dispatching orchestrator (which HAS Bash) calls this helper to fold the
#   sidecar into the master trace via emit-trace.sh — so seq/ts/run_id are
#   stamped consistently and the agent's steps carry agent=<type> + agentId.
#   It also cross-checks the agentId against dispatch-manifest.jsonl.
#
# USAGE
#   bash scripts/gates/ingest-agent-trace.sh \
#     --sidecar <path-to-agent.trace.ndjson> \
#     --skill <dispatching-skill>  --agent <subagent_type>  --agentId <id> \
#     [--phase X.X] [--output-root DIR] [--proj DIR]
#
# EXIT CODES
#   0  ingested >=1 record
#   1  usage / missing sidecar / zero valid records ingested (fail-loud)
#   2  emit-trace.sh helper missing / python3 unavailable

set -uo pipefail

SIDECAR="" SKILL="" AGENT="" AGENT_ID="" PHASE="" OUTPUT_ROOT_ARG="" PROJ=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sidecar)      SIDECAR="${2:-}"; shift 2 ;;
    --skill)        SKILL="${2:-}"; shift 2 ;;
    --agent)        AGENT="${2:-}"; shift 2 ;;
    --agentId)      AGENT_ID="${2:-}"; shift 2 ;;
    --phase)        PHASE="${2:-}"; shift 2 ;;
    --output-root)  OUTPUT_ROOT_ARG="${2:-}"; shift 2 ;;
    --proj)         PROJ="${2:-}"; shift 2 ;;
    -h|--help)      grep -E '^#' "$0" | sed 's/^# *//'; exit 0 ;;
    *)              echo "ERROR: unknown flag $1" >&2; exit 1 ;;
  esac
done

if [ -z "$SIDECAR" ] || [ -z "$SKILL" ] || [ -z "$AGENT" ] || [ -z "$AGENT_ID" ]; then
  echo "ERROR: --sidecar, --skill, --agent, --agentId are required" >&2
  exit 1
fi
if [ ! -f "$SIDECAR" ]; then
  echo "ERROR: sidecar trace not found: $SIDECAR" >&2
  exit 1
fi

GATES_DIR="$(cd "$(dirname "$0")" && pwd)"
EMIT="$GATES_DIR/emit-trace.sh"
if [ ! -f "$EMIT" ] || [ ! -r "$EMIT" ]; then
  echo "ERROR: required helper missing/unreadable: $EMIT" >&2
  exit 2
fi
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 required to parse the sidecar" >&2; exit 2; }

OUTPUT_ROOT="${OUTPUT_ROOT_ARG:-${OUTPUT_ROOT:-output}}"

# ── Fold each valid sidecar record into the master trace via emit-trace ──────
# python drives the loop (safe subprocess quoting) and reports the count.
INGESTED=$(python3 - "$SIDECAR" "$EMIT" "$SKILL" "$AGENT" "$AGENT_ID" "$PHASE" "$OUTPUT_ROOT" <<'PY'
import json, os, subprocess, sys
sidecar, emit, skill, agent, agent_id, phase, output_root = sys.argv[1:8]
n = 0
for line in open(sidecar):
    line = line.strip()
    if not line:
        continue
    try:
        r = json.loads(line)
    except Exception:
        print("WARN: skipping malformed sidecar line", file=sys.stderr)
        continue
    step = str(r.get("step") or "").strip()
    reasoning = str(r.get("reasoning") or "")
    action = str(r.get("action") or "")
    observation = str(r.get("observation") or "")
    if not step or not (reasoning or action or observation):
        print("WARN: skipping sidecar record with no step or empty RAO triad", file=sys.stderr)
        continue
    refs = r.get("refs") or []
    if isinstance(refs, list):
        refs = ",".join(str(x) for x in refs)
    else:
        refs = str(refs)
    status = str(r.get("status") or "ok")
    if status not in ("ok", "fail", "skipped"):
        status = "ok"
    args = ["bash", emit, "--skill", skill, "--agent", agent,
            "--agentId", agent_id, "--step", step, "--status", status]
    if phase:
        args += ["--phase", phase]
    if reasoning:
        args += ["--reasoning", reasoning]
    if action:
        args += ["--action", action]
    if observation:
        args += ["--observation", observation]
    if refs:
        args += ["--refs", refs]
    env = dict(os.environ, OUTPUT_ROOT=output_root)
    res = subprocess.run(args, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if res.returncode == 0:
        n += 1
    else:
        print(f"WARN: emit-trace rejected a record (step={step!r})", file=sys.stderr)
print(n)
PY
)
INGESTED=${INGESTED:-0}

if [ "$INGESTED" -lt 1 ]; then
  echo "ERROR: no valid RAO records ingested from $SIDECAR (agent produced an empty/invalid trace)" >&2
  exit 1
fi

# ── Cross-check agentId against the dispatch manifest (non-blocking) ─────────
if [ -n "$PROJ" ]; then
  MANIFEST="$PROJ/logs/dispatch-manifest.jsonl"
  if [ -f "$MANIFEST" ]; then
    if grep -qF "\"agentId\":\"$AGENT_ID\"" "$MANIFEST"; then
      echo "AGENTID_BOUND=yes"
    else
      echo "WARN: agentId '$AGENT_ID' not found in $MANIFEST — record the dispatch via emit-task-dispatch.sh so the trace cross-links to provenance." >&2
      echo "AGENTID_BOUND=no"
    fi
  fi
fi

echo "INGESTED=$INGESTED"
echo "TRACE_FILE=${OUTPUT_ROOT}/logs/trace-${SKILL}-$(date +%Y-%m-%d).ndjson"
exit 0
