#!/usr/bin/env python3
"""
reduce_corpus — Stage 0+1+2 of the corpus-reduction plan for very large corpora.

Fixes the C-parser "Buffer overflow" blocker AND removes negatives in ONE streaming pass, using an
ENSEMBLE of cheap keep/drop classifiers over title + first-100-word description (tags DROPPED):

  backends (choose any subset via --features):
    tfidf : TF-IDF (jieba CJK + latin) -> logistic regression        (CPU, dependency-light)
    embed : frozen multilingual sentence-embeddings -> logistic reg  (GPU for scale; sentence-transformers)

  verbs:
    train : fit each requested backend on the gold, calibrate a per-backend probability threshold to
            a RECALL target via out-of-fold CV, save one model file + a combined report per backend.
    apply : stream the raw corpus with a ROBUST reader (stdlib csv, field_size_limit=maxsize), trim
            text, score with EVERY trained backend, and KEEP a row if the UNION of backends flags it
            (i.e. positive by tfidf OR by embed) — recall-first. Writes clean, C-parser-safe shards.

Aggregate-only stdout (LOCAL_MODE safe). train and apply MUST run from this same file so the pickled
tfidf analyzer resolves. Embedding models load from HF (set HF_HOME to an offline cache on HPC).

Examples
  python3 assets/reduce_corpus.py train --gold devset_gold_v3.csv --recall 0.98 \
      --features tfidf,embed --embed-model BAAI/bge-m3 --out-dir corpus_reduced
  python3 assets/reduce_corpus.py apply --spec corpus_spec_cluster.json \
      --out-dir corpus_reduced --combine union --shard 0 --nshards 1     # loads all models in out-dir
"""
import os, sys, csv, json, glob, re, math, argparse, hashlib

# ------------------------------------------------------------------ shared utils
csv.field_size_limit(sys.maxsize)            # THE robust-read fix: tolerate giant fields
# Column/label DEFAULTS for a scraped-video-metadata corpus shape. Override per corpus with
# --target / --target-col / --id-col / --strata-col rather than editing these.
TARGET = "ON_TOPIC"
TARGET_COL = "relevance"
ID_COL = "video_id"
STRATA_COL = "query_category"
DEFAULT_EMBED = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")

try:
    import jieba; jieba.setLogLevel(60); HAS_J = True
except Exception:
    HAS_J = False

def analyzer(t):
    """tfidf tokenizer: latin word tokens + Chinese features. Uses jieba words when available,
    else falls back to CJK CHARACTER BIGRAMS so Chinese is never dropped when jieba is absent
    (char-bigram TF-IDF is a strong standard baseline for Chinese text classification)."""
    t = str(t).lower(); out = re.findall(r"[a-z][a-z'\-]{2,}", t)
    if re.search(r"[一-鿿]", t):
        if HAS_J:
            out += [w for w in jieba.cut(t) if len(w) >= 2 and re.search(r"[一-鿿]", w)]
        else:
            ch = re.findall(r"[一-鿿]", t)                       # CJK chars, in order
            out += [ch[i] + ch[i + 1] for i in range(len(ch) - 1)]  # bigrams
    return out

def sh(s):
    return int(hashlib.md5(str(s).encode("utf-8", "ignore")).hexdigest()[:8], 16)

def sanitize(s):
    """Collapse all whitespace (newlines/tabs included) -> one clean CSV record."""
    return " ".join(str(s or "").split())

def trim_desc(s, nwords, nchars):
    """First `nwords` whitespace tokens AND a hard `nchars` cap (CJK safety). Also sanitizes."""
    s = sanitize(s); toks = s.split(" ")
    if len(toks) > nwords:
        s = " ".join(toks[:nwords])
    return s[:nchars]

def build_text(title, desc, nwords, nchars):
    return (sanitize(title) + " " + trim_desc(desc, nwords, nchars)).strip()

# ------------------------------------------------------------------ embedding backend (lazy)
# Uses raw transformers + torch (already on the cluster) — no sentence-transformers dependency.
# Pooling: CLS (default, correct for BGE models) or mean (e5/gte); set EMBED_POOL / EMBED_PREFIX.
_ENC = {}
def get_encoder(name):
    if name not in _ENC:
        import torch
        from transformers import AutoTokenizer, AutoModel
        dev = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"  loading embed model '{name}' on {dev}", flush=True)
        tok = AutoTokenizer.from_pretrained(name)
        mdl = AutoModel.from_pretrained(name).to(dev).eval()
        _ENC[name] = (tok, mdl, dev)
    return _ENC[name]

