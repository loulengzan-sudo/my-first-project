#!/usr/bin/env python3
"""
Fine-tuned multilingual-BERT distillation of RELEVANCE (MODE 9, strongest variant). Fine-tunes
XLM-RoBERTa end-to-end on the GLM relevance labels (silver), validates vs gold (κ), and (apply)
scores the full reduced corpus. Complements distill_embed.py (frozen bge-m3 + LR) — use whichever
clears the gate; fine-tuning usually wins once there are enough labels (we have 38k+).

  train : fine-tune on GLM labels (gold ids excluded) -> report FINETUNE-vs-gold κ -> save model
  apply : classify the reduced corpus (sharded) -> write [video_id, relevance]

Silver-label guards: class-weighted loss, gold held OUT of training, early value = validate on gold.
Needs transformers + torch (already on the nodes) and the encoder cached under HF_HOME.
"""
import os, sys, csv, json, argparse, hashlib
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import reduce_corpus as rc
csv.field_size_limit(sys.maxsize)
MODEL = os.environ.get("FT_MODEL", "FacebookAI/xlm-roberta-base")

def sh(s): return int(hashlib.md5(str(s).encode("utf-8", "ignore")).hexdigest()[:8], 16)

def cmd_train(a):
    import torch, pandas as pd, collections
    from torch.utils.data import DataLoader, Dataset
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    from sklearn.metrics import cohen_kappa_score, f1_score, classification_report
    dev = "cuda" if torch.cuda.is_available() else "cpu"

    lab = pd.read_csv(a.labels, dtype=str).fillna("")
    samp = pd.read_csv(a.sample, dtype=str).fillna("")
    gold = pd.read_csv(a.gold, dtype=str).fillna("")
    TGT = a.target_col
    d = lab.merge(samp[["video_id", "title", "description"]], on="video_id")
    if a.filter_col:                                                         # e.g. keep only relevance==ON_TOPIC
        d = d[d[a.filter_col] == a.filter_val]; gold = gold[gold[a.filter_col] == a.filter_val]
    # drop empty/none targets (for frames, 'none' isn't a real class to learn)
    drop = {"", "none"}; d = d[~d[TGT].isin(drop)]; gold = gold[~gold[TGT].isin(drop)]
    gids = set(gold["video_id"]); d = d[~d["video_id"].isin(gids)]           # hold gold OUT of training
    classes = sorted(d[TGT].unique()); c2i = {c: i for i, c in enumerate(classes)}
    texts = [(rc.sanitize(t) + " " + rc.sanitize(dd)).strip() for t, dd in zip(d["title"], d["description"])]
    ys = [c2i[r] for r in d[TGT]]
    print(f"target={TGT}  train rows={len(d)}  classes={classes}", flush=True)

    if a.oversample:                                                     # replicate minority classes to full balance
        import random as _rnd
        byc = collections.defaultdict(list)
        for t, y in zip(texts, ys): byc[y].append(t)
        mx = max(len(v) for v in byc.values()); rng = _rnd.Random(23); texts, ys = [], []
        for c, ts in byc.items():
            reps = ts * (mx // len(ts)) + rng.sample(ts, mx % len(ts))
            texts += reps; ys += [c] * len(reps)
        print(f"oversampled rare classes to {mx}/class -> {len(texts)} rows", flush=True)

    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL, num_labels=len(classes)).to(dev)
    cnt = collections.Counter(ys)
    if a.oversample:
        w = torch.ones(len(classes), dtype=torch.float).to(dev)          # data already balanced by oversampling
    else:
        w = torch.tensor([(len(ys) / (len(classes) * max(1, cnt[i]))) ** a.weight_pow for i in range(len(classes))], dtype=torch.float).to(dev)

    class DS(Dataset):
        def __len__(s): return len(texts)
        def __getitem__(s, i): return texts[i], ys[i]
    def collate(b):
        enc = tok([x[0] for x in b], padding=True, truncation=True, max_length=a.maxlen, return_tensors="pt")
        return enc, torch.tensor([x[1] for x in b])
    dl = DataLoader(DS(), batch_size=a.batch, shuffle=True, collate_fn=collate)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr)
    lossf = torch.nn.CrossEntropyLoss(weight=w)
    model.train()
    for ep in range(a.epochs):
        tot = 0.0
        for enc, lb in dl:
            enc = {k: v.to(dev) for k, v in enc.items()}; lb = lb.to(dev)
            loss = lossf(model(**enc).logits, lb)
            opt.zero_grad(); loss.backward(); opt.step(); tot += loss.item()
        print(f"epoch {ep+1}/{a.epochs} loss={tot/len(dl):.4f}", flush=True)

    # validate vs gold (gold's own text, trimmed identically)
    model.eval()
    gt = [(rc.sanitize(t) + " " + rc.trim_desc(dd, 100, 600)).strip() for t, dd in zip(gold["title"], gold["description"])]
    preds = []
    with torch.no_grad():
        for i in range(0, len(gt), a.batch):
            enc = tok(gt[i:i+a.batch], padding=True, truncation=True, max_length=a.maxlen, return_tensors="pt").to(dev)
            preds += model(**enc).logits.argmax(-1).cpu().tolist()
    gp = [classes[i] for i in preds]
    k = cohen_kappa_score(gold[TGT], gp); f = f1_score(gold[TGT], gp, average="macro", zero_division=0)
    print(f"FINETUNE vs gold [{TGT}]: kappa={k:.3f}  F1_macro={f:.3f}  n={len(gold)}")
    print(classification_report(gold[TGT], gp, zero_division=0))
    os.makedirs(a.out_dir, exist_ok=True)
    model.save_pretrained(a.out_dir); tok.save_pretrained(a.out_dir)
    json.dump({"classes": classes, "maxlen": a.maxlen}, open(os.path.join(a.out_dir, "classes.json"), "w"))
    print(f"model -> {a.out_dir}  gate({a.gate}): {'PASS' if k >= a.gate else 'FAIL'}")
    sys.exit(0 if k >= a.gate else 2)

