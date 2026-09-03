#!/usr/bin/env python3
"""
Active-learning candidate mining for a RARE target class (MODE 4 top-up). When the target is
~few % of the corpus, random sampling wastes annotation budget. Instead: train a quick binary
classifier on the existing gold (target vs rest), scan the corpus, and return the top-K
highest-probability candidates — which the LLM then labels at a much higher hit rate. Fold the
confirmed positives back into the gold to enrich the rare class + its sub-codes.

Usage:
  python3 active_sample.py --gold devset_gold.csv --target ON_TOPIC --target-col relevance \
     --corpus corpus_spec.json --text-cols title,description,tags --id-col video_id \
     --exclude devset.csv --scan 300000 --top 800 --out candidates.csv
"""
import csv, glob, os, argparse, heapq, re, json
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
try: import jieba; jieba.setLogLevel(60); HAS_J=True
except Exception: HAS_J=False

def analyzer(t):
    t=str(t).lower(); out=re.findall(r"[a-z][a-z'\-]{2,}",t)
    if HAS_J and re.search(r"[一-鿿]",t): out+=[w for w in jieba.cut(t) if len(w)>=2 and re.search(r"[一-鿿]",w)]
    return out

def rows_of(path, cols):
    for eng in ("c","python"):
        try:
            import pandas as pd
            for ch in pd.read_csv(path,usecols=lambda c:c in cols,chunksize=100_000,dtype=str,on_bad_lines="skip",engine=eng):
                yield ch
            return
        except Exception: pass

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--gold",required=True); ap.add_argument("--target",required=True); ap.add_argument("--target-col",default="relevance")
    ap.add_argument("--corpus",required=True); ap.add_argument("--text-cols",required=True); ap.add_argument("--id-col",default="video_id")
    ap.add_argument("--exclude"); ap.add_argument("--scan",type=int,default=300000); ap.add_argument("--top",type=int,default=800)
    ap.add_argument("--out",required=True)
    a=ap.parse_args()
    import pandas as pd
    tcols=a.text_cols.split(","); idc=a.id_col
    g=pd.read_csv(a.gold,dtype=str).fillna("")
    g["_t"]=g[tcols].agg(" ".join,axis=1)
    y=(g[a.target_col]==a.target).astype(int)
    pos=int(y.sum())
    pipe=Pipeline([("tf",TfidfVectorizer(analyzer=analyzer,min_df=2,max_df=0.5,max_features=20000)),
                   ("lr",LogisticRegression(max_iter=2000,C=4.0,class_weight="balanced"))])
    pipe.fit(g["_t"], y)
    print(f"trained binary '{a.target}' classifier on gold: {pos} pos / {len(g)-pos} neg")

    excl=set()
    if a.exclude and os.path.exists(a.exclude):
        excl=set(pd.read_csv(a.exclude,dtype=str)[idc])
    excl|=set(g[idc])
    spec=json.load(open(a.corpus)); files=sorted(glob.glob(spec.get("category_glob","")))
    heap=[]; scanned=0; seen=set()
    read_cols=list(dict.fromkeys([idc]+tcols))
    per_file=max(1, a.scan // max(1,len(files)))   # spread budget across ALL categories
    for f in files:
        fscan=0
        for ch in rows_of(f, read_cols):
            ch=ch[~ch[idc].isin(excl)].copy()
            ch=ch[~ch[idc].isin(seen)]
            if len(ch)==0: continue
            ch["_t"]=ch[[c for c in tcols if c in ch]].fillna("").agg(" ".join,axis=1)
            p=pipe.predict_proba(ch["_t"])[:,1]
            for vid,prob,*rest in zip(ch[idc],p, *[ch[c] for c in tcols]):
                seen.add(vid)
                rec=(float(prob), vid, {c:ch_val for c,ch_val in zip(tcols,rest)})
                if len(heap)<a.top: heapq.heappush(heap,rec)
                elif prob>heap[0][0]: heapq.heapreplace(heap,rec)
            scanned+=len(ch); fscan+=len(ch)
            if fscan>=per_file: break
    cand=sorted(heap,key=lambda x:-x[0])
    with open(a.out,"w",newline="",encoding="utf-8") as fh:
        w=csv.writer(fh); w.writerow([idc]+tcols+["meta_prob"])
        for prob,vid,td in cand: w.writerow([vid]+[td.get(c,"") for c in tcols]+[round(prob,4)])
    print(f"scanned {scanned} rows; wrote top {len(cand)} candidates -> {a.out}")
    print(f"candidate prob range: {cand[-1][0]:.3f}..{cand[0][0]:.3f}")

if __name__=="__main__": main()
