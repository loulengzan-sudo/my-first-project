#!/usr/bin/env bash
# code-review-coverage-check.sh — Verify all 6 review-code-* agents fired
# in the latest scholar-code-review report.
#
# Audit 2026-04-27 user-flagged finding (audit itself missed it). The cohab
# Phase 5.5 fix-log showed only 3 of 6 review-code agents firing
# (data-handling, correctness, statistics fired; reproducibility, robustness,
# style had zero footprint). Diagnosed cause: Phase 5.5 instructions say
# "scholar-code-review in full mode" but do not enumerate the 6 dispatchable
# agents. The model interprets "code review" loosely, picks the agents that
# feel most relevant to causal-inference work, and skips the others. This
# gate makes the coverage requirement mechanical and structural.
#
# Usage:
#   bash scripts/gates/code-review-coverage-check.sh <project_dir> [<report_path>] [--required=fast|full] [--phase=<tag>]
#
# --required modes (audit 2026-04-27-v2 ERROR-A2):
#   full (default) — all 6 review-code-* agents must fire. Used at Phase 5.5
#                    exit gate where scholar-code-review is dispatched in full
#                    mode (correctness + data-handling + statistics +
#                    reproducibility + robustness + style).
#   fast           — the 3-agent fast subset (correctness + data-handling +
#                    statistics) must fire. Used at Phase 5A.5 exit gate.
#                    Phase 5A.5 deliberately runs only the fast 3 — but if
#                    Phase 5.5 is silently skipped, full coverage is never
#                    enforced. The fast-mode gate at 5A.5 closes that hole.
#
# --phase=<tag> (pre-execution-review protocol, 2026-08-13):
#   Overrides the dispatch-manifest phase tag the manifest-aware path filters
#   on (default "5.5", the orchestrator Phase 5.5 convention). Standalone
#   pre-execution callers (pre-exec-review-check.sh) pass their skill-scoped
#   tag, e.g. --phase=pre-exec-analyze. When --phase is EXPLICITLY provided
#   and the dispatch manifest is unavailable, the gate exits YELLOW instead
#   of falling back to token-counting — token presence in prose must never
#   turn into a GREEN for a newly generated pre-execution review
#   (_shared/pre-execution-review.md §3; the wrapper maps YELLOW to RED).
#
# Detection:
#   For each required review-code-* agent, the gate greps the most recent
#   consolidated code-review report for the agent's name. The scholar-code-review
#   skill produces a per-dimension scorecard table that includes the agent's
#   exact name (e.g., `review-code-correctness`), so the absence of that token
#   means the agent did not produce a report row — i.e., it did not fire.
#
# Excuse mechanism:
#   To skip an agent legitimately (e.g., no scripts to review for `style`
#   on a qualitative-only project), add an explicit annotation line to the
#   report: `[EXCUSED: <reason>] review-code-<dim>`. The gate honors these
#   and downgrades the missing agent to INFO.
#
# Exit codes:
#   0 GREEN  — all required review-code-* agents present (or excused) in the report
#   1 RED    — ≥1 required agent missing without excuse
#   2 YELLOW — report unreadable / not found; run scholar-code-review first

set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: code-review-coverage-check.sh <project_dir> [<report_path>] [--required=fast|full]" >&2
  exit 1
fi

PROJ=""
REPORT=""
MODE="full"
PHASE_ARG=""
REPORT_EXPLICIT=0
AGG_TMP=""
REPORT_LAYOUT="consolidated"
# Clean up the aggregate scratch file on every exit path.
trap '[ -n "$AGG_TMP" ] && [ -f "$AGG_TMP" ] && rm -f "$AGG_TMP"' EXIT
for arg in "$@"; do
  case "$arg" in
    --required=fast) MODE="fast" ;;
    --required=full) MODE="full" ;;
    --required=*) echo "ERR: unknown --required value (expected fast|full)" >&2; exit 1 ;;
    --phase=?*) PHASE_ARG="${arg#--phase=}" ;;
    --phase=) echo "ERR: --phase requires a non-empty tag" >&2; exit 1 ;;
    *)
      if [ -z "$PROJ" ]; then PROJ="$arg"
      elif [ -z "$REPORT" ]; then REPORT="$arg"; REPORT_EXPLICIT=1
      else echo "ERR: too many positional args" >&2; exit 1
      fi
      ;;
  esac
done
if [ -z "$PROJ" ]; then
  echo "Usage: code-review-coverage-check.sh <project_dir> [<report_path>] [--required=fast|full] [--phase=<tag>]" >&2
  exit 1
