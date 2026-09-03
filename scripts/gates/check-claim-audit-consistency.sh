#!/usr/bin/env bash
# check-claim-audit-consistency.sh — self-validation gate for
# claim-faithfulness audit NDJSON (claim-audit-record/v1).
#
# Referenced as binding by .claude/agents/verify-claim-faithfulness.md Phase 7
# ("run the consistency finalizer... the orchestrator runs it again as a gate")
# — this script ships that finalizer. Schema: schema/claim-audit-record.schema.json.
#
# RULE-SET (each violation is RED):
#   R1  every line parses as JSON with schema == "claim-audit-record/v1"
#   R2  required fields present (claim_id, cite_key, manuscript_loc,
#       manuscript_quote, citation_function, sub_claims[>=1], sentence_verdict,
#       severity, access_tier_max, evidence_retrieved)
#   R3  verdict→severity mapping is CANONICAL:
#         CLAIM-REVERSED|CLAIM-UNSUPPORTED → HIGH
#         CLAIM-MISCHARACTERIZED|CLAIM-OVERCAUSAL|CLAIM-WRONG-POPULATION|CLAIM-IMPRECISE → MED
#         CLAIM-VERIFIED|CLAIM-NOT-A-CLAIM → OK
#         CLAIM-NOT-CHECKABLE → UNVERIFIABLE
#   R4  every sub-claim with verdict SUPPORTED or CONTRADICTED carries a
#       non-empty evidence_quote (no verdict without the passage that produced it)
#   R5  precision rule: CLAIM-UNSUPPORTED requires access_tier_max in
#       T0_kg_fulltext|T1_fulltext|T2_oa_fulltext (abstract-only absence is
#       CLAIM-NOT-CHECKABLE, never UNSUPPORTED)
#   R6  CLAIM-VERIFIED requires evidence_retrieved == true
#   R7  anchor_refs[], when present, match ^[a-z0-9_-]+-[0-9a-f]{8}$
#
# Exit codes: 0 = GREEN (all records consistent), 1 = RED (violations or
# missing/empty file — this gate validates a produced artifact; absence is the
# caller's coverage problem, reported here as RED for fail-loud semantics).
#
# Usage: check-claim-audit-consistency.sh <audit.ndjson>

set -u

AUDIT="${1:-}"
if [ -z "$AUDIT" ]; then
  echo "FAIL"; echo "REASON=usage: check-claim-audit-consistency.sh <audit.ndjson>"; exit 1
fi
if [ ! -s "$AUDIT" ]; then
  echo "FAIL"; echo "REASON=audit file missing or empty: $AUDIT"; exit 1
fi
command -v jq >/dev/null 2>&1 || { echo "FAIL"; echo "REASON=jq is required"; exit 1; }

TOTAL=$(wc -l < "$AUDIT" | tr -d ' ')

VIOLATIONS=$(jq -rc '
  def sev_for(v):
    if   (v == "CLAIM-REVERSED" or v == "CLAIM-UNSUPPORTED") then "HIGH"
    elif (v == "CLAIM-MISCHARACTERIZED" or v == "CLAIM-OVERCAUSAL"
          or v == "CLAIM-WRONG-POPULATION" or v == "CLAIM-IMPRECISE") then "MED"
    elif (v == "CLAIM-VERIFIED" or v == "CLAIM-NOT-A-CLAIM") then "OK"
    elif (v == "CLAIM-NOT-CHECKABLE") then "UNVERIFIABLE"
    else "INVALID" end;
  . as $r
  | [
      (if .schema != "claim-audit-record/v1" then "R1:bad-schema" else empty end),
      (if ([.claim_id, .cite_key, .manuscript_loc, .manuscript_quote,
            .citation_function, .sentence_verdict, .severity, .access_tier_max]
           | map(type == "string" and length > 0) | all | not)
           or (.sub_claims | type != "array" or length < 1)
           or (.evidence_retrieved | type != "boolean")
       then "R2:missing-required-field" else empty end),
      (if sev_for(.sentence_verdict) == "INVALID" then "R3:unknown-verdict"
       elif .severity != sev_for(.sentence_verdict)
       then "R3:severity-mismatch(\(.sentence_verdict)->\(.severity),canonical=\(sev_for(.sentence_verdict)))"
       else empty end),
      (if ((.sub_claims // []) | map(select((.verdict == "SUPPORTED" or .verdict == "CONTRADICTED")
            and ((.evidence_quote // "") | length) == 0)) | length) > 0
       then "R4:verdict-without-evidence-quote" else empty end),
      (if .sentence_verdict == "CLAIM-UNSUPPORTED"
          and ((.access_tier_max // "") | IN("T0_kg_fulltext","T1_fulltext","T2_oa_fulltext") | not)
       then "R5:unsupported-without-fulltext(\(.access_tier_max // "none"))" else empty end),
      (if .sentence_verdict == "CLAIM-VERIFIED" and .evidence_retrieved != true
       then "R6:verified-without-retrieval" else empty end),
      (if ((.anchor_refs // []) | map(select(test("^[a-z0-9_-]+-[0-9a-f]{8}$") | not)) | length) > 0
       then "R7:malformed-anchor-ref" else empty end)
    ]
  | select(length > 0)
  | "\($r.claim_id // "?"): \(join("; "))"
' "$AUDIT" 2>/dev/null)
JQ_RC=$?

if [ "$JQ_RC" -ne 0 ]; then
  echo "FAIL"
  echo "REASON=unparseable NDJSON (jq exit $JQ_RC) — at least one line is not valid JSON"
  exit 1
fi

echo "Claim-audit consistency check: $AUDIT ($TOTAL record(s))"
if [ -n "$VIOLATIONS" ]; then
  COUNT=$(printf '%s\n' "$VIOLATIONS" | grep -c . )
  printf '%s\n' "$VIOLATIONS" | sed 's/^/  RED: /'
  echo "RESULT: RED — $COUNT record(s) violate claim-audit-record/v1 consistency rules"
  exit 1
fi
echo "RESULT: GREEN — all $TOTAL record(s) consistent (R1–R7)"
exit 0
