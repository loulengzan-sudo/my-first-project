#!/usr/bin/env python3
"""
Deploy a DSPy-compiled annotator (from dspy_optimize.py) at scale against a LOCAL GLM.

Loads the *exact* compiled program (CoT + bootstrapped demos that MODE 7 validated) and annotates
either a CSV (gold, for the validation gate) or a corpus-spec JSON (the full reduced corpus),
threaded + sharded + resumable, writing `[id, strata, <out_fields>]` CSVs in the SAME shape as
annotate_engine.py — so the existing merge / validate / consume steps are unchanged. This is the
"frozen deployment" of the optimized program: no prompt is re-encoded, so what runs == what passed.

Usage:
  # validate on gold:
  python3 dspy_run.py --program annotator_program_glm.json --codebook codebook.md \
     --input devset_gold_v3.csv --text-cols title,description --api-base $GLM_API_BASE \
     --out-dir glm_val_dspy --threads 8
  # scale on the reduced corpus (job-array shard i of N):
  python3 dspy_run.py --program annotator_program_glm.json --codebook codebook.md \
     --input glm_reduced_input.json --text-cols title,description --api-base $GLM_API_BASE \
     --out-dir annotations_reduced --shard $i --nshards $N --threads 8
"""
import os, sys, csv, json, argparse, hashlib, threading
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import codebook_schema
csv.field_size_limit(sys.maxsize)

def sh(s): return int(hashlib.md5(str(s).encode("utf-8", "ignore")).hexdigest()[:8], 16)

def chunks(it, n):
    b = []
    for x in it:
        b.append(x)
        if len(b) >= n: yield b; b = []
    if b: yield b

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--program", required=True); ap.add_argument("--codebook", required=True)
    ap.add_argument("--input", required=True)                 # gold CSV or corpus-spec .json
    ap.add_argument("--text-cols", required=True); ap.add_argument("--id-col", default="video_id")
    ap.add_argument("--strata-col", default="query_category")
    ap.add_argument("--model", default="glm-5.2"); ap.add_argument("--api-base"); ap.add_argument("--key-env", default="GLM_API_KEY")
    ap.add_argument("--out-dir", default="annotations_dspy"); ap.add_argument("--shard", type=int, default=0); ap.add_argument("--nshards", type=int, default=1)
    ap.add_argument("--limit", type=int); ap.add_argument("--threads", type=int, default=8); ap.add_argument("--shards-out", type=int, default=64)
    ap.add_argument("--no-cot", action="store_true", help="load a dspy.Predict program (no reasoning) instead of ChainOfThought")
    ap.add_argument("--json-adapter", action="store_true", help="use dspy.JSONAdapter (compact output) instead of ChatAdapter")
    ap.add_argument("--max-tokens", type=int, default=1200, help="cap generation length (lower = faster with JSON output)")
    a = ap.parse_args()
    import dspy, pandas as pd
    from typing import Literal
    cb = codebook_schema.load(a.codebook); tcols = a.text_cols.split(","); of = cb["out_fields"]; labels = cb["labels"]
    idc = a.id_col; strata = a.strata_col

    key = os.environ.get(a.key_env) or "sk-local"
    lm = dspy.LM(f"openai/{a.model}", api_key=key, api_base=a.api_base, temperature=0.0, max_tokens=a.max_tokens, cache=False)
    if a.json_adapter:
        dspy.configure(lm=lm, adapter=dspy.JSONAdapter())               # compact JSON output vs verbose ChatAdapter markers
        print("adapter=JSONAdapter", flush=True)
    else:
        dspy.configure(lm=lm)
    ns = {"__doc__": cb["system"][:1800], "__annotations__": {}}
    for c in tcols: ns[c] = dspy.InputField(); ns["__annotations__"][c] = str
    for f in of:
        allowed = labels.get(f); ns[f] = dspy.OutputField(desc=f"one of {allowed}" if allowed else "")
        ns["__annotations__"][f] = (Literal[tuple(allowed)] if allowed else str)
    Sig = type("Annotate", (dspy.Signature,), ns)
    program = (dspy.Predict(Sig) if a.no_cot else dspy.ChainOfThought(Sig)); program.load(a.program)
    print(f"loaded program {a.program}; module={'Predict' if a.no_cot else 'ChainOfThought'}; out_fields={of}", flush=True)

    os.makedirs(a.out_dir, exist_ok=True)
    suf = f"_t{a.shard}of{a.nshards}" if a.nshards > 1 else ""
    ckpt = os.path.join(a.out_dir, f"processed{suf}.txt"); failedp = os.path.join(a.out_dir, f"failed{suf}.txt")
    HEAD = [idc, strata] + of
    done = set(open(ckpt).read().split()) if os.path.exists(ckpt) else set()

    def rows():
        if str(a.input).endswith(".json"):
            import reduce_corpus
            spec = json.load(open(a.input))
            for r in reduce_corpus.iter_spec_rows(spec, a.shard, a.nshards): yield r
        else:
            df = pd.read_csv(a.input, dtype=str).fillna("")
            for r in df.to_dict("records"):
                v = str(r.get(idc))
                if a.nshards > 1 and sh(v) % a.nshards != a.shard: continue
                yield r

    files = {}; locks = {}; guard = threading.Lock(); cklock = threading.Lock()
    ck = open(ckpt, "a"); fl = open(failedp, "a"); st = {"n": 0, "f": 0}
    def writer(vid):
        with guard:
            p = os.path.join(a.out_dir, f"annot{suf}_{sh(vid) % a.shards_out:03d}.csv")
            if p not in files:
                new = not os.path.exists(p) or os.path.getsize(p) == 0
                fh = open(p, "a", newline="", encoding="utf-8"); w = csv.DictWriter(fh, fieldnames=HEAD, extrasaction="ignore")
                if new: w.writeheader(); fh.flush()
                files[p] = (fh, w); locks[p] = threading.Lock()
            return locks[p], files[p]
    def annotate(r):
        vid = str(r.get(idc))
        if not vid or vid in done: return
        try:
            inp = {c: str(r.get(c, ""))[:400] for c in tcols}
            p = program(**inp)
            row = {idc: vid, strata: str(r.get(strata, ""))}
            for f in of: row[f] = str(getattr(p, f, "") or "")
            lock, (fh, w) = writer(vid)
            with lock: w.writerow(row); fh.flush()
            with cklock:
                ck.write(vid + "\n"); ck.flush(); st["n"] += 1
                if st["n"] % 500 == 0: print(f"  annotated={st['n']} failed={st['f']}", flush=True)
        except Exception:
            with cklock: fl.write(vid + "\n"); fl.flush(); st["f"] += 1

    cnt = 0
    with ThreadPoolExecutor(max_workers=a.threads) as ex:
        for batch in chunks(rows(), a.threads * 20):
            list(ex.map(annotate, batch)); cnt += len(batch)
            if a.limit and cnt >= a.limit: break
    ck.close(); fl.close()
    for fh, _ in files.values():
        try: fh.close()
        except Exception: pass
    print(f"DONE annotated={st['n']} failed={st['f']} (shard {a.shard}/{a.nshards}) -> {a.out_dir}")

if __name__ == "__main__":
    main()