def embed(texts, name, batch=64):
    import torch, numpy as np
    tok, mdl, dev = get_encoder(name)
    pool = os.environ.get("EMBED_POOL", "cls"); prefix = os.environ.get("EMBED_PREFIX", "")
    texts = [prefix + t for t in texts] if prefix else list(texts)
    out = []
    with torch.no_grad():
        for i in range(0, len(texts), batch):
            enc = tok(texts[i:i + batch], padding=True, truncation=True, max_length=512,
                      return_tensors="pt").to(dev)
            hs = mdl(**enc).last_hidden_state                       # (b, L, d)
            if pool == "mean":
                m = enc["attention_mask"].unsqueeze(-1).float()
                v = (hs * m).sum(1) / m.sum(1).clamp(min=1e-9)
            else:                                                   # cls
                v = hs[:, 0]
            v = torch.nn.functional.normalize(v, p=2, dim=1)
            out.append(v.cpu().numpy())
    return np.vstack(out).astype("float32")

# ------------------------------------------------------------------ calibration helpers
def make_lr():
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(max_iter=2000, C=4.0, class_weight="balanced")

def make_tfidf_pipe():
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import Pipeline
    return Pipeline([("tf", TfidfVectorizer(analyzer=analyzer, min_df=2, max_df=0.5, max_features=20000)),
                     ("lr", make_lr())])

def calibrate(oof, y, recall):
    """Lowest threshold that retains `recall` of positives (via their sorted OOF probs)."""
    pos = sorted(float(p) for p, yy in zip(oof, y) if yy == 1)
    k = min(int(math.floor((1.0 - recall) * len(pos))), len(pos) - 1)
    return pos[k]

def metrics_at(oof, y, thr):
    import numpy as np
    y = np.asarray(y); keep = np.asarray(oof) >= thr
    tp = int(((y == 1) & keep).sum()); fp = int(((y == 0) & keep).sum()); npos = int((y == 1).sum())
    return {"threshold": round(float(thr), 5), "cv_recall": round(tp / max(1, npos), 4),
            "cv_precision": round(tp / max(1, tp + fp), 4),
            "kept_fraction_gold": round(float(keep.mean()), 4)}

# ------------------------------------------------------------------ TRAIN
def cmd_train(a):
    import numpy as np, pandas as pd, joblib
    from sklearn.model_selection import cross_val_predict, StratifiedKFold
    os.makedirs(a.out_dir, exist_ok=True)
    feats = [f.strip() for f in a.features.split(",") if f.strip()]
    # drop stale model files for backends NOT trained this run, so `apply` (which globs all
    # prefilter_model_*.joblib) never silently reuses a previous run's backend.
    for kf in glob.glob(os.path.join(a.out_dir, "prefilter_model_*.joblib")):
        kind = os.path.basename(kf)[len("prefilter_model_"):-len(".joblib")]
        if kind not in feats:
            os.remove(kf); print(f"  removed stale {os.path.basename(kf)}")

    g = pd.read_csv(a.gold, dtype=str).fillna("")
    target_col = getattr(a, "target_col", TARGET_COL); target = getattr(a, "target", TARGET)
    if target_col not in g.columns: sys.exit(f"gold missing '{target_col}'")
    if target not in set(g[target_col]): sys.exit(f"--target {target!r} does not occur in gold column {target_col!r}")
    texts = [build_text(t, d, a.desc_words, a.desc_chars)
             for t, d in zip(g.get("title", ""), g.get("description", ""))]
    y = (g[target_col] == target).astype(int).values
    n_pos = int(y.sum()); n = len(g)
    if n_pos < 10: sys.exit(f"only {n_pos} positive gold rows — too few to train")
    cv = StratifiedKFold(n_splits=min(5, n_pos), shuffle=True, random_state=0)

    report = {"gold": a.gold, "n": n, "n_pos": n_pos, "n_neg": n - n_pos,
              "recall_target": a.recall, "features": feats, "backends": {},
              "note": ("kept_fraction is on the positive-ENRICHED gold (~%.0f%% positive); the "
                       "wild corpus is ~3%% positive, so real kept fractions will be LOWER (safe)."
                       % (100.0 * n_pos / n)),
              "combine_at_apply": "union (keep if any backend flags positive)"}

    if "tfidf" in feats:
        pipe = make_tfidf_pipe()
        oof = cross_val_predict(pipe, texts, y, cv=cv, method="predict_proba")[:, 1]
        thr = calibrate(oof, y, a.recall); m = metrics_at(oof, y, thr)
        pipe.fit(texts, y)
        joblib.dump({"kind": "tfidf", "pipe": pipe, "threshold": float(thr),
                     "desc_words": a.desc_words, "desc_chars": a.desc_chars},
                    os.path.join(a.out_dir, "prefilter_model_tfidf.joblib"))
        report["backends"]["tfidf"] = m
        print(f"[tfidf] thr={thr:.5f} recall={m['cv_recall']} prec={m['cv_precision']} "
              f"kept(gold)={m['kept_fraction_gold']}")

    if "embed" in feats:
        X = embed(texts, a.embed_model)
        lr = make_lr()
        oof = cross_val_predict(lr, X, y, cv=cv, method="predict_proba")[:, 1]
        thr = calibrate(oof, y, a.recall); m = metrics_at(oof, y, thr); m["embed_model"] = a.embed_model
        lr.fit(X, y)
        joblib.dump({"kind": "embed", "lr": lr, "embed_model": a.embed_model, "threshold": float(thr),
                     "desc_words": a.desc_words, "desc_chars": a.desc_chars},
                    os.path.join(a.out_dir, "prefilter_model_embed.joblib"))
        report["backends"]["embed"] = m
        print(f"[embed:{a.embed_model}] thr={thr:.5f} recall={m['cv_recall']} prec={m['cv_precision']} "
              f"kept(gold)={m['kept_fraction_gold']}")

    rep_path = os.path.join(a.out_dir, "prefilter_report.json")
    json.dump(report, open(rep_path, "w"), indent=1, ensure_ascii=False)
    print(f"jieba={HAS_J}; report -> {rep_path}")
    for b, m in report["backends"].items():
        if m["cv_recall"] < a.recall - 0.02:
            print(f"WARNING [{b}]: CV recall {m['cv_recall']} < target {a.recall} — raise --recall "
                  f"or use GLM-silver training before trusting the filter to discard data.")