def cmd_apply(a):
    import torch
    from transformers import AutoTokenizer, AutoModelForSequenceClassification
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    meta = json.load(open(os.path.join(a.model_dir, "classes.json"))); classes = meta["classes"]; maxlen = meta.get("maxlen", 256)
    tok = AutoTokenizer.from_pretrained(a.model_dir)
    model = AutoModelForSequenceClassification.from_pretrained(a.model_dir).to(dev).eval()
    suf = f"_t{a.shard}of{a.nshards}" if a.nshards > 1 else ""
    out = a.out.replace(".csv", f"{suf}.csv") if a.nshards > 1 else a.out
    fh = open(out, "w", newline=""); w = csv.writer(fh); w.writerow(["video_id", "relevance"]); n = 0
    bid, btx = [], []
    def flush():
        nonlocal n
        if not btx: return
        with torch.no_grad():
            enc = tok(btx, padding=True, truncation=True, max_length=maxlen, return_tensors="pt").to(dev)
            pr = model(**enc).logits.argmax(-1).cpu().tolist()
        for vid, i in zip(bid, pr): w.writerow([vid, classes[i]]); n += 1
        fh.flush(); bid.clear(); btx.clear()
    with open(a.sample, newline="", encoding="utf-8", errors="ignore") as f:
        for r in csv.DictReader(f):
            vid = r.get("video_id")
            if not vid or (a.nshards > 1 and sh(vid) % a.nshards != a.shard): continue
            bid.append(vid); btx.append((rc.sanitize(r.get("title","")) + " " + rc.sanitize(r.get("description",""))).strip())
            if len(btx) >= a.batch:
                flush()
                if n % 100000 == 0: print(f"  scored={n}", flush=True)
    flush(); fh.close()
    print(f"DONE scored={n} (shard {a.shard}/{a.nshards}) -> {out}")

def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("train")
    t.add_argument("--labels", required=True); t.add_argument("--sample", required=True); t.add_argument("--gold", required=True)
    t.add_argument("--out-dir", default="output/models/relevance_ft"); t.add_argument("--epochs", type=int, default=3)
    t.add_argument("--target-col", default="relevance"); t.add_argument("--filter-col"); t.add_argument("--filter-val")
    t.add_argument("--oversample", action="store_true", help="replicate minority classes to full balance")
    t.add_argument("--weight-pow", type=float, default=1.0, help="exponent on balanced class weights (higher = stronger rare-class emphasis)")
    t.add_argument("--batch", type=int, default=32); t.add_argument("--lr", type=float, default=2e-5)
    t.add_argument("--maxlen", type=int, default=256); t.add_argument("--gate", type=float, default=0.70); t.set_defaults(fn=cmd_train)
    p = sub.add_parser("apply")
    p.add_argument("--model-dir", required=True); p.add_argument("--sample", required=True)
    p.add_argument("--out", default="annotations_reduced/relevance_ft_full.csv")
    p.add_argument("--shard", type=int, default=0); p.add_argument("--nshards", type=int, default=1); p.add_argument("--batch", type=int, default=64)
    p.set_defaults(fn=cmd_apply)
    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
