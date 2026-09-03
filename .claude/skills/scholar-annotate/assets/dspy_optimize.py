#!/usr/bin/env python3
"""
DSPy prompt optimization for scholar-annotate (MODE 6), tuned for a conditional two-field
(relevance + frame) task on a LOCAL open-weight model. Builds a typed ChainOfThought annotator from the compiled codebook, few-shot-
optimizes it against gold with BootstrapFewShot, and reports HELD-OUT Cohen κ for both relevance
and discourse_frame (parallel eval). Archives the compiled program + hash.

Key differences from the generic optimizer:
  * STRATIFIED split — every positive-relevance row (all frames) goes into the bootstrap trainset,
    so the optimizer can actually pick demos for the rare frame classes.
  * κ reporting (relevance + frame), not just exact-match, so it lines up with the MODE 7 gate.
  * bounded, threaded held-out eval so a slow local model stays within a job's wall-clock.

Usage (local GLM server):
  python3 dspy_optimize.py --gold devset_gold_v3.csv --codebook codebook.md \
     --text-cols title,description --provider local --api-base $GLM_API_BASE --model glm-5.2 \
     --key-env GLM_API_KEY --out annotator_program_glm.json --demos 6 --max-test 160 --threads 4
"""
import os, sys, json, hashlib, argparse
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import codebook_schema

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True); ap.add_argument("--codebook", required=True)
    ap.add_argument("--text-cols", required=True); ap.add_argument("--provider", default="openai")
    ap.add_argument("--model", required=True); ap.add_argument("--api-base"); ap.add_argument("--key-env", default="OPENAI_API_KEY")
    ap.add_argument("--out", default="annotator_program_glm.json"); ap.add_argument("--demos", type=int, default=6)
    ap.add_argument("--max-neg-train", type=int, default=250); ap.add_argument("--max-test", type=int, default=160)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--no-cot", action="store_true", help="use dspy.Predict (no reasoning) instead of ChainOfThought")
    ap.add_argument("--positive-label", default=None,
                    help="value of the gating field defining the positive stratum "
                         "(default: read from the codebook's conditional.only_if)")
    a = ap.parse_args()
    import dspy, pandas as pd
    from typing import Literal
    from sklearn.metrics import cohen_kappa_score
    cb = codebook_schema.load(a.codebook); tcols = a.text_cols.split(","); of = cb["out_fields"]; labels = cb["labels"]

    key = os.environ.get(a.key_env) or ("sk-local" if a.provider == "local" else None)
    lm = dspy.LM(f"openai/{a.model}", api_key=key, api_base=a.api_base, temperature=0.0, max_tokens=1200, cache=False)
    dspy.configure(lm=lm)

    # typed signature built from the codebook
    ns = {"__doc__": cb["system"][:1800], "__annotations__": {}}
    for c in tcols:
        ns[c] = dspy.InputField(); ns["__annotations__"][c] = str
    for f in of:
        allowed = labels.get(f)
        ns[f] = dspy.OutputField(desc=f"one of {allowed}" if allowed else "")
        ns["__annotations__"][f] = (Literal[tuple(allowed)] if allowed else str)
    Sig = type("Annotate", (dspy.Signature,), ns)
    program = dspy.Predict(Sig) if a.no_cot else dspy.ChainOfThought(Sig)  # A/B: no-CoT = ~10x fewer output tokens
    print(f"module={'Predict(no-CoT)' if a.no_cot else 'ChainOfThought'}", flush=True)

    df = pd.read_csv(a.gold, dtype=str).fillna("")
    df = df[df[of[0]] != ""].reset_index(drop=True)
    def mkex(r): return dspy.Example(**{c: str(r.get(c, ""))[:400] for c in tcols},
                                     **{f: r.get(f, "") for f in of}).with_inputs(*tcols)
    # The positive stratum is whatever value of the gating field switches the conditional
    # field on. Read it from the codebook instead of hardcoding one project's label —
    # a wrong literal here silently empties the stratum and every rare class of the
    # conditional field ends up with 0 demonstrations (the failure mode described above).
    pos = a.positive_label
    if pos is None and len(of) > 1:
        pos = ((cb.get("conditional", {}).get(of[1], {}) or {}).get("only_if", {}) or {}).get(of[0])
    if pos is None:
        sys.exit("cannot determine the positive stratum: pass --positive-label, or declare "
                 "\"conditional\": {\"<field>\": {\"only_if\": {\"<gate-field>\": \"<value>\"}}} in the codebook")
    if pos not in set(df[of[0]].unique()):
        sys.exit(f"--positive-label {pos!r} does not occur in gold column {of[0]!r}")
    meta = df[df[of[0]] == pos]; nonmeta = df[df[of[0]] != pos]
    # keep 70% of the positive stratum in the bootstrap pool (frame coverage) but HOLD OUT 30%
    # so the test set actually contains positive rows -> the conditional field's kappa is measurable
    meta_tr = meta.sample(frac=0.7, random_state=5); meta_te = meta.drop(meta_tr.index)
    neg_tr = nonmeta.sample(min(len(nonmeta), a.max_neg_train), random_state=1)
    train_df = pd.concat([meta_tr, neg_tr])
    neg_pool = nonmeta.drop(neg_tr.index)
    n_neg_te = max(0, a.max_test - len(meta_te))
    test_df = pd.concat([meta_te, neg_pool.sample(min(len(neg_pool), n_neg_te), random_state=2)])
    trainset = [mkex(r) for _, r in train_df.iterrows()]
    testset = [mkex(r) for _, r in test_df.iterrows()]
    print(f"trainset={len(trainset)} ({pos}={len(meta_tr)}), testset={len(testset)} "
          f"({pos}={len(meta_te)})", flush=True)

    def metric(g, p, trace=None):
        return float(all(getattr(p, f, None) == getattr(g, f, None) for f in of[:2]))

    opt = dspy.BootstrapFewShot(metric=metric, max_bootstrapped_demos=a.demos, max_labeled_demos=a.demos)
    compiled = opt.compile(program, trainset=trainset)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True); compiled.save(a.out)
    print(f"compiled + saved -> {a.out}", flush=True)

    # threaded held-out eval -> κ
    def run_one(ex):
        try:
            p = compiled(**ex.inputs())
            return (str(getattr(ex, of[0], "") or ""), str(getattr(p, of[0], "") or ""),
                    str(getattr(ex, of[1], "") or ""), str(getattr(p, of[1], "") or ""))
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=a.threads) as exr:
        res = [r for r in exr.map(run_one, testset) if r]
    if res:
        g_rel, p_rel, g_fr, p_fr = zip(*res)
        em = sum(1 for i in range(len(res)) if g_rel[i] == p_rel[i] and g_fr[i] == p_fr[i]) / len(res)
        print(f"held-out n={len(res)} exact2={em:.3f}")
        try: print(f"  relevance kappa={cohen_kappa_score(g_rel, p_rel):.3f}")
        except Exception as e: print("  relevance kappa err:", e)
        try: print(f"  frame kappa={cohen_kappa_score(g_fr, p_fr):.3f}")
        except Exception as e: print("  frame kappa err:", e)
    h = hashlib.sha256(open(a.out, "rb").read()).hexdigest()[:12]
    print(f"program -> {a.out}  hash={h}  model={a.model}  demos={a.demos}  temp=0")

if __name__ == "__main__":
    main()
