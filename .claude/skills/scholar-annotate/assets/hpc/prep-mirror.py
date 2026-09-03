#!/usr/bin/env python3
"""
Build a SLIM metadata mirror for transfer to a cluster: keep only the columns the annotator
needs (id + text cols + stratum), truncate long text, pre-filter the big file to its unique
strata. Mirrors the corpus-spec layout so the engine runs unchanged with PROJ pointed at it.
Usage: python3 prep-mirror.py --corpus corpus_spec.json --id-col video_id \
          --text-cols title,description,tags --out-root ./xfer
"""
import pandas as pd, glob, os, json, argparse
def robust(path,cols):
    for eng in ("c","python"):
        try:
            for ch in pd.read_csv(path,usecols=lambda c:c in cols,chunksize=200_000,dtype=str,on_bad_lines="skip",engine=eng): yield ch
            return
        except Exception as e: print(f"  {os.path.basename(path)} engine={eng}: {str(e)[:50]}",flush=True)
def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--corpus",required=True); ap.add_argument("--id-col",default="video_id")
    ap.add_argument("--text-cols",required=True); ap.add_argument("--out-root",default="./xfer")
    ap.add_argument("--trunc",type=int,default=600); a=ap.parse_args()
    spec=json.load(open(a.corpus)); tcols=a.text_cols.split(","); idc=a.id_col
    strata=spec.get("strata_col","query_category")
    xcat=os.path.join(a.out_root,"by_category"); os.makedirs(xcat,exist_ok=True); tot=0
    def slim(ch):
        for c in tcols:
            if c in ch: ch[c]=ch[c].fillna("").str.slice(0,a.trunc)
        return ch
    for f in sorted(glob.glob(spec.get("category_glob",""))) if spec.get("category_glob") else []:
        out=os.path.join(xcat,os.path.basename(f)); first=True; n=0
        for ch in robust(f,[idc]+tcols):
            slim(ch).to_csv(out,mode="w" if first else "a",header=first,index=False); first=False; n+=len(ch)
        tot+=n; print(f"  {os.path.basename(f)}: {n} -> {os.path.getsize(out)//1_000_000}MB",flush=True)
    if spec.get("big_file"):
        out=os.path.join(a.out_root,os.path.basename(spec["big_file"])); first=True; n=0
        for ch in robust(spec["big_file"],[idc,strata]+tcols):
            if spec.get("big_only"): ch=ch[ch[strata].isin(spec["big_only"])].copy()
            if len(ch)==0: continue
            slim(ch).to_csv(out,mode="w" if first else "a",header=first,index=False); first=False; n+=len(ch)
        print(f"  big-only: {n} -> {os.path.getsize(out)//1_000_000}MB",flush=True); tot+=n
    sz=sum(os.path.getsize(os.path.join(dp,fn)) for dp,_,fs in os.walk(a.out_root) for fn in fs)
    print(f"\nMIRROR: {tot} rows, {sz//1_000_000}MB at {a.out_root}. rsync it + the skill assets; point PROJ at it.")
if __name__=="__main__": main()
