#!/usr/bin/env python3
"""
scholar-annotate — execution engine.  Verbs: sample | annotate | validate | distill.
Self-contained; only stdlib + pandas + scikit-learn (+ providers.py, codebook_schema.py here).
LLM calls go through providers.py (OpenAI/Anthropic live+Batch, or a local OpenAI-compatible
server). Aggregate-only stdout (LOCAL_MODE safe). See references/scale-engine.md.

Corpus spec (JSON) used by `sample` and `distill`:
  {"category_glob":"…/by_category/*.csv",        # 0+ files, filename stem = stratum
   "big_file":"…/all.csv", "big_only":["catA",…],# optional; big_file rows kept only for these strata
   "strata_col":"query_category"}                 # column holding the stratum in big_file
Run manifest (JSON) used by `annotate`:
  {"input":"sample.csv","id_col":"video_id","text_cols":["title","description","tags"],
   "codebook":"codebook.md","out_dir":"annotations",
   "provider":{"strategy":"local|async|batch","provider":"local|openai|anthropic",
               "model":"glm-5.2","api_base":"http://127.0.0.1:8080/v1","key_env":"GLM_API_KEY"},
   "fewshot":{"gold":"devset_gold.csv","k":2}, "workers":16, "shards":64}
"""
import os, sys, csv, json, glob, argparse, hashlib, random, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
HERE=os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
import providers, codebook_schema

def sh(s): return int(hashlib.md5(str(s).encode("utf-8","ignore")).hexdigest()[:8],16)
def S(x): return x if isinstance(x,str) else ""
def sha256_file(path):
    """Content digest of a file, or None when absent. Used for instrument provenance."""
    try:
        h=hashlib.sha256()
        with open(path,"rb") as fh:
            for blk in iter(lambda: fh.read(1<<20), b""): h.update(blk)
        return h.hexdigest()
    except Exception: return None

# UNREADABLE is the three-valued companion for corpus reads: a file that could not be
# parsed must remain distinguishable from a file that legitimately held no rows. Before
# 2026-08-11 robust_chunks printed a line and yielded nothing, so 60 unreadable files out
# of 100 produced a silent 40-file sample. Callers MUST consult this list and fail closed.
UNREADABLE=[]
def robust_chunks(path, cols):
    import pandas as pd
    for eng in ("c","python"):
        try:
            for ch in pd.read_csv(path,usecols=lambda c:c in cols,chunksize=200_000,dtype=str,
                                  on_bad_lines="skip",engine=eng): yield ch
            return
        except Exception as e: print(f"  [{os.path.basename(path)}] engine={eng} failed ({str(e)[:60]})",flush=True)
    UNREADABLE.append(path)
    print(f"  [{os.path.basename(path)}] UNREADABLE",flush=True)

def assert_readable(allow, where):
    """Fail closed on unreadable corpus files. Safe path is the default (U10): an
    unreadable file silently shrinks the corpus, so it must be opted into explicitly."""
    n=len(UNREADABLE)
    if n<=allow: return n
    print(f"\nSTATUS=RED\nREASON=unreadable_corpus_files")
    print(f"FATAL ({where}): {n} corpus file(s) could not be parsed; --allow-unreadable={allow}.")
    for p in UNREADABLE[:20]: print(f"  UNREADABLE {p}")
    if n>20: print(f"  … and {n-20} more")
    print("Re-run with --allow-unreadable N once you have confirmed the loss is acceptable;")
    print("the count is stamped on the output sidecar either way.")
    sys.exit(2)

