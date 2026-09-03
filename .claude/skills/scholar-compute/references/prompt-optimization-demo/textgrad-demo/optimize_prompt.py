"""
optimize_prompt.py — Does TextGrad actually optimize a prompt? A runnable test.

We optimize the *system prompt* of a text classifier with TextGrad's "textual gradient
descent" (TGD), using GOLD LABELS as the loss (not an LLM rubric — the rigorous,
gold-label version). It runs on a LOCAL model via Ollama's
OpenAI-compatible endpoint: no API key, no cost.

The instrument gap.  A 7B model already labels the *intuitive* capital-vs-leisure split at
~100% from a vague prompt, so there is nothing to optimize. Our codebook is deliberately
NARROW (see make_data.py / CODEBOOK.md): only transactional / official / economic ACTIONS
count as CAPITAL; all information-seeking and self-directed learning (reading the news,
looking things up, watching a tutorial, practicing on an app) is LEISURE. A vague prompt
follows intuition and mislabels those "info-seeking" items as CAPITAL. TextGrad must
discover the narrow boundary from labeled training examples and write it into the prompt.

Mechanism:
    loss      = a string: CORRECT / INCORRECT + why      (StringBasedFunction, gold-label)
    backward()= the engine writes a "textual gradient": what to change, and why
    step()    = the engine rewrites the system-prompt Variable using that gradient
                (constrained to keep the strict one-word output format)

Protocol: vague prompt -> TGD step(s) on the train split (each step a contrastive batch of
CAPITAL + LEISURE items) -> best checkpoint on val -> report accuracy on an untouched test
split. Prompts, per-item predictions, and metrics land in ./outputs/.

Run:  <venv>/bin/python optimize_prompt.py
Env:  TG_MODEL=qwen2.5:7b  OLLAMA_BASE_URL=http://localhost:11434/v1  TG_STEPS=1  TG_NPER=3
"""
import os
import re
import csv
import json
import time
import datetime

from openai import OpenAI
import textgrad as tg
from textgrad.autograd import StringBasedFunction
from textgrad.engine.local_model_openai_api import ChatExternalClient

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "outputs")
os.makedirs(OUT, exist_ok=True)

BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1")
MODEL = os.environ.get("TG_MODEL", "qwen2.5:7b")   # one model for fwd+bwd (avoid Ollama swaps)
STEPS = int(os.environ.get("TG_STEPS", "1"))
NPER = int(os.environ.get("TG_NPER", "3"))          # items per class per step

LABELS = ("CAPITAL", "LEISURE")
INIT_PROMPT = ("Classify the internet activity as CAPITAL (capital-enhancing use) or "
               "LEISURE (recreational use). Answer with one word.")

_log = []


def log(msg=""):
    print(msg, flush=True)
    _log.append(str(msg))


def load_data():
    rows = list(csv.DictReader(open(os.path.join(HERE, "synthetic_data.csv"))))
    pick = lambda s: [r for r in rows if r["split"] == s]
    return pick("train"), pick("val"), pick("test")


def parse_label(resp):
    u = (resp or "").upper()
    ic, il = u.find("CAPITAL"), u.find("LEISURE")
    if ic == -1 and il == -1:
        return "?"
    if ic != -1 and (il == -1 or ic < il):
        return "CAPITAL"
    return "LEISURE"


def evaluate(engine, prompt_text, rows):
    preds, correct = [], 0
    for r in rows:
        resp = engine.generate(r["text"], system_prompt=prompt_text,
                               temperature=0, max_tokens=20)
        lab = parse_label(resp)
        ok = (lab == r["label"])
        correct += ok
        preds.append({"id": r["id"], "text": r["text"], "gold": r["label"],
                      "pred": lab, "correct": int(ok),
                      "counterintuitive": r["counterintuitive"],
                      "raw": (resp or "").strip().replace("\n", " ")[:120]})
    return correct / len(rows), preds


def eval_string(prediction, ground_truth_answer, input_text):
    pl = parse_label(prediction.value)
    gl = ground_truth_answer.value
    if pl == gl:
        return f"CORRECT. The prediction '{pl}' matches the gold label '{gl}'."
    return (f"INCORRECT. Activity: \"{input_text.value}\". The classifier answered '{pl}', "
            f"but the correct label is '{gl}'. The codebook is NARROWER than intuition: "
            f"study the labeled examples to find the real boundary between CAPITAL and "
            f"LEISURE, then write explicit definitions of both labels and a decision rule "
            f"into the prompt so activities like this are labeled '{gl}'.")


