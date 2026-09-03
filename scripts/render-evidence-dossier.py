#!/usr/bin/env python3
"""render-evidence-dossier.py — render the Evidence Dossier / Evidence Brief.

Reads a project's Evidence Ledger (evidence/claim-anchors.ndjson, claim-anchor/v1),
optional claim inventory (evidence/claim-inventory.json), optional faithfulness
audit (evidence/claim-faithfulness-audit-*.ndjson), and optionally the canonical
draft — and renders either:

  * the two-layer Evidence Dossier (default): dashboard (summary stats, an
    action-required view first, source index) + per-claim cards ordered by
    manuscript section -> line order (never ledger append order); or
  * a section-scoped Evidence Brief (--brief): the drafting interface for
    /scholar-write — anchors filtered by claim kind / cite key, grouped per
    claim, best evidence first.

The dossier is stamped UNADJUDICATED until an audit file exists. Callers pick
the output path (use version-check.sh for dossier naming); pass the canonical
draft resolved via resolve-canonical-draft.sh — this script never globs for
"newest draft" itself.

Protocol: skills/_shared/evidence-ledger.md. Stdlib only.

Usage:
  render-evidence-dossier.py --proj <dir> --out <file.md>
      [--draft <canonical-draft.md>] [--slug <slug>] [--compact]
  render-evidence-dossier.py --proj <dir> --out <file.md> --brief
      [--section <name>] [--kinds k1,k2] [--cite-keys a,b]

Exit codes: 0 rendered; 1 usage/IO error; 3 nothing to render (no ledger).
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

EV_TAG_RE = re.compile(r"<!--\s*ev:\s*([a-z0-9_,\s-]+?)\s*-->")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
TIER_RANK = {"T1_fulltext": 0, "T2_oa_fulltext": 1, "T0_kg_fulltext": 2,
             "T3_abstract": 3, "T4_none": 4}
ACTION_VERDICTS = {"CLAIM-REVERSED", "CLAIM-UNSUPPORTED", "CLAIM-MISCHARACTERIZED",
                   "CLAIM-OVERCAUSAL", "CLAIM-WRONG-POPULATION"}


def normalize(text):
    """Same normalization as evidence-ledger.md helpers: lowercase, collapse ws."""
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def fingerprint(text):
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def load_ndjson(path):
    records = []
    if not os.path.isfile(path):
        return records
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                records.append({"_parse_error": line[:120]})
    return records


def load_ledger(proj):
    """Return (active_by_id, superseded_ids, parse_errors)."""
    recs = load_ndjson(os.path.join(proj, "evidence", "claim-anchors.ndjson"))
    by_id, errors = {}, 0
    for r in recs:
        if "_parse_error" in r:
            errors += 1
            continue
        aid = r.get("anchor_id")
        if aid:
            by_id[aid] = r  # last record per id wins
    superseded = {r.get("supersedes") for r in by_id.values() if r.get("supersedes")}
    active = {aid: r for aid, r in by_id.items() if aid not in superseded}
    return active, superseded, errors


def load_inventory(proj):
    path = os.path.join(proj, "evidence", "claim-inventory.json")
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data.get("rows", [])
    except (json.JSONDecodeError, OSError):
        return None


def find_audit(proj):
    """Prefer evidence/LATEST-audit.txt pointer; else newest by name (dated)."""
    evdir = os.path.join(proj, "evidence")
    pointer = os.path.join(evdir, "LATEST-audit.txt")
    if os.path.isfile(pointer):
        with open(pointer, encoding="utf-8") as f:
            cand = f.read().strip()
        if cand:
            cand = cand if os.path.isabs(cand) else os.path.join(evdir, cand)
            if os.path.isfile(cand):
                return cand
    hits = sorted(glob.glob(os.path.join(evdir, "claim-faithfulness-audit-*.ndjson")))
    return hits[-1] if hits else None


def load_audit(proj):
    path = find_audit(proj)
    if not path:
        return None, {}, []
    recs = [r for r in load_ndjson(path) if "_parse_error" not in r]
    by_anchor = {}
    for r in recs:
        for aid in r.get("anchor_refs") or []:
            by_anchor.setdefault(aid, []).append(r)
    return path, by_anchor, recs


def scan_draft(draft_path):
    """Return (bindings, unmatched_capable) — bindings in draft order:
    {section, line_no, sentence, anchor_ids}."""
    bindings = []
    if not draft_path or not os.path.isfile(draft_path):
        return bindings
    section = "(front matter)"
    with open(draft_path, encoding="utf-8") as f:
        for i, line in enumerate(f, 1):
            m = re.match(r"^#{1,3}\s+(.*)", line)
            if m:
                section = m.group(1).strip()
            for tag in EV_TAG_RE.finditer(line):
                ids = [t.strip() for t in tag.group(1).split(",") if t.strip()]
                sentence = HTML_COMMENT_RE.sub("", line).strip()
                bindings.append({"section": section, "line_no": i,
                                 "sentence": sentence, "anchor_ids": ids})
    return bindings


def anchor_card(a, audit_by_anchor, compact=False):
    lines = []
    cite = a.get("cite_key", "?")
    loc = a.get("source_loc") or "no locator"
    tier = a.get("access_tier", "?")
    form = a.get("evidence_form", "?")
    stance = a.get("stance", "supports")
    head = f"**{cite}** ({loc}; {tier}; {form}; {stance})"
    verdicts = sorted({r.get("sentence_verdict") for r in audit_by_anchor.get(a.get("anchor_id"), [])
                       if r.get("sentence_verdict")})
    if verdicts:
        head += " — audit: " + ", ".join(verdicts)
    lines.append(f"- {head} `{a.get('anchor_id')}`")
    quote = a.get("evidence_quote")
    if quote:
        words = quote.split()
        if len(words) > 60:
            quote = " ".join(words[:60]) + " […]"
        lines.append(f"  > {quote}")
    else:
        lines.append("  > *(no quotable evidence captured — "
                     f"{form}; see tier {tier})*")
    if not compact and a.get("evidence_context"):
        lines.append(f"  - context: {a['evidence_context']}")
    return lines


def render_dossier(args, active, superseded, errors, inventory, audit_path,
                   audit_by_anchor, audit_recs):
    out = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    slug = args.slug or os.path.basename(os.path.normpath(args.proj))
    adjudicated = audit_path is not None
    title_stamp = "" if adjudicated else " — **UNADJUDICATED**"
    out.append(f"# Evidence Dossier: {slug}{title_stamp}\n")
    out.append(f"*Generated {now} by render-evidence-dossier.py "
               f"(protocol: skills/_shared/evidence-ledger.md)*\n")

    # Draft identity
    out.append("## Identity\n")
    out.append(f"- Ledger: `evidence/claim-anchors.ndjson` — {len(active)} active anchor(s), "
               f"{len(superseded)} superseded, {errors} unparseable line(s)")
    if args.draft and os.path.isfile(args.draft):
        with open(args.draft, "rb") as f:
            dhash = hashlib.sha256(f.read()).hexdigest()[:16]
        out.append(f"- Canonical draft: `{args.draft}` (sha256 {dhash}…)")
    else:
        out.append("- Canonical draft: *(none provided — map/inventory view only)*")
    out.append(f"- Audit: {'`' + os.path.basename(audit_path) + '`' if adjudicated else '**none — all claims UNADJUDICATED**'}\n")

    # Dashboard
    def dist(key):
        counts = {}
        for a in active.values():
            counts[a.get(key) or "?"] = counts.get(a.get(key) or "?", 0) + 1
        return ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
    out.append("## Dashboard\n")
    out.append(f"- Access tiers — {dist('access_tier')}")
    out.append(f"- Evidence forms — {dist('evidence_form')}")
    out.append(f"- Stances — {dist('stance')}")
    out.append(f"- Producers — {dist('produced_by')}")
    if adjudicated:
        vc = {}
        for r in audit_recs:
            v = r.get("sentence_verdict", "?")
            vc[v] = vc.get(v, 0) + 1
        out.append(f"- Audit verdicts — {', '.join(f'{k}: {v}' for k, v in sorted(vc.items()))}")
    out.append("")

    bindings = scan_draft(args.draft)
    bound_ids = {aid for b in bindings for aid in b["anchor_ids"]}
    inv_ids = {aid for row in (inventory or []) for aid in (row.get("anchor_ids") or [])}
    unmatched_tags = sorted(aid for aid in bound_ids if aid not in active)

    # Action required (first, always)
    out.append("## Action required\n")
    action_items = []
    for r in audit_recs:
        if r.get("severity") == "HIGH" or r.get("sentence_verdict") in ACTION_VERDICTS:
            action_items.append(
                f"- **{r.get('sentence_verdict')}** ({r.get('severity')}) — "
                f"{r.get('cite_key')} at {r.get('manuscript_loc')}: "
                f"“{(r.get('manuscript_quote') or '')[:160]}”")
    # Stale claims: audit fingerprint vs current draft sentence
    if args.draft and audit_recs:
        fp_now = {}
        for b in bindings:
            fp_now[fingerprint(b["sentence"])] = b
        for r in audit_recs:
            fp = r.get("claim_fingerprint")
            if fp and fp not in fp_now:
                action_items.append(
                    f"- **STALE** — audited claim no longer found verbatim in draft "
                    f"({r.get('cite_key')}, was at {r.get('manuscript_loc')}); re-adjudicate")
    for aid in unmatched_tags:
        action_items.append(f"- **UNMATCHED TAG** — draft references `{aid}` but the ledger has no such anchor")
    weak = [a for a in active.values()
            if a.get("evidence_form") == "metadata_only" or a.get("access_tier") == "T4_none"]
    for a in weak:
        action_items.append(f"- **METADATA-ONLY** — {a.get('cite_key')} `{a.get('anchor_id')}` "
                            f"cited without quotable evidence ({a.get('claim_kind')})")
    out.extend(action_items if action_items else ["*(nothing flagged)*"])
    out.append("")

    # Claims by manuscript section (draft order)
    if bindings:
        out.append("## Claims in the draft (by section)\n")
        current = None
        for b in bindings:
            if b["section"] != current:
                current = b["section"]
                out.append(f"### {current}\n")
            out.append(f"**Our prose** (line {b['line_no']}): {b['sentence']}\n")
            for aid in b["anchor_ids"]:
                a = active.get(aid)
                if a:
                    out.extend(anchor_card(a, audit_by_anchor, args.compact))
                else:
                    out.append(f"- **UNRESOLVED** `{aid}`")
            out.append("")

    # Map / inventory claims
    if inventory:
        out.append("## Landscape-map claims (claim inventory)\n")
        for row in inventory:
            out.append(f"**{row.get('row_id', '?')}** ({row.get('claim_kind', '?')}): "
                       f"{row.get('claim_text', '')}\n")
            for aid in row.get("anchor_ids") or []:
                a = active.get(aid)
                if a:
                    out.extend(anchor_card(a, audit_by_anchor, args.compact))
                else:
                    out.append(f"- **UNRESOLVED** `{aid}`")
            if not row.get("anchor_ids"):
                out.append("- *(no anchors — uncovered row)*")
            out.append("")

    # Unused anchors
    unused = [a for aid, a in sorted(active.items())
              if aid not in bound_ids and aid not in inv_ids]
    out.append("## Unused anchors\n")
    if unused:
        for a in unused:
            out.extend(anchor_card(a, audit_by_anchor, compact=True))
    else:
        out.append("*(none — every active anchor is bound to a claim)*")
    out.append("")

    # Source index
    out.append("## Source index\n")
    by_cite = {}
    for aid, a in active.items():
        by_cite.setdefault(a.get("cite_key", "?"), []).append(aid)
    for ck in sorted(by_cite):
        out.append(f"- **{ck}**: {', '.join('`' + i + '`' for i in sorted(by_cite[ck]))}")
    out.append("")
    return "\n".join(out)


def render_brief(args, active, audit_by_anchor):
    kinds = set(args.kinds.split(",")) if args.kinds else None
    cite_keys = set(args.cite_keys.split(",")) if args.cite_keys else None
    pool = []
    for a in active.values():
        if kinds and a.get("claim_kind") not in kinds:
            continue
        if cite_keys and a.get("cite_key") not in cite_keys:
            continue
        pool.append(a)

    def rank(a):
        audited_ok = any(r.get("sentence_verdict") == "CLAIM-VERIFIED"
                         for r in audit_by_anchor.get(a.get("anchor_id"), []))
        return (0 if audited_ok else 1, TIER_RANK.get(a.get("access_tier"), 9))

    pool.sort(key=rank)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out = [f"# Evidence Brief — {args.section or 'all sections'}\n",
           f"*Generated {now}. Best evidence first (audited > T1 > T2 > T0 > T3 > T4). "
           f"Drafting contract: stay faithful to these passages (direction, magnitude, "
           f"population); tag evidence-bearing sentences `<!--ev: anchor_id-->`; do not "
           f"re-derive claims from memory.*\n",
           f"Filters: kinds={args.kinds or 'all'}; cite_keys={args.cite_keys or 'all'}; "
           f"{len(pool)} anchor(s).\n"]
    by_claim = {}
    for a in pool:
        key = a.get("claim_text") or "(no claim snapshot)"
        by_claim.setdefault(key, []).append(a)
    for claim, anchors in by_claim.items():
        out.append(f"## {claim[:200]}\n")
        for a in anchors:
            out.extend(anchor_card(a, audit_by_anchor, compact=False))
        out.append("")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--proj", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--draft", default=None,
                   help="Canonical draft (resolve via resolve-canonical-draft.sh)")
    p.add_argument("--slug", default=None)
    p.add_argument("--compact", action="store_true")
    p.add_argument("--brief", action="store_true")
    p.add_argument("--section", default=None)
    p.add_argument("--kinds", default=None)
    p.add_argument("--cite-keys", dest="cite_keys", default=None)
    args = p.parse_args()

    if not os.path.isdir(args.proj):
        print(f"ERROR: project dir not found: {args.proj}", file=sys.stderr)
        return 1
    active, superseded, errors = load_ledger(args.proj)
    if not active:
        print("NOTHING TO RENDER: no active anchors in evidence/claim-anchors.ndjson",
              file=sys.stderr)
        return 3
    audit_path, audit_by_anchor, audit_recs = load_audit(args.proj)
    inventory = load_inventory(args.proj)

    if args.brief:
        text = render_brief(args, active, audit_by_anchor)
    else:
        text = render_dossier(args, active, superseded, errors, inventory,
                              audit_path, audit_by_anchor, audit_recs)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"WROTE: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
