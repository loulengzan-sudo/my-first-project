"""
dspy_demo.py — Does DSPy actually optimize a prompt? A runnable test (same data as TextGrad).

DSPy's philosophy ("signatures, not strings"): you DECLARE the task as a
typed Signature, pick a Module, and an *optimizer* COMPILES a prompt for it against a metric
— you never hand-write the prompt string. Here the optimizer is BootstrapFewShot, which
searches the training set for demonstrations (few-shot examples) that maximize gold-label
accuracy, and bakes the best ones into the prompt.

Same task and data as the TextGrad demo: label a described internet activity as CAPITAL vs
LEISURE under a deliberately NARROW codebook (only transactional/official ACTIONS are
CAPITAL; all information-seeking and self-directed learning is LEISURE — see CODEBOOK.md).
A vague zero-shot prompt follows intuition and mislabels the info-seeking items. We test
whether DSPy's compiled program recovers the narrow boundary.

Contrast with TextGrad:
    TextGrad  : rewrites the *instruction* via natural-language "textual gradients".
    DSPy      : keeps the instruction, *selects demonstrations* (and, with MIPRO/COPRO,
                can also propose instructions) to maximize the metric.

Runs on a LOCAL model via Ollama (litellm 'ollama_chat/...'): no API key, no cost.

Run:  <venv>/bin/python dspy_demo.py
Env:  DSPY_MODEL=qwen2.5:7b  OLLAMA_BASE_URL=http://localhost:11434
"""
import os
import re
import csv
import json
import time
import datetime

import dspy
from dspy.teleprompt import BootstrapFewShot, MIPROv2

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)

MODEL = os.environ.get("DSPY_MODEL", "qwen2.5:7b")
API_BASE = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

_log = []


def log(m=""):
    print(m, flush=True)
    _log.append(str(m))


def norm(s):
    u = (s or "").upper()
    ic, il = u.find("CAPITAL"), u.find("LEISURE")
    if ic == -1 and il == -1:
        return "?"
    return "CAPITAL" if ic != -1 and (il == -1 or ic < il) else "LEISURE"


# --- the DECLARATIVE signature: vague instruction on purpose (headroom to optimize) ---
class ClassifyUse(dspy.Signature):
    """Classify the internet activity as CAPITAL (capital-enhancing use) or LEISURE (recreational use)."""
    activity: str = dspy.InputField(desc="a short description of an internet activity")
    label: str = dspy.OutputField(desc="exactly one word: CAPITAL or LEISURE")


def load():
    rows = list(csv.DictReader(open(os.path.join(HERE, "synthetic_data.csv"))))
    to_ex = lambda r: dspy.Example(activity=r["text"], label=r["label"],
                                   _id=r["id"], _counter=r["counterintuitive"]).with_inputs("activity")
    pick = lambda s: [to_ex(r) for r in rows if r["split"] == s]
    return pick("train"), pick("val"), pick("test")


def metric(example, pred, trace=None):
    return norm(getattr(pred, "label", "")) == example.label.upper()


def accuracy(program, examples):
    preds, correct = [], 0
    for ex in examples:
        try:
            out = norm(program(activity=ex.activity).label)
        except Exception as e:
            out = "?"
        ok = (out == ex.label.upper())
        correct += ok
        preds.append({"id": ex._id, "text": ex.activity, "gold": ex.label,
                      "pred": out, "correct": int(ok), "counterintuitive": ex._counter})
    return correct / len(examples), preds


def capture_prompt(program, example):
    """The fully-rendered chat prompt DSPy actually sends for one prediction
    (instruction + any demos + field formatting) — 'what a compiled prompt looks like'."""
    try:
        program(activity=example.activity)
        msgs = (dspy.settings.lm.history[-1].get("messages") or [])
        return "\n\n".join(f"[{m.get('role', '?').upper()}]\n{m.get('content', '')}" for m in msgs)
    except Exception as e:
        return f"(could not capture prompt: {e})"


