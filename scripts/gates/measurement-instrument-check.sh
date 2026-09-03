#!/usr/bin/env bash
# measurement-instrument-check.sh — bind every measurement-derived constant to the
# instrument that produced it, and every instrument to the construct its consumer needs.
#
# CONTRACT
#   A measurement is not a number. It is a number PLUS the question that produced it.
#   This gate refuses a project in which a model consumes a constant whose instrument
#   asked a different question than the model needs answered.
#
# AUDIT TRAIL (2026-08-11 a large-corpus annotation run, handoff §12 — the most
#   consequential scientific error of that run, survived four review iterations).
#   `analysis_focal_model.R` hard-coded `TARGET_RECALL <- 0.62`. The 0.62 came from an
#   LLM audit whose prompt asked about "the superordinate category in the sense used by domain experts,
#   sense" and credited "an alternative or colloquial name" — a BROADER construct than the
#   lexicon the consuming model implements. A codebook-matched re-draw on the SAME 358
#   items measured 0.94. The "script asymmetry" that drove two declared sensitivity arms,
#   an outcome-conditioned predecessor, and a planned manuscript caveat was not present
#   under the matcher's own codebook. Three compounding reasons, each addressed here:
#     1. The literal had no path to its source — retracting the artifact could not reach a
#        number typed into source, because a literal is not a read.
#     2. A comment asserted the opposite ("NOTHING here is hard-coded from a count" — true
#        of the cell counts, false of the rate, and the rate was load-bearing).
#     3. Nobody asked what the annotator was asked. Six review-code-* agents checked
#        whether the number was USED correctly; none checked whether it MEASURED the
#        construct the model needed.
#   It was caught only because an estimator refused a degenerate fit (one job,
#   "singular information matrix"). A gate is cheaper than a refusal.
#
# RULE-SET
#   R1  Every validation_report.json under the project declares an instrument
#       (`instrument.declared == true`). An explicit `--no-instrument` waiver is YELLOW,
#       never GREEN — the waiver is a recorded absence, not a satisfied requirement.
#   R2  Every constant declared in design/measurement-constants.json still holds the
#       declared value in its consuming script. A constant that drifted from its
#       declaration is RED (the script and the declaration disagree about a measurement).
#   R3  Every declared constant names an instrument_id that resolves to a construct-match
#       record whose verdict is MATCH. NARROWER / BROADER / UNASSESSED are RED — this is
#       the §12 shape.
#   R4  A measurement's design parameters are properties of the declared gate, not CLI
#       defaults (handoff §11/U14). When design/measurement-design.json declares n /
#       stratification / stopping rule for a named measurement, the artifact's
#       `<out>.design.json` sidecar must agree. Disagreement is RED — this is the hakka
#       n=400 -> n=100 silent-rebuild regression that flipped a KEEP to a DROP.
#   R5  Measurement-shaped literals in scripts (IDENT_RECALL / _PRECISION / _SE / _SP /
#       _KAPPA / _ACCURACY / _RATE assigned a bare float) that are NOT declared are
#       YELLOW — advisory, naming exactly what to declare.
#
# EXITS
#   0 GREEN  — every declared instrument resolves and every declared constant matches
#   1 RED    — R1 (undeclared) / R2 (drift) / R3 (construct mismatch) / R4 (design drift)
#   2 YELLOW — waived instruments and/or undeclared measurement-shaped literals only
#   3 INERT  — no validation reports, no declarations, no measurement literals: the
#              measurement path was not used by this project (never a false GREEN)
#
# FIXTURES  tests/smoke/test-measurement-instrument-check.sh
#
# Usage:
#   bash scripts/gates/measurement-instrument-check.sh <project_dir>

set -uo pipefail
export LC_ALL=C