# ------------------------------------------------------------------ scoring
def score(model, texts):
    if model["kind"] == "tfidf":
        return model["pipe"].predict_proba(texts)[:, 1]
    return model["lr"].predict_proba(embed(texts, model["embed_model"]))[:, 1]

# ------------------------------------------------------------------ robust corpus reader
def iter_spec_rows(spec, shard, nshards):
    """Yield sanitized dict rows from the corpus spec via stdlib csv (field_size_limit raised).
    by_category: stratum = filename stem; big_file: filtered to big_only strata. Sharded by
    md5(video_id) and deduped within-shard."""
    seen = set()
    def emit(vid, strat, title, desc):
        if not vid or vid == ID_COL: return None
        if nshards > 1 and sh(vid) % nshards != shard: return None
        if vid in seen: return None
        seen.add(vid)
        return {ID_COL: vid, STRATA_COL: strat, "title": title, "description": desc}
    for f in sorted(glob.glob(spec.get("category_glob", ""))) if spec.get("category_glob") else []:
        strat = os.path.basename(f)[:-4]
        with open(f, newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                rec = emit(row.get(ID_COL), strat, row.get("title", ""), row.get("description", ""))
                if rec: yield rec
    big = spec.get("big_file")
    if big and os.path.exists(big):
        keep_strata = set(spec.get("big_only", [])); scol = spec.get("strata_col", STRATA_COL)
        with open(big, newline="", encoding="utf-8", errors="ignore") as fh:
            for row in csv.DictReader(fh):
                strat = row.get(scol, "")
                if keep_strata and strat not in keep_strata: continue
                rec = emit(row.get(ID_COL), strat, row.get("title", ""), row.get("description", ""))
                if rec: yield rec

# ------------------------------------------------------------------ APPLY
def cmd_apply(a):
    import numpy as np, joblib
    files = a.models.split(",") if a.models else sorted(glob.glob(os.path.join(a.out_dir, "prefilter_model_*.joblib")))
    if not files: sys.exit(f"no models found in {a.out_dir} (run `train` first)")
    models = [joblib.load(f) for f in files]
    kinds = [m["kind"] for m in models]
    nwords = a.desc_words or models[0].get("desc_words", 100)
    nchars = a.desc_chars or models[0].get("desc_chars", 600)
    spec = json.load(open(a.spec)); os.makedirs(a.out_dir, exist_ok=True)

    suf = f"_t{a.shard}of{a.nshards}" if a.nshards > 1 else ""
    out_path = os.path.join(a.out_dir, f"reduced{suf}.csv")
    HEAD = [ID_COL, STRATA_COL, "title", "description"]
    fh = open(out_path, "w", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=HEAD); w.writeheader()
    print(f"APPLY shard={a.shard}/{a.nshards} backends={kinds} combine={a.combine} "
          f"desc_words={nwords} desc_chars={nchars} -> {out_path}", flush=True)

    buf_rows, buf_text = [], []
    scanned = kept = 0
    kept_by = {k: 0 for k in kinds}
    by_strat_scan, by_strat_keep = {}, {}
    BATCH = a.batch

    def flush():
        nonlocal kept
        if not buf_text: return
        masks = []
        for m in models:
            p = score(m, buf_text)
            mk = p >= m["threshold"]; masks.append(mk)
            kept_by[m["kind"]] += int(mk.sum())
        keep = masks[0].copy()
        for mk in masks[1:]:
            keep = (keep | mk) if a.combine == "union" else (keep & mk)
        for rec, k in zip(buf_rows, keep):
            if k:
                w.writerow(rec); kept += 1
                by_strat_keep[rec[STRATA_COL]] = by_strat_keep.get(rec[STRATA_COL], 0) + 1
        fh.flush(); buf_rows.clear(); buf_text.clear()

    for rec in iter_spec_rows(spec, a.shard, a.nshards):
        title = sanitize(rec["title"]); desc = trim_desc(rec["description"], nwords, nchars)
        buf_rows.append({ID_COL: rec[ID_COL], STRATA_COL: rec[STRATA_COL], "title": title, "description": desc})
        buf_text.append((title + " " + desc).strip())
        by_strat_scan[rec[STRATA_COL]] = by_strat_scan.get(rec[STRATA_COL], 0) + 1
        scanned += 1
        if len(buf_text) >= BATCH:
            flush()
            if scanned % (BATCH * 10) == 0:
                print(f"  scanned={scanned} kept={kept} ({100.0*kept/max(1,scanned):.1f}%)", flush=True)
        if a.limit and scanned >= a.limit: break
    flush(); fh.close()

    rep = {"shard": a.shard, "nshards": a.nshards, "backends": kinds, "combine": a.combine,
           "scanned": scanned, "kept_union" if a.combine == "union" else "kept": kept,
           "kept_fraction": round(kept / max(1, scanned), 4),
           "kept_by_backend": kept_by,
           "by_stratum_scanned": by_strat_scan, "by_stratum_kept": by_strat_keep, "out": out_path}
    rep_path = os.path.join(a.out_dir, f"apply_report{suf}.json")
    json.dump(rep, open(rep_path, "w"), indent=1, ensure_ascii=False)
    print(f"DONE scanned={scanned} kept={kept} ({100.0*kept/max(1,scanned):.1f}%) "
          f"per-backend={kept_by} -> {out_path}")
    print(f"report -> {rep_path}")

# ------------------------------------------------------------------ CLI
def main():
    ap = argparse.ArgumentParser(prog="reduce_corpus")
    sub = ap.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("train")
    t.add_argument("--gold", required=True)
    t.add_argument("--target", default=TARGET, help=f"positive class label (default: {TARGET})")
    t.add_argument("--target-col", default=TARGET_COL, help=f"gold column holding the class (default: {TARGET_COL})")
    t.add_argument("--recall", type=float, default=0.98)
    t.add_argument("--features", default="tfidf,embed", help="comma list: tfidf,embed")
    t.add_argument("--embed-model", default=DEFAULT_EMBED)
    t.add_argument("--desc-words", type=int, default=100)
    t.add_argument("--desc-chars", type=int, default=600)
    t.add_argument("--out-dir", default="corpus_reduced")
    t.set_defaults(fn=cmd_train)

    p = sub.add_parser("apply")
    p.add_argument("--spec", required=True)
    p.add_argument("--models", help="comma list of .joblib; default = all prefilter_model_*.joblib in out-dir")
    p.add_argument("--out-dir", default="corpus_reduced")
    p.add_argument("--combine", choices=["union", "intersection"], default="union")
    p.add_argument("--shard", type=int, default=0)
    p.add_argument("--nshards", type=int, default=1)
    p.add_argument("--desc-words", type=int, default=0, help="0 = value stored in model")
    p.add_argument("--desc-chars", type=int, default=0, help="0 = value stored in model")
    p.add_argument("--batch", type=int, default=4096)
    p.add_argument("--limit", type=int, help="smoke: stop after N scanned rows")
    p.set_defaults(fn=cmd_apply)

    a = ap.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