def main():
    t0 = time.time()
    log("=" * 78)
    log("TextGrad prompt-optimization test — can it learn a narrow codebook from labels?")
    log(f"model={MODEL} (fwd+bwd)  steps={STEPS}  n_per_class={NPER}  endpoint={BASE_URL}")
    log("=" * 78)

    train, val, test = load_data()
    trC = [r for r in train if r["label"] == "CAPITAL"]
    trL = [r for r in train if r["label"] == "LEISURE"]  # counterintuitive items first
    log(f"data: train={len(train)}  val={len(val)}  test={len(test)}  (balanced)")

    client = OpenAI(base_url=BASE_URL, api_key="ollama")
    engine = ChatExternalClient(client=client, model_string=MODEL)
    tg.set_backward_engine(engine, override=True)

    system_prompt = tg.Variable(
        INIT_PROMPT, requires_grad=True,
        role_description=("the system prompt of a classifier that labels a described "
                          "internet activity as CAPITAL or LEISURE; improve it to maximize "
                          "accuracy against the (narrow) gold codebook"))
    model = tg.BlackboxLLM(engine, system_prompt)
    optimizer = tg.TGD(
        parameters=[system_prompt],
        constraints=["The improved prompt MUST still instruct the model to answer with "
                     "exactly one word: CAPITAL or LEISURE, and nothing else."])
    loss_fn = StringBasedFunction(
        eval_string,
        function_purpose="check whether the predicted label matches the gold label and explain any error")

    log("\n[step 0] evaluating the INITIAL (vague, intuition-following) prompt ...")
    v0, _ = evaluate(engine, system_prompt.value, val)
    a0, init_preds = evaluate(engine, system_prompt.value, test)
    log(f"           val acc = {v0:.3f}   test acc = {a0:.3f}")
    traj = [{"step": 0, "val_acc": v0, "test_acc": a0, "prompt": system_prompt.value}]
    best = {"step": 0, "val_acc": v0, "test_acc": a0,
            "prompt": system_prompt.value, "test_preds": init_preds}

    for step in range(1, STEPS + 1):
        s = (step - 1) * NPER
        cset = [trC[(s + k) % len(trC)] for k in range(NPER)]
        lset = [trL[(s + k) % len(trL)] for k in range(NPER)]
        batch = cset + lset
        log(f"\n[step {step}] TGD on {len(batch)} items "
            f"(CAPITAL ids {[r['id'] for r in cset]}, LEISURE ids {[r['id'] for r in lset]}) ...")
        optimizer.zero_grad()
        losses = []
        for r in batch:
            x = tg.Variable(r["text"], requires_grad=False,
                            role_description="a description of an internet activity")
            gt = tg.Variable(r["label"], requires_grad=False,
                             role_description="the correct label for this activity")
            losses.append(loss_fn(inputs={"prediction": model(x),
                                          "ground_truth_answer": gt, "input_text": x}))
        tg.sum(losses).backward()
        optimizer.step()

        vacc, _ = evaluate(engine, system_prompt.value, val)
        tacc, tpreds = evaluate(engine, system_prompt.value, test)
        log(f"           val acc = {vacc:.3f}   test acc = {tacc:.3f}")
        log(f"           prompt now: {system_prompt.value[:110]!r}...")
        traj.append({"step": step, "val_acc": vacc, "test_acc": tacc,
                     "prompt": system_prompt.value})
        if vacc > best["val_acc"]:
            best = {"step": step, "val_acc": vacc, "test_acc": tacc,
                    "prompt": system_prompt.value, "test_preds": tpreds}
            log("           ^ new best-on-val checkpoint")

    dt = time.time() - t0
    delta = best["test_acc"] - traj[0]["test_acc"]
    log("\n" + "=" * 78)
    log("RESULT")
    log(f"  initial  prompt : test acc = {traj[0]['test_acc']:.3f}")
    log(f"  optimized prompt: test acc = {best['test_acc']:.3f}  "
        f"(best-on-val at step {best['step']}, val {best['val_acc']:.3f})")
    log(f"  change          : {delta:+.3f} test accuracy")
    log(f"  wall time       : {dt:.0f}s")
    log("=" * 78)

    open(os.path.join(OUT, "initial_prompt.txt"), "w").write(traj[0]["prompt"] + "\n")
    open(os.path.join(OUT, "optimized_prompt.txt"), "w").write(best["prompt"] + "\n")
    json.dump(traj, open(os.path.join(OUT, "trajectory.json"), "w"), indent=2)

    def dump(path, preds):
        w = csv.DictWriter(open(path, "w", newline=""),
                           fieldnames=["id", "text", "gold", "pred", "correct",
                                       "counterintuitive", "raw"])
        w.writeheader()
        w.writerows(preds)
    dump(os.path.join(OUT, "predictions_initial.csv"), init_preds)
    dump(os.path.join(OUT, "predictions_optimized.csv"), best["test_preds"])

    # which counterintuitive test items did optimization fix?
    after = {p["id"]: p for p in best["test_preds"]}
    fixed = [{"id": b["id"], "text": b["text"], "gold": b["gold"],
              "before": b["pred"], "after": after[b["id"]]["pred"]}
             for b in init_preds
             if b["counterintuitive"] == "1" and after.get(b["id"]) and
             int(after[b["id"]]["correct"]) > int(b["correct"])]

    summary = {
        "model": MODEL, "steps": STEPS, "n_per_class": NPER,
        "n_train": len(train), "n_val": len(val), "n_test": len(test),
        "initial_test_acc": round(traj[0]["test_acc"], 4),
        "optimized_test_acc": round(best["test_acc"], 4),
        "delta_test_acc": round(delta, 4), "best_step": best["step"],
        "best_val_acc": round(best["val_acc"], 4), "wall_time_s": round(dt, 1),
        "counterintuitive_items_fixed": fixed,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    json.dump(summary, open(os.path.join(OUT, "summary.json"), "w"), indent=2)
    open(os.path.join(OUT, "run_log.txt"), "w").write("\n".join(_log) + "\n")
    log(f"\nsaved -> {OUT}/")


if __name__ == "__main__":
    main()