def main():
    t0 = time.time()
    log("=" * 78)
    log("DSPy prompt-optimization test — can BootstrapFewShot learn a narrow codebook?")
    log(f"model={MODEL}  api_base={API_BASE}")
    log("=" * 78)

    # max_tokens must be generous: the classifier only emits one word, but MIPROv2 reuses
    # this LM to *propose instructions*, which need room (60 tokens truncates them).
    lm = dspy.LM(f"ollama_chat/{MODEL}", api_base=API_BASE, api_key="",
                 temperature=0.0, max_tokens=1200)
    dspy.configure(lm=lm)

    train, val, test = load()
    log(f"data: train={len(train)}  val={len(val)}  test={len(test)}  (balanced)")

    program = dspy.Predict(ClassifyUse)

    # ---- baseline: zero-shot declarative program ----
    log("\n[baseline] zero-shot dspy.Predict (vague signature) ...")
    base_acc, base_preds = accuracy(program, test)
    log(f"           test acc = {base_acc:.3f}")
    open(os.path.join(OUT, "rendered_prompt_baseline.txt"), "w").write(
        capture_prompt(program, test[0]) + "\n")

    def dump(path, preds):
        w = csv.DictWriter(open(path, "w", newline=""),
                           fieldnames=["id", "text", "gold", "pred", "correct", "counterintuitive"])
        w.writeheader()
        w.writerows(preds)

    def fixed_counter(before_preds, after_preds):
        aft = {p["id"]: p for p in after_preds}
        return [{"id": b["id"], "text": b["text"], "gold": b["gold"],
                 "before": b["pred"], "after": aft[b["id"]]["pred"]}
                for b in before_preds
                if b["counterintuitive"] == "1" and aft.get(b["id"]) and
                int(aft[b["id"]]["correct"]) > int(b["correct"])]

    dump(os.path.join(OUT, "predictions_baseline.csv"), base_preds)

    # ---- Optimizer 1: BootstrapFewShot — SELECTS DEMONSTRATIONS (keeps the instruction) ----
    log("\n[optimizer 1] BootstrapFewShot — bootstraps few-shot demos (max_bs=4, max_labeled=8) ...")
    bfs = BootstrapFewShot(metric=metric, max_bootstrapped_demos=4, max_labeled_demos=8)
    bfs_prog = bfs.compile(program, trainset=train)
    bfs_acc, bfs_preds = accuracy(bfs_prog, test)
    log(f"              test acc = {bfs_acc:.3f}  ({bfs_acc-base_acc:+.3f} vs baseline)")
    bfs_demos = [{"activity": getattr(d, "activity", None), "label": getattr(d, "label", None)}
                 for d in getattr(bfs_prog.predictors()[0], "demos", [])]
    json.dump(bfs_demos, open(os.path.join(OUT, "bootstrap_demos.json"), "w"), indent=2)
    dump(os.path.join(OUT, "predictions_bootstrap.csv"), bfs_preds)
    open(os.path.join(OUT, "rendered_prompt_bootstrap.txt"), "w").write(
        capture_prompt(bfs_prog, test[0]) + "\n")

    # ---- Optimizer 2: MIPROv2 — PROPOSES INSTRUCTIONS (+ demos), like TextGrad ----
    mipro_acc, mipro_instr, mipro_demos, mipro_preds, mipro_err = base_acc, "", [], base_preds, None
    log("\n[optimizer 2] MIPROv2(auto='light') — proposes an optimized INSTRUCTION (+ demos) ...")
    try:
        mipro = MIPROv2(metric=metric, auto="light", num_threads=1, verbose=False)
        mipro_prog = mipro.compile(program, trainset=train, valset=val,
                                   requires_permission_to_run=False)
        mipro_acc, mipro_preds = accuracy(mipro_prog, test)
        pred0 = mipro_prog.predictors()[0]
        mipro_instr = getattr(getattr(pred0, "signature", None), "instructions", "") or ""
        mipro_demos = [{"activity": getattr(d, "activity", None), "label": getattr(d, "label", None)}
                       for d in getattr(pred0, "demos", [])]
        open(os.path.join(OUT, "rendered_prompt_mipro.txt"), "w").write(
            capture_prompt(mipro_prog, test[0]) + "\n")
        log(f"              test acc = {mipro_acc:.3f}  ({mipro_acc-base_acc:+.3f} vs baseline)")
    except Exception as e:
        mipro_err = f"{type(e).__name__}: {e}"
        log(f"              MIPROv2 did not complete: {mipro_err}")
    open(os.path.join(OUT, "mipro_instruction.txt"), "w").write((mipro_instr or "(none)") + "\n")
    json.dump(mipro_demos, open(os.path.join(OUT, "mipro_demos.json"), "w"), indent=2)
    dump(os.path.join(OUT, "predictions_mipro.csv"), mipro_preds)

    dt = time.time() - t0
    log("\n" + "=" * 78)
    log("RESULT (same task/data as the TextGrad demo)")
    log(f"  baseline  (zero-shot instruction)        : test acc = {base_acc:.3f}")
    log(f"  BootstrapFewShot (select demonstrations) : test acc = {bfs_acc:.3f}  ({bfs_acc-base_acc:+.3f})")
    log(f"  MIPROv2 (optimize the instruction)       : test acc = {mipro_acc:.3f}  ({mipro_acc-base_acc:+.3f})")
    log(f"  wall time                                : {dt:.0f}s")
    log("=" * 78)

    summary = {
        "framework": "dspy", "dspy_version": dspy.__version__, "model": MODEL,
        "n_train": len(train), "n_val": len(val), "n_test": len(test),
        "baseline_test_acc": round(base_acc, 4),
        "bootstrapfewshot": {
            "optimizer": "BootstrapFewShot(max_bootstrapped_demos=4, max_labeled_demos=8)",
            "test_acc": round(bfs_acc, 4), "delta": round(bfs_acc - base_acc, 4),
            "n_demos": len(bfs_demos),
            "counterintuitive_items_fixed": fixed_counter(base_preds, bfs_preds)},
        "miprov2": {
            "optimizer": "MIPROv2(auto='light')", "error": mipro_err,
            "test_acc": round(mipro_acc, 4), "delta": round(mipro_acc - base_acc, 4),
            "optimized_instruction": mipro_instr, "n_demos": len(mipro_demos),
            "counterintuitive_items_fixed": fixed_counter(base_preds, mipro_preds)},
        "wall_time_s": round(dt, 1),
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=2)
    open(os.path.join(OUT, "run_log.txt"), "w").write("\n".join(_log) + "\n")
    log(f"\nsaved -> {OUT}/")


if __name__ == "__main__":
    main()
