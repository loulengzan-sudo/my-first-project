#!/usr/bin/env python3
"""
Embed-based distillation of RELEVANCE (MODE 9, high-fidelity variant). TF-IDF distill only reached
κ=0.607 vs gold; bge-m3 embeddings discriminate far better (as in the prefilter). This trains a
multinomial logistic head on frozen bge-m3 embeddings of the GLM-labeled rows, validates vs gold,
and (apply) scores the full reduced corpus. Reuses reduce_corpus.embed() / trim helpers.

  train : embed GLM-labeled rows -> fit LR -> report DISTILLED-vs-gold κ (relevance) -> save model
  apply : embed the reduced corpus (sharded) -> predict relevance -> write [video_id, relevance]

Usage (GPU node, GLM not needed — just the embedding model):
  python3 distill_embed.py train --labels glm_labels_relevance.csv --sample corpus_reduced/reduced_all.csv \
     --gold devset_gold_v3.csv --out-dir output/models
  python3 distill_embed.py apply --model output/models/relevance_distill_embed.joblib \
     --sample corpus_reduced/reduced_all.csv --out annotations_reduced/relevance_distilled_full.csv \
     --shard 0 --nshards 1
"""
import os, sys, csv, argparse, hashlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import reduce_corpus as rc
csv.field_size_limit(sys.maxsize)

def sh(s): return int(hashlib.md5(str(s).encode("utf-8", "ignore")).hexdigest()[:8], 16)
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")

def cmd_train(a):
    import pandas as pd, joblib
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import cohen_kappa_score, f1_score, classification_report
    os.makedirs(a.out_dir, exist_ok=True)
    lab = pd.read_csv(a.labels, dtype=str).fillna("")                      # video_id, relevance
    samp = pd.read_csv(a.sample, dtype=str).fillna("")                     # reduced_all: id,strata,title,description (already trimmed)
    d = lab.merge(samp[["video_id", "title", "description"]], on="video_id")
    d = d[d["relevance"] != ""]
    texts = [ (rc.sanitize(t) + " " + rc.sanitize(dd)).strip() for t, dd in zip(d["title"], d["description"]) ]
    y = d["relevance"].values
    print(f"train rows={len(d)}  classes={sorted(set(y))}", flush=True)
    X = rc.embed(texts, EMBED_MODEL)
    lr = LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced", n_jobs=-1)
    lr.fit(X, y)

    # validate vs gold: gold's own text, trimmed the same way (title + first-100-word desc)
    gold = pd.read_csv(a.gold, dtype=str).fillna("")
    gold = gold[gold["relevance"] != ""]
    gtexts = [ (rc.sanitize(t) + " " + rc.trim_desc(dd, 100, 600)).strip() for t, dd in zip(gold["title"], gold["description"]) ]
    gp = lr.predict(rc.embed(gtexts, EMBED_MODEL))
    k = cohen_kappa_score(gold["relevance"], gp); f = f1_score(gold["relevance"], gp, average="macro", zero_division=0)
    print(f"DISTILLED(embed) vs gold: kappa={k:.3f}  F1_macro={f:.3f}  n={len(gold)}")
    print(classification_report(gold["relevance"], gp, zero_division=0))
    out = os.path.join(a.out_dir, "relevance_distill_embed.joblib")
    joblib.dump({"lr": lr, "embed_model": EMBED_MODEL}, out)
    print(f"model -> {out}  gate({a.gate}): {'PASS' if k >= a.gate else 'FAIL'}")
    sys.exit(0 if k >= a.gate else 2)

def cmd_apply(a):
    import joblib, pandas as pd
    m = joblib.load(a.model); lr = m["lr"]; em = m["embed_model"]
    suf = f"_t{a.shard}of{a.nshards}" if a.nshards > 1 else ""
    out = a.out.replace(".csv", f"{suf}.csv") if a.nshards > 1 else a.out
    fh = open(out, "w", newline=""); w = csv.writer(fh); w.writerow(["video_id", "relevance"])
    n = 0; buf_id = []; buf_tx = []
    def flush():
        nonlocal n
        if not buf_tx: return
        preds = lr.predict(rc.embed(buf_tx, em))
        for vid, p in zip(buf_id, preds): w.writerow([vid, p]); n += 1
        fh.flush(); buf_id.clear(); buf_tx.clear()
    # stream reduced_all.csv with stdlib csv (already clean/trimmed)
    with open(a.sample, newline="", encoding="utf-8", errors="ignore") as f:
        for r in csv.DictReader(f):
            vid = r.get("video_id")
            if not vid: continue
            if a.nshards > 1 and sh(vid) % a.nshards != a.shard: continue
            buf_id.append(vid); buf_tx.append((rc.sanitize(r.get("title","")) + " " + rc.sanitize(r.get("description",""))).strip())
            if len(buf_tx) >= a.batch:
                flush()
                if n % 100000 == 0: print(f"  scored={n}", flush=True)
    flush(); fh.close()
    print(f"DONE scored={n} (shard {a.shard}/{a.nshards}) -> {out}")

def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--labels", required=True); t.add_argument("--sample", required=True); t.add_argument("--gold", required=True)
    t.add_argument("--out-dir", default="output/models"); t.add_argument("--gate", type=float, default=0.70); t.set_defaults(fn=cmd_train)
    p = sub.add_parser("apply")
    p.add_argument("--model", required=True); p.add_argument("--sample", required=True)
    p.add_argument("--out", default="annotations_reduced/relevance_distilled_full.csv")
    p.add_argument("--shard", type=int, default=0); p.add_argument("--nshards", type=int, default=1); p.add_argument("--batch", type=int, default=2048)
    p.set_defaults(fn=cmd_apply)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