fi

# Auto-detect newest code-review report. Skill writes to either
# `${PROJ}/reports/code-review-*.md` (orchestrator convention) or
# `${PROJ}/code-review/code-review-*.md` (direct invocation).
# An explicitly-passed report path that does not exist is an INVOCATION ERROR, not
# an absent report. Folding the two together (the prior behaviour) meant a stray
# positional — e.g. `<proj> 5.5`, mistaking the phase for an argument — was swallowed
# as `[<report_path>]` and reported as "no report found", flipping a genuinely GREEN
# project to YELLOW with a message naming the wrong cause (register G4).
if [ "$REPORT_EXPLICIT" = "1" ] && [ ! -f "$REPORT" ]; then
  echo "STATUS=RED"
  echo "FAIL: explicit report path does not exist: $REPORT"
  case "$REPORT" in
    [0-9]*)
      echo "      That looks like a phase tag. Phase is a FLAG, not a positional:"
      echo "        code-review-coverage-check.sh \"\$PROJ\" --phase=$REPORT" ;;
    *)
      echo "      Pass an existing report path, or omit it to auto-discover." ;;
  esac
  exit 1
fi

if [ -z "$REPORT" ]; then
  CANDIDATES=$(ls -t \
    "$PROJ"/reports/code-review-*.md \
    "$PROJ"/code-review/code-review-*.md \
    "$PROJ"/output/reports/code-review-*.md \
    "$PROJ"/output/code-review/code-review-*.md \
    2>/dev/null | head -1 || true)
  REPORT="$CANDIDATES"
fi

# Layout B — per-dimension reports. An orchestrator that dispatches the six agents
# separately gets six reports, each written by one agent
# (reviews/phase-5.5-iterN-<dim>.md). On the run that surfaced this, 36 such files
# existed and the gate still said "no report found".
#
# scholar-code-review writes a single CONSOLIDATED report, which Layout A above
# already finds — so this path is dormant for that skill and exists for callers
# that fan the dimensions out into separate files.
#
# These are aggregated, never selected: each file carries ONE dimension, so picking
# the newest single file would scan a 1-of-6 surface and RED a project that did all
# the work. We concatenate the highest iteration's files into one scan surface.
if [ -z "$REPORT" ] && [ -d "$PROJ/reviews" ]; then
  _hi=0
  for _f in "$PROJ"/reviews/phase-5.5-iter*-*.md; do
    [ -f "$_f" ] || continue
    _b="${_f##*/}"; _n="${_b#phase-5.5-iter}"; _n="${_n%%-*}"
    case "$_n" in ''|*[!0-9]*) continue ;; esac
    [ "$_n" -gt "$_hi" ] && _hi="$_n"
  done
  if [ "$_hi" -gt 0 ]; then
    AGG_TMP="$(mktemp -t crcc-agg.XXXXXX 2>/dev/null || echo "")"
    if [ -n "$AGG_TMP" ]; then
      for _f in "$PROJ"/reviews/phase-5.5-iter${_hi}-*.md; do
        [ -f "$_f" ] || continue
        printf '\n<!-- source: %s -->\n' "${_f##*/}" >> "$AGG_TMP"
        cat "$_f" >> "$AGG_TMP"
      done
      if [ -s "$AGG_TMP" ]; then
        REPORT="$AGG_TMP"
        REPORT_LAYOUT="per-dimension(iter${_hi})"
      fi
    fi
  fi
fi