def iter_corpus(spec, cols, shard=0, nshards=1):
    """Yield dict rows (deduped by id within-run) from a corpus spec, optionally sharded."""
    import pandas as pd
    seen=set(); idc=cols[0]
    def keep(v): return v and v!=idc and (nshards<=1 or sh(v)%nshards==shard) and v not in seen
    for f in sorted(glob.glob(spec.get("category_glob",""))) if spec.get("category_glob") else []:
        cat=os.path.basename(f)[:-4]
        for ch in robust_chunks(f, cols):
            for r in ch.itertuples(index=False):
                d=r._asdict() if hasattr(r,"_asdict") else dict(zip(cols,r))
                v=d.get(idc)
                if not keep(v): continue
                seen.add(v); d[spec.get("strata_col","stratum")]=cat; yield d
    if spec.get("big_file"):
        bc=list(dict.fromkeys(cols+[spec.get("strata_col","query_category")]))
        for ch in robust_chunks(spec["big_file"], bc):
            if spec.get("big_only"): ch=ch[ch[spec["strata_col"]].isin(spec["big_only"])]
            for r in ch.itertuples(index=False):
                d=dict(zip(ch.columns, r)); v=d.get(idc)
                if not keep(v): continue
                seen.add(v); yield d

# ----------------------------- SAMPLE -----------------------------
def cmd_sample(a):
    import pandas as pd, collections
    random.seed(a.seed)
    spec=json.load(open(a.corpus)); cols=a.text_cols.split(","); idc=a.id_col
    strata=a.strata or spec.get("strata_col","stratum")
    lex=json.load(open(a.oversample)) if a.oversample else None   # {"target_class":[markers...], ...}
    def prior(txt):
        if not lex: return "?"
        t=txt.lower(); best=("?",0)
        for cls,ms in lex.items():
            h=sum(1 for m in ms if m in t)
            if h>best[1]: best=(cls,h)
        return best[0] if best[1]>0 else "?"
    pool=collections.defaultdict(list); cnt=collections.defaultdict(lambda:[0]); cap=a.pool
    read_cols=list(dict.fromkeys([idc]+cols))
    for d in iter_corpus(spec, read_cols):
        st=d.get(strata,"NA"); cnt[st][0]+=1
        item={k:S(d.get(k)) for k in read_cols}; item[strata]=st
        item["_prior"]=prior(" ".join(item[k] for k in cols))
        L=pool[st]
        if len(L)<cap: L.append(item)
        else:
            j=random.randint(0,cnt[st][0]-1)
            if j<cap: L[j]=item
    rows=[x for v in pool.values() for x in v]
    df=pd.DataFrame(rows).drop_duplicates(idc)
    keys=sorted(df[strata].unique()); per=max(1,a.n//max(1,len(keys)))
    sel=[]
    if lex and a.target:  # over-sample target class first
        tgt=df[df._prior==a.target]; floor=min(len(tgt), a.n//2)
        sel.append(tgt.sample(min(floor,len(tgt)),random_state=1)); df=df[~df[idc].isin(sel[0][idc])]
    for k in keys:
        sub=df[df[strata]==k]; sel.append(sub.sample(min(per,len(sub)),random_state=2))
    n_unreadable=assert_readable(a.allow_unreadable,"sample")   # fail closed unless opted into
    out=pd.concat(sel).drop_duplicates(idc).sample(frac=1,random_state=3).head(a.n)
    if "description" in out.columns: out["description"]=out["description"].fillna("").str.slice(0,600)
    out.to_csv(a.out,index=False)
    # U14 — a measurement's design parameters are properties of the declared gate, not CLI
    # defaults decided by omission. The hakka n=400 -> n=100 regression (a rebuild re-ran a
    # precision gate at the wrapper default and flipped a KEEP to a DROP) happened because the
    # sample size existed only as an argument someone once typed. Stamp it on the artifact.
    design={"n_requested":a.n,"n_written":int(len(out)),"pool_per_stratum":a.pool,"seed":a.seed,
            "strata_col":strata,"id_col":idc,"text_cols":cols,
            "oversample_lexicon":a.oversample,"oversample_lexicon_sha256":sha256_file(a.oversample) if a.oversample else None,
            "target_class":a.target,"corpus_spec":os.path.abspath(a.corpus),
            "corpus_spec_sha256":sha256_file(a.corpus),
            "unreadable_files":n_unreadable,"unreadable_paths":list(UNREADABLE[:50]),
            "engine":os.path.basename(__file__)}
    dpath=a.out+".design.json"
    with open(dpath,"w") as fh: json.dump(design,fh,indent=1,ensure_ascii=False,allow_nan=False)
    print(f"SAMPLE n={len(out)} -> {a.out}")
    print(f"design sidecar -> {dpath}  (n={a.n} pool={a.pool} seed={a.seed} unreadable={n_unreadable})")
    print("prior distribution:"); print(out["_prior"].value_counts().to_string() if "_prior" in out else "(no lexicon)")
    print("strata:"); print(out[strata].value_counts().to_string())

# ----------------------------- ANNOTATE -----------------------------
def cmd_annotate(a):
    import pandas as pd
    m=json.load(open(a.manifest)); pv=m["provider"]; cb=codebook_schema.load(m["codebook"])
    idc=m.get("id_col","id"); tcols=m["text_cols"]; strata=m.get("strata_col","query_category")
    out_dir=m["out_dir"]; os.makedirs(out_dir,exist_ok=True)
    suf=f"_t{a.shard}of{a.nshards}" if a.nshards>1 else ""
    ckpt=os.path.join(out_dir,f"processed{suf}.txt"); failed=os.path.join(out_dir,f"failed{suf}.txt")
    ledger=os.path.join(out_dir,"cost_ledger.json")
    fewshot=providers.build_fewshot(m.get("fewshot"), cb, tcols)
    df=pd.read_csv(m["input"],dtype=str).fillna("")
    if a.limit: df=df.head(a.limit)
    if a.nshards>1: df=df[df[idc].map(lambda v: sh(v)%a.nshards==a.shard)]
    # pre-flight cost estimate
    est=providers.estimate_cost(pv, len(df), fewshot_len=len(fewshot))
    print(f"PRE-FLIGHT: {len(df)} docs, strategy={pv['strategy']} model={pv['model']} ~= {est}")
    if a.dry_run: print("dry-run: no calls made."); return
    done=set(open(ckpt).read().split()) if os.path.exists(ckpt) else set()
    todo=df[~df[idc].isin(done)]
    HEAD=[idc,strata]+cb["out_fields"]+["rationale"]

    if pv["strategy"]=="batch":
        providers.run_batch(todo, idc, strata, tcols, cb, fewshot, pv, out_dir, HEAD, ckpt)
        return
    # async / local : threaded live calls
    locks={}; files={}; guard=threading.Lock(); cklock=threading.Lock()
    ck=open(ckpt,"a"); fl=open(failed,"a")
    def writer(vid):
        with guard:
            p=os.path.join(out_dir,f"annot{suf}_{sh(vid)%m.get('shards',64):03d}.csv")
            if p not in files:
                new=not os.path.exists(p) or os.path.getsize(p)==0
                fh=open(p,"a",newline="",encoding="utf-8"); w=csv.DictWriter(fh,fieldnames=HEAD)
                if new: w.writeheader(); fh.flush()
                files[p]=(fh,w); locks[p]=threading.Lock()
            return locks[p],files[p]
    n=[0]; tok=[0,0]
    recs=todo.to_dict("records")
    def work(r):
        return providers.annotate_one(r, tcols, cb, fewshot, pv)
    with ThreadPoolExecutor(max_workers=m.get("workers",16)) as ex:
        futs={ex.submit(work,r):r for r in recs}
        for fut in as_completed(futs):
            r=futs[fut]; vid=r[idc]
            try: status,ann,usage=fut.result()
            except providers.QuotaStop as q: print(f"STOP {q}"); break
            if status=="ok":
                lock,(fh,w)=writer(vid); row={idc:vid,strata:r.get(strata,"")}; row.update(ann)
                with lock: w.writerow(row); fh.flush()
                with cklock: ck.write(vid+"\n"); ck.flush()
                n[0]+=1; tok[0]+=usage[0]; tok[1]+=usage[1]
                if n[0]%1000==0: print(f"  annotated={n[0]}",flush=True)
            else:
                with cklock: fl.write(vid+"\n"); fl.flush()
    ck.close(); fl.close()
    for fh,_ in files.values():
        try: fh.close()
        except Exception: pass
    json.dump({"model":pv["model"],"n":n[0],"prompt_tokens":tok[0],"completion_tokens":tok[1]},open(ledger,"w"),indent=1)
    print(f"DONE annotated={n[0]} (shard {a.shard}/{a.nshards}); ledger -> {ledger}")

# ----------------------------- VALIDATE (HARD GATE) -----------------------------
def cmd_validate(a):
    """Reliability gate. GOLD is the denominator, not the intersection.

    2026-08-11 rebuild. The prior implementation had three holes, all demonstrated on a
    fixture where an annotator failed 40% of documents (see tests/smoke/test-annotate-
    engine-gate.sh):
      E1  pred was INNER-joined onto gold, so every document the annotator failed on
          vanished from the denominator. Failing the hard cases and answering the easy
          ones RAISED the reported kappa. The `g.notna()` conjunct was also dead code
          after `.fillna("")` — notna() is always True on a filled frame.
      E1b a NaN kappa (degenerate single-label column, which is what a 40% failure rate
          produces) passed the gate, because `nan < gate` is False in IEEE-754. "Could
          not be computed" read as "not below the threshold" — the three-valued collapse
          this very gate exists to prevent.
      E2  only the FIRST --on field could set ok_gate, so a secondary field at kappa=0.00
          shipped with exit 0 while printing FAIL.
    Every excluded row now carries a companion naming the branch that excluded it
    (annotator_absent / annotator_blank / gold_empty) — written by the deciding branch,
    with no default, per the deciding-rule contract in _shared/defect-class-registry.md.
    """
    import pandas as pd, math, datetime
    from sklearn.metrics import cohen_kappa_score, f1_score, classification_report
    # Reject fail-open thresholds rather than silently honouring them. A negative --gate
    # admits virtually any defined kappa; a zero/negative --min-coverage disables the
    # coverage protection outright; NaN bypasses every comparison.
    for nm,val,lo,hi in (("--gate",a.gate,0.0,1.0),("--min-coverage",a.min_coverage,0.0,1.0)):
        if val is None or math.isnan(val) or val<lo or val>hi:
            print("STATUS=RED"); print("REASON=invalid_threshold")
            print(f"FATAL: {nm}={val!r} is outside [{lo}, {hi}] or not a number.")
            sys.exit(2)
    # `--min-coverage 0` disables the protection that the whole coverage fix exists to
    # provide. A stderr warning is not enough — warnings are lost in logs, and this is
    # precisely the DC-05 shape ("a default that silently determines evidentiary strength").
    # Safe path is the default; disabling it requires an explicit stated choice, and the
    # choice is stamped on the artifact so a downstream reader can see it.
    if a.min_coverage==0.0 and not a.allow_zero_coverage:
        print("STATUS=RED"); print("REASON=coverage_check_disabled")
        print("FATAL: --min-coverage 0 disables the coverage check entirely — the kappa would "
              "then describe whatever subset the annotator chose to answer, which is the defect "
              "this gate exists to prevent.\n"
              "       If that is genuinely intended, pass --allow-zero-coverage as well; the "
              "waiver is recorded in the report.")
        sys.exit(2)
    pred=pd.read_csv(a.pred,dtype=str).fillna(""); gold=pd.read_csv(a.gold,dtype=str).fillna("")  # blank/None -> "" label, not NaN (cohen_kappa sort-safe)
    idc=a.id_col; fields=[v.strip() for v in a.on.split(",") if v.strip()]
    # A gate that assessed nothing is not a gate that passed. `--on ",,"` (or an empty
    # value) previously yielded fields=[], skipped the whole loop, and exited 0 with
    # "0 gated field(s) ... PASS" — the same not-assessed-reads-as-assessed collapse this
    # gate exists to prevent, committed inside the fix for it. Found by external audit.
    if not fields:
        print("STATUS=RED"); print("REASON=no_fields_to_validate")
        print(f"FATAL: --on resolved to zero field names (given: {a.on!r}). "
              "A validation run that assesses no field cannot pass.")
        sys.exit(2)
    for frame,label in ((gold,"gold"),(pred,"pred")):
        if idc not in frame.columns:
            print("STATUS=RED"); print("REASON=id_col_not_present")
            print(f"FATAL: --id-col {idc!r} absent from {label}.")
            sys.exit(2)
    if idc in fields:
        print("STATUS=RED"); print("REASON=id_col_in_fields")
        print(f"FATAL: --id-col {idc!r} also appears in --on; the join key cannot be a scored field.")
        sys.exit(2)
    for frame,label in ((gold,"gold"),(pred,"pred")):
        if "_merge" in frame.columns:
            print("STATUS=RED"); print("REASON=reserved_column")
            print(f"FATAL: {label} contains a '_merge' column, which collides with the join indicator.")
            sys.exit(2)
    missing=[v for v in fields if v not in gold.columns or v not in pred.columns]
    if missing:
        print("STATUS=RED"); print(f"REASON=field_not_present\nFATAL: field(s) absent from gold or pred: {', '.join(missing)}")
        sys.exit(2)
    # Duplicate ids silently reweight kappa and the coverage denominator: 100 duplicated
    # correct rows for one document outvote a wrong one. Refuse rather than reweight.
    for frame,label in ((gold,"gold"),(pred,"pred")):
        dup=frame[idc].duplicated().sum()
        if dup:
            print("STATUS=RED"); print("REASON=duplicate_ids")
            print(f"FATAL: {label} contains {int(dup)} duplicate value(s) of {idc!r}. "
                  "Duplicates reweight kappa and the coverage denominator; de-duplicate first.")
            sys.exit(2)
    # LEFT join — gold rows the annotator never returned are RETAINED as unscored.
    j=gold.merge(pred[[idc]+fields],on=idc,how="left",suffixes=("_g","_p"),indicator=True)
    absent=(j["_merge"]=="left_only")
    gated=fields if not a.primary_only else fields[:1]
    # schema_version: per-field metrics moved from the top level into `fields` in v2, and
    # `cohen_kappa` may now be null. Consumers reading report[field]["cohen_kappa"] must
    # branch on this key; its absence means the pre-2026-08 flat layout.
    rep={"schema_version":2,
         "fields":{},"gate":{"kappa_threshold":a.gate,"min_coverage":a.min_coverage,
                             "gated_fields":gated,"primary_only":bool(a.primary_only),
                             "coverage_check_waived":bool(a.min_coverage==0.0 and a.allow_zero_coverage)}}
    ok_gate=True; reasons=[]
    for v in fields:
        g=j[f"{v}_g"].fillna(""); p=j[f"{v}_p"].fillna("")
        gold_empty=(g=="")                             # no ground truth: legitimately unscoreable
        blank=(~absent)&(~gold_empty)&(p=="")          # annotator returned the row but left the field empty
        scored=(~absent)&(~gold_empty)&(p!="")
        n_gold=int((~gold_empty).sum()); n_scored=int(scored.sum())
        cov=(n_scored/n_gold) if n_gold else 0.0
        labels=sorted(set(g[scored]) | set(p[scored])) if n_scored else []
        if len(labels)>1:
            k=float(cohen_kappa_score(g[scored],p[scored],labels=labels))
            f=float(f1_score(g[scored],p[scored],average="macro",zero_division=0,labels=labels))
        else:
            k=float("nan"); f=float("nan")             # degenerate: <2 distinct labels across gold+pred
        # NaN is NEVER a pass. NOTE (mutation testing 2026-08-12): this isnan() guard is
        # currently REDUNDANT — the comparison is `>=`, and `nan >= x` is already False.
        # It is kept deliberately as defence-in-depth: the original defect was `k < gate`
        # (False for NaN, so the gate was never cleared), and anyone who flips this back to
        # a `<` form would silently reintroduce it. Reverting this line alone does not fail
        # the suite; that is expected, not a coverage gap.
        k_ok=(not math.isnan(k)) and k>=a.gate
        cov_ok=cov>=a.min_coverage
        rep["fields"][v]={
            "n_gold_labeled":n_gold,"n_scored":n_scored,
            "n_unscored":int(n_gold-n_scored),"coverage":round(cov,4),
            "excluded_by":{"annotator_absent":int((absent&~gold_empty).sum()),
                           "annotator_blank":int(blank.sum()),
                           "gold_empty":int(gold_empty.sum())},
            "cohen_kappa":(None if math.isnan(k) else round(k,3)),
            "kappa_undefined_reason":(None if not math.isnan(k) else
                ("no_scored_rows" if n_scored==0 else "fewer_than_two_distinct_labels")),
            "f1_macro":(None if math.isnan(f) else round(f,3)),
            "gated":bool(v in gated),
            "pass":bool(k_ok and cov_ok)}
        kd=("undefined" if math.isnan(k) else f"{k:.3f}")
        verdict="PASS" if (k_ok and cov_ok) else "FAIL"
        print(f"[{v}] gold={n_gold} scored={n_scored} coverage={cov:.1%} κ={kd} "
              f"F1={'undefined' if math.isnan(f) else f'{f:.3f}'}  {verdict}"
              f"{'' if v in gated else '  (not gated)'}")
        if int((absent&~gold_empty).sum()) or int(blank.sum()):
            print(f"      unscored: annotator_absent={int((absent&~gold_empty).sum())} "
                  f"annotator_blank={int(blank.sum())} gold_empty={int(gold_empty.sum())}")
        if n_scored: print(classification_report(g[scored],p[scored],zero_division=0))
        if v in gated and not (k_ok and cov_ok):
            ok_gate=False
            if not cov_ok: reasons.append(f"{v}: coverage {cov:.1%} < {a.min_coverage:.0%} — the annotator did not answer enough of the gold set")
            elif math.isnan(k): reasons.append(f"{v}: κ undefined ({rep['fields'][v]['kappa_undefined_reason']}) — a measurement that could not be computed is not a passing measurement")
            else: reasons.append(f"{v}: κ {k:.3f} < {a.gate}")
    # ---- instrument provenance (U18): a measurement is a number PLUS the question that produced it
    inst={"declared":False}
    if a.manifest and os.path.exists(a.manifest):
        try:
            m=json.load(open(a.manifest))
            if not isinstance(m,dict): raise ValueError("manifest is not a JSON object")
            pv=m.get("provider") if isinstance(m.get("provider"),dict) else {}
            iid=a.instrument_id or m.get("instrument_id")
            # A parseable manifest is not a declared instrument. `{}` previously produced
            # declared=True with every identifying field None — provenance that identifies
            # nothing. Require the fields a downstream constant must actually bind to.
            need={"instrument_id":iid,"model":pv.get("model")}
            absent=[k for k,v in need.items() if not (isinstance(v,str) and v.strip())]
            if absent:
                inst={"declared":False,
                      "error":f"manifest present but incomplete: missing {', '.join(absent)}"}
            else:
                inst={"declared":True,"instrument_id":iid,
                      "model":pv.get("model"),"api_base":pv.get("api_base"),
                      "strategy":pv.get("strategy"),"manifest":os.path.abspath(a.manifest),
                      "manifest_sha256":sha256_file(a.manifest),
                      "codebook":m.get("codebook"),"codebook_sha256":sha256_file(m.get("codebook") or ""),
                      "program_hash":a.program_hash,
                      "validated_at":datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
        except Exception as e:
            inst={"declared":False,"error":f"manifest unreadable: {str(e)[:120]}"}
    elif a.no_instrument:
        inst={"declared":False,"waived":True,
              "waiver_note":"--no-instrument passed; this report cannot certify WHICH instrument produced these labels"}
    rep["instrument"]=inst
    if not inst.get("declared") and not a.no_instrument:
        ok_gate=False
        reasons.append("instrument provenance absent — pass --manifest <run.json> (and optionally "
                       "--instrument-id / --program-hash), or --no-instrument to record an explicit waiver")
    rep["GATE_pass"]=bool(ok_gate)
    rep["STATUS"]="GREEN" if ok_gate else "RED"
    # allow_nan=False is defence-in-depth: kappa/F1 are already converted to None above, so
    # no bare NaN can reach the encoder today. Reverting it alone does not fail the suite
    # (mutation-tested). It stays so that a future change emitting a raw float NaN fails
    # loudly at write time instead of producing a report other JSON parsers reject.
    with open(a.out,"w") as fh: json.dump(rep,fh,indent=1,ensure_ascii=False,allow_nan=False)
    print(f"\nSTATUS={'GREEN' if ok_gate else 'RED'}")
    print(f"HARD GATE (κ ≥ {a.gate} AND coverage ≥ {a.min_coverage:.0%} on {len(gated)} gated field(s), "
          f"instrument declared): {'PASS — cleared for MODE 8' if ok_gate else 'FAIL — DO NOT scale'}")
    for r in reasons: print(f"  REASON: {r}")
    print("report ->",a.out)
    sys.exit(0 if ok_gate else 2)

# ----------------------------- DISTILL -----------------------------
def cmd_distill(a):
    import pandas as pd, re
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import cohen_kappa_score, f1_score
    import joblib
    try: import jieba; jieba.setLogLevel(60); HAS_J=True
    except Exception: HAS_J=False
    def analyzer(t):
        t=str(t).lower(); out=re.findall(r"[a-z][a-z'\-]{2,}",t)
        if re.search(r"[一-鿿]",t):
            if HAS_J: out+=[w for w in jieba.cut(t) if len(w)>=2 and re.search(r"[一-鿿]",w)]
            else:                                                 # jieba often absent on compute nodes -> CJK char-bigram fallback (else Chinese is silently dropped)
                ch=re.findall(r"[一-鿿]",t); out+=[ch[i]+ch[i+1] for i in range(len(ch)-1)]
        return out
    lab=pd.read_csv(a.labels,dtype=str); tcols=a.text_cols.split(","); idc=a.id_col
    samp=pd.read_csv(a.sample,dtype=str) if a.sample else lab
    d=lab.merge(samp[[idc]+tcols],on=idc) if a.sample else lab
    d["_text"]=d[tcols].fillna("").agg(" ".join,axis=1).str.slice(0,1200)
    d=d[d[a.target_col].notna()]
    pipe=Pipeline([("tf",TfidfVectorizer(analyzer=analyzer,min_df=10,max_df=0.4,max_features=30000)),
                   ("lr",LogisticRegression(max_iter=2000,C=4.0,class_weight="balanced",n_jobs=-1))])
    if a.gold and os.path.exists(a.gold):
        gold=pd.read_csv(a.gold,dtype=str); gs=set(gold[idc]); tr=d[~d[idc].isin(gs)]
        pipe.fit(tr._text,tr[a.target_col])
        g=gold.merge(samp[[idc]+tcols],on=idc); g["_text"]=g[tcols].fillna("").agg(" ".join,axis=1).str.slice(0,1200)
        pr=pipe.predict(g._text)
        print(f"DISTILLED vs gold: κ={cohen_kappa_score(g[a.target_col],pr):.3f} F1={f1_score(g[a.target_col],pr,average='macro',zero_division=0):.3f}")
    pipe.fit(d._text,d[a.target_col]); joblib.dump(pipe,a.model_out); print(f"model -> {a.model_out}")
    if a.corpus:
        spec=json.load(open(a.corpus)); hdr=not os.path.exists(a.out); nrow=0
        for ch in _corpus_frames(spec,[idc]+tcols,tcols):
            ch["pred"]=pipe.predict(ch["_text"]); nrow+=len(ch)
            ch[[idc,"pred"]].to_csv(a.out,mode="a",header=hdr,index=False); hdr=False
        n_unreadable=assert_readable(a.allow_unreadable,"distill")   # fail closed unless opted into
        print(f"scored corpus n={nrow} unreadable={n_unreadable} -> {a.out}"
              + (f"  (target subset = rows where pred=='{a.target}')" if a.target else ""))

def _corpus_frames(spec,cols,tcols):
    import pandas as pd
    for f in sorted(glob.glob(spec.get("category_glob",""))) if spec.get("category_glob") else []:
        for ch in robust_chunks(f,cols):
            ch["_text"]=ch[tcols].fillna("").agg(" ".join,axis=1).str.slice(0,1200); yield ch
    if spec.get("big_file"):
        bc=list(dict.fromkeys(cols+[spec.get("strata_col","query_category")]))
        for ch in robust_chunks(spec["big_file"],bc):
            if spec.get("big_only"): ch=ch[ch[spec["strata_col"]].isin(spec["big_only"])]
            ch["_text"]=ch[[c for c in tcols if c in ch]].fillna("").agg(" ".join,axis=1).str.slice(0,1200); yield ch

def main():
    ap=argparse.ArgumentParser(prog="annotate_engine")
    sub=ap.add_subparsers(dest="cmd",required=True)
    s=sub.add_parser("sample"); s.add_argument("--corpus",required=True); s.add_argument("--text-cols",required=True)
    s.add_argument("--id-col",default="video_id"); s.add_argument("--strata"); s.add_argument("--n",type=int,default=3000)
    s.add_argument("--pool",type=int,default=500); s.add_argument("--oversample"); s.add_argument("--target")
    s.add_argument("--seed",type=int,default=23); s.add_argument("--out",required=True)
    s.add_argument("--allow-unreadable",type=int,default=0)   # safe path is the default (U10)
    s.set_defaults(fn=cmd_sample)
    an=sub.add_parser("annotate"); an.add_argument("--manifest",required=True); an.add_argument("--dry-run",action="store_true")
    an.add_argument("--resume",action="store_true"); an.add_argument("--limit",type=int); an.add_argument("--shard",type=int,default=0)
    an.add_argument("--nshards",type=int,default=1); an.set_defaults(fn=cmd_annotate)
    v=sub.add_parser("validate"); v.add_argument("--pred",required=True); v.add_argument("--gold",required=True)
    v.add_argument("--on",default="label"); v.add_argument("--id-col",default="video_id"); v.add_argument("--gate",type=float,default=0.70)
    # Coverage: of the gold rows that HAVE a label, what share did the annotator actually answer?
    # Below this, the kappa is computed on a self-selected subsample and does not describe the annotator.
    v.add_argument("--min-coverage",type=float,default=0.95)
    v.add_argument("--allow-zero-coverage",action="store_true")  # explicit opt-out, recorded on the artifact
    # Default is ALL fields in --on. --primary-only restores the pre-2026-08-11 first-field-only
    # behaviour for back-compat; it is recorded in the report so the choice is auditable.
    v.add_argument("--primary-only",action="store_true")
    # Instrument provenance (U18). Required unless explicitly waived.
    v.add_argument("--manifest"); v.add_argument("--instrument-id"); v.add_argument("--program-hash")
    v.add_argument("--no-instrument",action="store_true")
    v.add_argument("--out",default="output/tables/validation_report.json"); v.set_defaults(fn=cmd_validate)
    d=sub.add_parser("distill"); d.add_argument("--labels",required=True); d.add_argument("--sample"); d.add_argument("--corpus")
    d.add_argument("--text-cols",required=True); d.add_argument("--id-col",default="video_id"); d.add_argument("--target-col",default="relevance")
    d.add_argument("--target"); d.add_argument("--gold"); d.add_argument("--model-out",default="output/models/distilled.joblib")
    d.add_argument("--out",default="output/tables/labels_full.csv")
    d.add_argument("--allow-unreadable",type=int,default=0)   # safe path is the default (U10)
    d.set_defaults(fn=cmd_distill)
    a=ap.parse_args(); a.fn(a)

if __name__=="__main__": main()