if [ $# -lt 1 ]; then
  echo "Usage: measurement-instrument-check.sh <project_dir>" >&2
  exit 64
fi

PROJ="$1"
if [ ! -d "$PROJ" ]; then
  echo "STATUS=RED"
  echo "REASON=project_dir_not_found"
  echo "FAIL: project directory not found: $PROJ"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  # U7: a check that cannot look must say so in STATUS, never report GREEN.
  echo "STATUS=INERT"
  echo "REASON=python3_unavailable"
  echo "INFO: measurement-instrument-check.sh needs python3 to read JSON declarations."
  exit 3
fi

MIC_PROJ="$PROJ" python3 <<'PYCHECK'
import json, math, os, re, sys, glob

PROJ = os.environ["MIC_PROJ"]
reds, yellows, infos = [], [], []
unreadable = []

def rel(p):
    try: return os.path.relpath(p, PROJ)
    except Exception: return p

def load(path):
    """Return (obj, error). A malformed declaration is an error, never an empty dict —
    an unparseable file must not read as 'nothing declared' (three-valued collapse)."""
    try:
        with open(path) as fh: return json.load(fh), None
    except FileNotFoundError: return None, None
    except Exception as e: return None, f"{rel(path)}: {str(e)[:120]}"

# ---------------------------------------------------------------- discovery
reports = []
for pat in ("**/validation_report*.json", "**/*validation-report*.json"):
    reports += glob.glob(os.path.join(PROJ, pat), recursive=True)
reports = sorted(set(p for p in reports if "/_snapshots/" not in p))

consts_path  = os.path.join(PROJ, "design", "measurement-constants.json")
design_path  = os.path.join(PROJ, "design", "measurement-design.json")
match_dirs   = [os.path.join(PROJ, "design"), os.path.join(PROJ, "annotations"),
                os.path.join(PROJ, "compute"), os.path.join(PROJ, "reports")]
match_files  = []
for d in match_dirs:
    match_files += glob.glob(os.path.join(d, "construct-match*.json"))
match_files = sorted(set(match_files))

def as_entries(obj, key, rule, what, record_keys=()):
    """Coerce a declaration to a list of dict entries, or RED.

    External audit 2026-08-12 round 1: `{"constants":"garbage"}` was iterated CHARACTER BY
    CHARACTER and every character silently skipped as 'not a dict', leaving the gate
    GREEN on an unassessable declaration. A malformed schema must never read as an
    empty-but-valid one — that is the DC-01 collapse this gate exists to catch.

    Round 2: the first version of THIS function then treated every top-level dict as a
    wrapper and substituted [] when the wrapper key was absent, silently discarding the
    BARE-OBJECT form that construct-match.md documents as valid ("Either a bare object, a
    list, or {"instruments": [...]}"). That produced a FALSE missing-construct RED. A dict
    is a wrapper only if it carries the wrapper key; otherwise, if it looks like a record,
    it IS the record.
    """
    if obj is None: return []
    if isinstance(obj, dict):
        if key in obj:
            obj = obj[key]
            # An EMPTY dict under the wrapper key declares nothing; without this it fell
            # through to `[obj]` below and became one empty record, producing a false
            # "record has no instrument_id" RED (audit round 3).
            if isinstance(obj, dict) and not obj:
                return []
        elif record_keys and any(k in obj for k in record_keys):
            obj = [obj]                      # documented bare-object form
        elif not obj:
            return []                        # an empty object declares nothing
        else:
            reds.append((rule, f"{what} is an object with neither a {key!r} key nor any of "
                               f"{list(record_keys)} — cannot tell whether it is a wrapper or a record"))
            return []
    if isinstance(obj, dict): obj = [obj]
    # NOTE (mutation testing 2026-08-12): this type check is partly redundant — a string
    # falls through to the per-entry `bad` check below, which also REDs. It is kept because
    # it names the actual shape problem ("not a list of objects") instead of reporting N
    # non-object entries, where N is the character count. Reverting it alone does not fail
    # the suite.
    if not isinstance(obj, list):
        reds.append((rule, f"{what} is not a list of objects (got {type(obj).__name__}) — "
                           "an unparseable declaration cannot be treated as 'nothing declared'"))
        return []
    bad = [e for e in obj if not isinstance(e, dict)]
    if bad:
        reds.append((rule, f"{what} contains {len(bad)} non-object entr(y/ies); every entry must be a JSON object"))
    return [e for e in obj if isinstance(e, dict)]

def truthy(v):
    """JSON booleans only. Python treats the STRING "false" as truthy, so `"declared":"false"`
    previously entered the declared branch and `"waived":"false"` recorded a waiver."""
    return v is True

consts, err = load(consts_path)
if err: reds.append(("R2", err))
design, err = load(design_path)
if err: reds.append(("R4", err))

# ------------------------------------------------- construct-match registry
matches = {}   # instrument_id -> record
for mf in match_files:
    obj, err = load(mf)
    if err: reds.append(("R3", err)); continue
    recs = as_entries(obj, "instruments", "R3", rel(mf), ("instrument_id", "asked", "needed", "verdict"))
    for r in recs:
        if r.get("instrument_id"):
            matches[r["instrument_id"]] = r
        else:
            reds.append(("R3", f"{rel(mf)}: a construct-match record has no instrument_id"))

# ------------------------------------------------------------------ R1
declared_ids = set()
for rp in reports:
    obj, err = load(rp)
    if err:
        reds.append(("R1", f"{rel(rp)} is not readable JSON: {err}")); continue
    inst = (obj or {}).get("instrument")
    if not isinstance(inst, dict):
        reds.append(("R1", f"{rel(rp)} carries no `instrument` block — this report cannot "
                           "certify WHICH instrument produced the labels it validates"))
        continue
    if truthy(inst.get("declared")):
        if inst.get("instrument_id"): declared_ids.add(inst["instrument_id"])
        else:
            reds.append(("R1", f"{rel(rp)} declares an instrument with no `instrument_id` — "
                               "a constant cannot bind to an unnamed instrument"))
    elif truthy(inst.get("waived")):
        yellows.append(("R1", f"{rel(rp)} waived instrument provenance (--no-instrument); "
                              "recorded as an absence, not a satisfied requirement"))
    else:
        reds.append(("R1", f"{rel(rp)} has instrument.declared=false with no waiver"))

# ------------------------------------------------------------------ R2 + R3
declared_consts = as_entries(consts, "constants", "R2", "design/measurement-constants.json", ("name", "script", "instrument_id"))

# leading-dot forms (.62) and exponent forms are all measurement literals
NUM = r"[-+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?"
for c in declared_consts:
    if not isinstance(c, dict): continue
    name = c.get("name"); script = c.get("script"); val = c.get("value")
    iid  = c.get("instrument_id")
    if not name or script is None:
        reds.append(("R2", f"malformed constant declaration (needs name + script): {c!r:.120}")); continue
    spath = script if os.path.isabs(script) else os.path.join(PROJ, script)
    if not os.path.isfile(spath):
        reds.append(("R2", f"{name}: consuming script not found: {script}")); continue
    try:
        src = open(spath, encoding="utf-8", errors="replace").read()
    except Exception as e:
        reds.append(("R2", f"{name}: {script} unreadable ({str(e)[:80]})")); continue
    # R assignment (<- or =) or Python assignment; comments stripped line-wise first.
    body = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
    m = re.search(r"\b" + re.escape(name) + r"\b\s*(?:<-|=)\s*(" + NUM + r")", body)
    if not m:
        reds.append(("R2", f"{name}: declared as consumed by {script} but no numeric "
                           f"assignment found there — the declaration is stale or the "
                           f"constant moved"))
        continue
    found = float(m.group(1))
    if val is not None and not (isinstance(val, (int, float)) and math.isfinite(float(val))):
        reds.append(("R2", f"{name}: declared value {val!r} is not a finite number — "
                           "a non-finite declaration can never register drift (NaN compares "
                           "unequal to everything, so the check silently passes)"))
    elif val is not None and abs(found - float(val)) > 1e-12:
        reds.append(("R2", f"{name}: {script} holds {found}, declaration says {val} — "
                           "the script and the declaration disagree about a measurement"))
    if not iid:
        reds.append(("R3", f"{name}: no instrument_id — a number typed into source has no "
                           "path to its source (§12 reason 1)"))
        continue
    rec = matches.get(iid)
    if rec is None:
        reds.append(("R3", f"{name}: instrument_id '{iid}' has no construct-match record "
                           f"under design/ | annotations/ | compute/ | reports/"))
        continue
    verdict = str(rec.get("verdict", "")).upper()
    if verdict != "MATCH":
        detail = rec.get("note") or rec.get("rationale") or ""
        reds.append(("R3", f"{name}: instrument '{iid}' construct verdict is {verdict or 'UNASSESSED'} "
                           f"— the annotator was not asked the question this model needs"
                           + (f" ({detail[:120]})" if detail else "")))
    for need in ("asked", "needed"):
        if not rec.get(need):
            yellows.append(("R3", f"instrument '{iid}': construct-match record omits `{need}` — "
                                  "the verdict is not auditable without both constructs stated"))

# ------------------------------------------------------------------ R3b
# An instrument that produced SHIPPED LABELS, with no construct-match record at all.
# R3 above only reaches instruments named by a declared constant. But the construct
# question applies to the labels too: a codebook can be broader or narrower than the
# model that consumes the resulting variable, and nothing else in the system asks.
# YELLOW rather than RED — the labels carry their codebook, so this is a missing
# assessment rather than a demonstrated mismatch.
for iid in sorted(declared_ids):
    if iid not in matches:
        yellows.append(("R3b", f"instrument '{iid}' validated labels but has no construct-match "
                               "record — nothing has asked whether the question the annotator was "
                               "asked matches the question the consuming model needs "
                               "(see scholar-annotate/references/construct-match.md)"))

# ------------------------------------------------------------------ R4
declared_designs = as_entries(design, "measurements", "R4", "design/measurement-design.json", ("name", "artifact", "expect"))
for d in declared_designs:
    if not isinstance(d, dict): continue
    nm = d.get("name", "(unnamed)"); art = d.get("artifact")
    if not art:
        reds.append(("R4", f"{nm}: measurement design declares no `artifact` to check against")); continue
    apath = art if os.path.isabs(art) else os.path.join(PROJ, art)
    side = apath + ".design.json"
    if not os.path.isfile(side):
        reds.append(("R4", f"{nm}: no design sidecar at {rel(side)} — the artifact does not "
                           "record the parameters it was produced under (§11/U14)"))
        continue
    got, err = load(side)
    if err: reds.append(("R4", err)); continue
    for k, want in (d.get("expect") or {}).items():
        have = (got or {}).get(k)
        if have != want:
            reds.append(("R4", f"{nm}: {k} declared as {want!r} but the artifact was produced "
                               f"with {have!r} — a rebuild silently changed the evidentiary "
                               f"strength of a shipped decision"))

# ------------------------------------------------------------------ R5
LIT = re.compile(r"^\s*([A-Z][A-Z0-9_]*(?:RECALL|PRECISION|_SE|_SP|SENS|SPEC|KAPPA|ACCURACY|_RATE|_FPR|_FNR))\s*(?:<-|=)\s*(" + NUM + r")", re.M)
declared_names = {c.get("name") for c in declared_consts if isinstance(c, dict)}
scanned = 0
for ext in ("*.R", "*.r", "*.py"):
    for sp in glob.glob(os.path.join(PROJ, "scripts", "**", ext), recursive=True):
        scanned += 1
        try: src = open(sp, encoding="utf-8", errors="replace").read()
        except Exception as e:
            unreadable.append(rel(sp))
            yellows.append(("R5", f"{rel(sp)}: unreadable ({str(e)[:60]}) — not scanned for "
                                  "measurement literals; this is 'not assessed', not 'clean'"))
            continue
        body = "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())
        for nm, num in LIT.findall(body):
            if nm not in declared_names:
                yellows.append(("R5", f"{rel(sp)}: `{nm} = {num}` looks like a measurement-derived "
                                      "constant but is not declared in design/measurement-constants.json"))

# ------------------------------------------------------------------ verdict
print("GATE=measurement-instrument-check")
print("PROJECT=%s" % PROJ)
print("VALIDATION_REPORTS=%d" % len(reports))
print("DECLARED_CONSTANTS=%d" % len(declared_consts))
print("CONSTRUCT_MATCH_RECORDS=%d" % len(matches))
print("SCRIPTS_SCANNED=%d" % scanned)
print("RED=%d" % len(reds))
print("YELLOW=%d" % len(yellows))
for rule, msg in reds:    print("MIC_RED %s: %s" % (rule, msg))
for rule, msg in yellows: print("MIC_YELLOW %s: %s" % (rule, msg))

# INERT is "nothing to look at", NOT "nothing went wrong". It must therefore be evaluated
# only after RED/YELLOW are known to be empty, and it must count EVERY discovery channel —
# including a declared measurement design, which is itself evidence the path is in use.
# (Caught by tests/smoke/test-measurement-instrument-check.sh on first run: a project whose
#  only artifact was a drifted design short-circuited to INERT and the R4 RED was discarded.)
discovered = bool(reports or declared_consts or matches or declared_designs)
if not discovered and not reds and not yellows:
    print("STATUS=INERT")
    print("REASON=no measurement artifacts, declarations, or measurement-shaped literals found")
    sys.exit(3)
if reds:
    print("STATUS=RED")
    print("REASON=%d instrument/constant binding violation(s)" % len(reds))
    print("HOW TO FIX:")
    print("  1. Re-run MODE 7 with --manifest so validation_report.json carries an instrument block.")
    print("  2. Declare each measurement-derived constant in design/measurement-constants.json:")
    print('       {"constants":[{"name":"TARGET_RECALL","value":0.94,"script":"scripts/06-logits.R",')
    print('                      "instrument_id":"target-recall-v2"}]}')
    print("  3. Write a construct-match record (see scholar-annotate/references/construct-match.md):")
    print('       {"instrument_id":"target-recall-v2","asked":"…","needed":"…","verdict":"MATCH"}')
    sys.exit(1)
if yellows:
    print("STATUS=YELLOW")
    print("REASON=%d advisory(ies) — waived provenance and/or undeclared measurement literals" % len(yellows))
    sys.exit(2)
print("STATUS=GREEN")
print("REASON=every declared constant binds to an instrument whose construct matches its consumer")
sys.exit(0)
PYCHECK