if [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
  echo "STATUS=YELLOW"
  echo "WARN: no code-review report found under $PROJ/{reports,code-review}/ or $PROJ/reviews/phase-5.5-iterN-<dim>.md."
  echo "      Run scholar-code-review (full mode) before invoking this gate."
  exit 2
fi
echo "REPORT_LAYOUT=$REPORT_LAYOUT"

if [ "$MODE" = "fast" ]; then
  REQUIRED=(
    "review-code-correctness"
    "review-code-data-handling"
    "review-code-statistics"
  )
else
  REQUIRED=(
    "review-code-correctness"
    "review-code-data-handling"
    "review-code-statistics"
    "review-code-reproducibility"
    "review-code-robustness"
    "review-code-style"
  )
fi

# A3 (2026-05-23): manifest-aware binding. The previous logic counted
# agent-name TOKEN occurrences in the report file, which let consolidated-
# batch dispatches (5 agents sharing one Task agentId) pass as if they were
# 5 separate dispatches. The new logic prefers the dispatch-manifest
# (logs/dispatch-manifest.jsonl) as the source of truth — each row carries
# a unique Task agentId from a real Agent call. Falls back to token-counting
# only when the manifest is absent (legacy projects pre-2026-05-19).
MANIFEST="${PROJ}/logs/dispatch-manifest.jsonl"
USE_MANIFEST=0
HAVE_JQ=0
if command -v jq >/dev/null 2>&1; then HAVE_JQ=1; fi
if [ -f "$MANIFEST" ] && [ "$HAVE_JQ" = "1" ]; then USE_MANIFEST=1; fi

# Map MODE to dispatch-manifest phase tag. Default "5.5" (orchestrator
# Phase 5.5 / 5A.5 convention); standalone pre-execution callers override
# via --phase=<tag> (e.g. pre-exec-analyze).
MANIFEST_PHASE="5.5"
if [ -n "$PHASE_ARG" ]; then MANIFEST_PHASE="$PHASE_ARG"; fi

# Pre-execution provenance rule: an EXPLICIT --phase caller is gating a
# newly generated review — token-counting in prose is not acceptable
# provenance for it. If the manifest path is unavailable, stop at YELLOW
# here rather than reaching the legacy token fallback below.
if [ -n "$PHASE_ARG" ] && [ "$USE_MANIFEST" != "1" ]; then
  echo "STATUS=YELLOW"
  echo "WARN: --phase=$MANIFEST_PHASE given but dispatch-manifest provenance is unavailable"
  echo "      (manifest=$([ -f "$MANIFEST" ] && echo present || echo absent), jq=$([ "$HAVE_JQ" = "1" ] && echo present || echo absent))."
  echo "      Pre-execution reviews must record each reviewer dispatch via emit-task-dispatch.sh --phase $MANIFEST_PHASE;"
  echo "      token-counting cannot stand in for dispatch provenance on a new review."
  exit 2
fi

EXCUSED_LIST=()
TRULY_MISSING=()
CONSOLIDATED_BATCH=0   # set to 1 when the manifest shows fewer unique IDs than rows
TOTAL_UNIQUE_IDS=0
TOTAL_ROWS=0

if [ "$USE_MANIFEST" = "1" ]; then
  # Manifest-aware path
  for AGENT in "${REQUIRED[@]}"; do
    AGENT_IDS=$(jq -r --arg agent "$AGENT" --arg phase "$MANIFEST_PHASE" \
      'select(.subagent==$agent and .phase==$phase) | .agentId' \
      "$MANIFEST" 2>/dev/null | sort -u)
    AGENT_HITS=0
    if [ -n "$AGENT_IDS" ]; then
      AGENT_HITS=$(printf '%s\n' "$AGENT_IDS" | grep -c . 2>/dev/null || true)
      AGENT_HITS=${AGENT_HITS:-0}
    fi
    if [ "$AGENT_HITS" -ge 1 ]; then
      continue
    fi
    # Manifest didn't show a dispatch; check for excuse annotation in report
    EXCUSED_HITS=$(grep -cE "\[EXCUSED:[^]]*\][[:space:]]+${AGENT}|${AGENT}[[:space:]]+\[EXCUSED:" "$REPORT" 2>/dev/null || true)
    EXCUSED_HITS=${EXCUSED_HITS:-0}
    if [ "$EXCUSED_HITS" -gt 0 ]; then
      EXCUSED_LIST+=("$AGENT")
    else
      TRULY_MISSING+=("$AGENT")
    fi
  done

  # Consolidated-batch detection: count manifest rows vs unique agentIds
  TOTAL_ROWS=$(jq -r --arg phase "$MANIFEST_PHASE" 'select(.phase==$phase) | .agentId' \
    "$MANIFEST" 2>/dev/null | grep -c . || true)
  TOTAL_ROWS=${TOTAL_ROWS:-0}
  TOTAL_UNIQUE_IDS=$(jq -r --arg phase "$MANIFEST_PHASE" 'select(.phase==$phase) | .agentId' \
    "$MANIFEST" 2>/dev/null | sort -u | grep -c . || true)
  TOTAL_UNIQUE_IDS=${TOTAL_UNIQUE_IDS:-0}
  if [ "$TOTAL_ROWS" -gt "$TOTAL_UNIQUE_IDS" ] && [ "$TOTAL_UNIQUE_IDS" -ge 1 ]; then
    CONSOLIDATED_BATCH=1
  fi

  # D4 log-binding (audit 2026-06-10): the dispatch manifest is orchestrator-
  # writable, so 6 DISTINCT but FABRICATED agentIds (never issued by a real
  # Task dispatch) pass the cardinality check above. Bind each manifest
  # agentId to a process-log dispatch row via the shared provenance helper —
  # the same anchor design-review-check / analysis-review-check use. RED only
  # when the log EXISTS and an id is absent (helper rc=1); a missing/legacy log
  # → helper YELLOW (rc=2) → advisory, no false-RED.
  LOG_BIND_RED=""
  _crc_ids=$(jq -r --arg phase "$MANIFEST_PHASE" 'select(.phase==$phase) | .agentId' \
    "$MANIFEST" 2>/dev/null | sort -u | paste -sd, - 2>/dev/null || true)
  CRC_PROV_HELPER="$(dirname "$0")/verify-task-dispatch-provenance.sh"
  if [ -n "$_crc_ids" ] && { [ -x "$CRC_PROV_HELPER" ] || [ -f "$CRC_PROV_HELPER" ]; }; then
    set +e
    _crc_prov=$(bash "$CRC_PROV_HELPER" --ids "$_crc_ids" --proj "$PROJ" --require-distinct --quiet 2>&1)
    _crc_rc=$?
    set -e
    if [ "$_crc_rc" = "1" ]; then
      # Reviewer-audit-batch4 false-RED fix (audit 2026-06-10): only escalate
      # to RED when the process log actually RECORDS code-review dispatch rows
      # for this project — otherwise a legit project that simply does not log
      # review-code dispatches (the process-logger contract does not yet mandate
      # 5.5 agentId rows) would false-RED. If no review-code row exists at all,
      # treat the binding as advisory (the consolidated-batch cardinality check
      # above remains the hard RED).
      if grep -rlE 'subagent_type[=:][[:space:]]*"?review-code-|review-code-[a-z]+.*agentId' "$PROJ"/logs/process-log-*.md >/dev/null 2>&1; then
        LOG_BIND_RED=$(printf '%s\n' "$_crc_prov" | grep '^REASON=' | head -1 | sed 's/^REASON=//' || true)
        [ -n "$LOG_BIND_RED" ] || LOG_BIND_RED="provenance-binding-failed"
      fi
    fi
  fi
  unset _crc_ids _crc_prov _crc_rc CRC_PROV_HELPER
else
  # Legacy fallback (manifest absent OR jq absent) — token-counting.
  echo "INFO: dispatch-manifest unavailable (manifest=$([ -f "$MANIFEST" ] && echo present || echo absent), jq=$([ "$HAVE_JQ" = "1" ] && echo present || echo absent)); falling back to token-counting (legacy mode)"
  for AGENT in "${REQUIRED[@]}"; do
    TOTAL=$(grep -cE "${AGENT}" "$REPORT" 2>/dev/null || true)
    TOTAL=${TOTAL:-0}
    EXCUSED_HITS=$(grep -cE "\[EXCUSED:[^]]*\][[:space:]]+${AGENT}|${AGENT}[[:space:]]+\[EXCUSED:" "$REPORT" 2>/dev/null || true)
    EXCUSED_HITS=${EXCUSED_HITS:-0}
    REAL=$((TOTAL - EXCUSED_HITS))
    if [ "$REAL" -gt 0 ]; then
      continue
    elif [ "$EXCUSED_HITS" -gt 0 ]; then
      EXCUSED_LIST+=("$AGENT")
    else
      TRULY_MISSING+=("$AGENT")
    fi
  done
fi

for AGENT in ${EXCUSED_LIST[@]+"${EXCUSED_LIST[@]}"}; do
  echo "INFO: $AGENT excused per report annotation"
done

if [ "${#TRULY_MISSING[@]}" -eq 0 ]; then
  # D4 (audit 2026-06-10): on a manifest-era project, a consolidated batch
  # (N manifest rows sharing < N unique agentIds) means the required review-code-*
  # agents were NOT each separately dispatched — one Agent call cannot satisfy
  # the 6-independent-reviewers contract. Escalate to RED instead of the prior
  # GREEN-with-WARN (the cheap "6 rows, 1 recycled id" forgery). Legacy
  # (no-manifest) projects cannot verify ids, so they retain the advisory WARN.
  if [ "$USE_MANIFEST" = "1" ] && [ "$CONSOLIDATED_BATCH" = "1" ]; then
    echo "STATUS=RED"
    echo "FAIL: consolidated-batch — $TOTAL_ROWS Phase $MANIFEST_PHASE dispatches share only $TOTAL_UNIQUE_IDS unique agentId(s); the required review-code-* agents were not each separately dispatched (D4, audit 2026-06-10)."
    echo "Per memory feedback_invoke_real_review_agents.md, each reviewer must be a SEPARATE Agent call (one Agent dispatch per dimension)."
    echo "Remediation: re-run scholar-code-review so each review-code-* agent is its own Agent call (distinct agentId per dimension)."
    exit 1
  fi
  # D4 log-binding (audit 2026-06-10): fabricated manifest ids not traceable to
  # a real Task dispatch in the process log.
  if [ -n "${LOG_BIND_RED:-}" ]; then
    echo "STATUS=RED"
    echo "FAIL: code-review dispatch provenance — manifest agentId(s) not bound to a process-log dispatch row (D4 log-binding): ${LOG_BIND_RED}"
    echo "Each review-code-* agentId recorded in logs/dispatch-manifest.jsonl must appear as a real Task-dispatch row in the process log; a hand-written manifest row does not satisfy the gate."
    echo "Remediation: re-dispatch the affected review-code-* agent(s) via real Agent calls."
    exit 1
  fi
  if [ "${#EXCUSED_LIST[@]}" -gt 0 ]; then
    echo "STATUS=GREEN"
    echo "PASS: required agents fired or are explicitly excused (${#EXCUSED_LIST[@]} excused)."
  else
    echo "STATUS=GREEN"
    if [ "$USE_MANIFEST" = "1" ]; then
      echo "PASS: all ${#REQUIRED[@]} required review-code-* agents fired (dispatch-manifest verified, mode=$MODE)"
    else
      echo "PASS: all ${#REQUIRED[@]} required review-code-* agents fired in $(basename "$REPORT") (legacy token-mode=$MODE)"
    fi
  fi
  if [ "$CONSOLIDATED_BATCH" = "1" ]; then
    echo "WARN: consolidated-batch pattern detected — $TOTAL_ROWS Phase $MANIFEST_PHASE dispatches in manifest share only $TOTAL_UNIQUE_IDS unique agentId(s). Memory \`feedback_invoke_real_review_agents.md\` requires each reviewer to be a SEPARATE Agent call."
  fi
  exit 0
fi

echo "STATUS=RED"
if [ "$USE_MANIFEST" = "1" ]; then
  echo "FAIL: ${#TRULY_MISSING[@]} of ${#REQUIRED[@]} required review-code-* agents missing from dispatch-manifest (logs/dispatch-manifest.jsonl, phase=$MANIFEST_PHASE, mode=$MODE):"
else
  echo "FAIL: ${#TRULY_MISSING[@]} of ${#REQUIRED[@]} required review-code-* agents missing from $(basename "$REPORT") (legacy token-mode=$MODE):"
fi
for AGENT in ${TRULY_MISSING[@]+"${TRULY_MISSING[@]}"}; do
  echo "  - $AGENT"
done
if [ "$CONSOLIDATED_BATCH" = "1" ]; then
  echo ""
  echo "ALSO: consolidated-batch pattern detected — $TOTAL_ROWS Phase $MANIFEST_PHASE dispatches share only $TOTAL_UNIQUE_IDS unique agentId(s)."
  echo "Per memory feedback_invoke_real_review_agents.md, each reviewer must be a SEPARATE Agent call (one Agent dispatch per dimension)."
fi
echo ""
if [ "$MODE" = "fast" ]; then
  echo "Remediation: re-run scholar-code-review in fast mode (3 agents in"
  echo "parallel via the Agent tool). The fast subset is:"
  echo "  1. review-code-correctness"
  echo "  2. review-code-data-handling"
  echo "  3. review-code-statistics"
else
  echo "Remediation: re-run scholar-code-review in full mode and confirm all 6"
  echo "agents are dispatched in parallel via the Agent tool. The 6 canonical"
  echo "agents are:"
  echo "  1. review-code-correctness"
  echo "  2. review-code-data-handling"
  echo "  3. review-code-statistics"
  echo "  4. review-code-reproducibility"
  echo "  5. review-code-robustness"
  echo "  6. review-code-style"
fi
echo ""
echo "If an agent is genuinely not applicable (e.g., no scripts → style),"
echo "add an excuse annotation to the report:"
echo "  '[EXCUSED: <reason>] review-code-<dim>'"
exit 1
