#!/usr/bin/env python3
"""
Reconcile N independent annotation runs into a gold set (MODE 5). The N runs can be:
  * different MODELS, same or different providers (e.g. gpt-5.6 + gpt-4.1 + claude), OR
  * the SAME model run K times (majority vote — damps non-determinism, e.g. GPT-5's forced
    temperature=1), OR any mix.
Reports pairwise agreement + Cohen κ, builds the gold (unanimous or majority), and writes the
disagreements for adjudication. Hand-rolled κ (no sklearn dependency → always portable).

Usage:
  python3 gold_reconcile.py --inputs run1.csv run2.csv [run3.csv ...] \
     --id-col video_id --on relevance,discourse_frame --mode unanimous|majority \
     --attach devset.csv --text-cols title,description,tags \
     --gold devset_gold.csv --disagree disagreements.csv
Each input CSV needs the id column + the fields named in --on.
"""
import csv, argparse
from collections import Counter, defaultdict

def read(path, idc):
    d={}
    with open(path, encoding="utf-8", errors="ignore") as f:
        for r in csv.DictReader(f): d[r[idc]]=r
    return d

def kappa(a, b):
    n=len(a);
    if n==0: return 0.0, 0.0
    po=sum(1 for i in range(n) if a[i]==b[i])/n
    ca=Counter(a); cb=Counter(b); cats=set(a)|set(b)
    pe=sum((ca[c]/n)*(cb[c]/n) for c in cats)
    return po, ((po-pe)/(1-pe) if pe<1 else 1.0)

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--inputs", nargs="+", required=True)
    ap.add_argument("--id-col", default="video_id")
    ap.add_argument("--on", default="relevance")
    ap.add_argument("--mode", choices=["unanimous","majority"], default="unanimous")
    ap.add_argument("--attach"); ap.add_argument("--text-cols", default="")
    ap.add_argument("--gold", default="devset_gold.csv"); ap.add_argument("--disagree", default="disagreements.csv")
    a=ap.parse_args()
    fields=a.on.split(","); prim=fields[0]
    runs=[read(p, a.id_col) for p in a.inputs]
    ids=set(runs[0]);
    for r in runs[1:]: ids &= set(r)
    ids=sorted(ids)
    print(f"{len(a.inputs)} runs, {len(ids)} common ids")

    # pairwise agreement + κ on the primary field
    print(f"\nPairwise agreement / Cohen κ on '{prim}':")
    for i in range(len(runs)):
        for j in range(i+1, len(runs)):
            a_i=[runs[i][x][prim] for x in ids]; a_j=[runs[j][x][prim] for x in ids]
            po,k=kappa(a_i,a_j)
            print(f"  run{i+1} vs run{j+1}: {100*po:.1f}%  κ={k:.3f}")

    attach={}; tcols=[c for c in a.text_cols.split(",") if c]
    if a.attach:
        attach=read(a.attach, a.id_col)

    gold=[]; dis=[]
    for x in ids:
        labs=[runs[r][x][prim] for r in range(len(runs))]
        cnt=Counter(labs); top,topn=cnt.most_common(1)[0]
        agree = (topn==len(runs)) if a.mode=="unanimous" else (topn>len(runs)/2)
        if agree:
            row={a.id_col:x, prim:top}
            for f in fields[1:]:
                fl=[runs[r][x].get(f,"") for r in range(len(runs))]
                fc=Counter(fl).most_common(1)[0]
                row[f]= fc[0] if fc[1]>len(runs)/2 else ""
            if attach.get(x):
                for c in tcols: row[c]=attach[x].get(c,"")
            gold.append(row)
        else:
            dis.append({a.id_col:x, **{f"run{r+1}_{prim}":runs[r][x][prim] for r in range(len(runs))}})

    goldcols=[a.id_col]+fields+tcols
    with open(a.gold,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=goldcols, extrasaction="ignore"); w.writeheader(); w.writerows(gold)
    with open(a.disagree,"w",newline="",encoding="utf-8") as f:
        if dis:
            w=csv.DictWriter(f, fieldnames=list(dis[0].keys())); w.writeheader(); w.writerows(dis)
    print(f"\nGOLD ({a.mode} on {prim}): {len(gold)}/{len(ids)} -> {a.gold}")
    print(f"disagreements: {len(dis)} -> {a.disagree}")
    print("gold relevance mix:", dict(Counter(r[prim] for r in gold)))

if __name__=="__main__": main()
