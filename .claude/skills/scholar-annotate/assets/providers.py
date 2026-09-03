#!/usr/bin/env python3
"""
Provider abstraction for scholar-annotate. Unifies:
  * OpenAI          (live chat.completions + Batch API)          key_env=OPENAI_API_KEY
  * Anthropic       (live messages + Message Batches)            key_env=ANTHROPIC_API_KEY
  * local           (any OpenAI-compatible server: llama.cpp/ollama/vLLM/GLM) no real key needed
Manifest `provider` block: {strategy: batch|async|local, provider: openai|anthropic|local,
  model, api_base, key_env}. temperature=0 always (replicability).
"""
import os, io, csv, json, re, time, threading, datetime
JSON_RE=re.compile(r"\{.*\}", re.S)
class QuotaStop(Exception): pass

def _oai_kw(model, max_out=300):
    """OpenAI param compatibility. GPT-5 / o-series (reasoning) models require
    `max_completion_tokens` and reject `temperature` != 1 (default only). Everything else
    (incl. local OpenAI-compatible servers like llama.cpp/vLLM) uses max_tokens + temperature=0."""
    m=(model or "").lower()
    if m.startswith("gpt-5") or m.startswith("o1") or m.startswith("o3") or m.startswith("o4"):
        return {"max_completion_tokens": max(max_out, 900)}   # no temperature; bigger budget for reasoning tokens
    return {"temperature": 0, "max_tokens": max_out}

def _key(pv):
    base=pv.get("api_base","")
    if pv["provider"]=="local" or "127.0.0.1" in base or "localhost" in base: return "sk-local"
    k=os.environ.get(pv.get("key_env","")) or os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not k: raise SystemExit(f"Set {pv.get('key_env','the provider API key')} env var.")
    return k

_local=threading.local()
def _oai(pv):
    if not hasattr(_local,"o"):
        from openai import OpenAI
        base=pv.get("api_base") or None
        if pv.get("provider")=="local" and os.environ.get("GLM_API_BASE"):
            base=os.environ["GLM_API_BASE"]   # honor a per-job dynamic server port (HPC sbatch exports GLM_API_BASE)
        _local.o=OpenAI(api_key=_key(pv), base_url=base, timeout=120, max_retries=0)
    return _local.o
def _ant(pv):
    if not hasattr(_local,"a"):
        from anthropic import Anthropic
        _local.a=Anthropic(api_key=_key(pv))
    return _local.a

def _user(r, tcols):
    return json.dumps({c:str(r.get(c,""))[:600] for c in tcols}, ensure_ascii=False)
def _parse(txt, cb):
    try: m=JSON_RE.search(txt or ""); o=json.loads(m.group(0)) if m else {}
    except Exception: o={}
    return cb["validate"](o)

def build_fewshot(cfg, cb, tcols):
    """Few-shot messages from a gold csv (cfg={'gold':path,'k':2}); k per class of the first field."""
    if not cfg or not cfg.get("gold") or not os.path.exists(cfg["gold"]): return []
    import pandas as pd
    d=pd.read_csv(cfg["gold"],dtype=str).fillna("")
    f0=cb["out_fields"][0]
    d=d[d[f0]!=""].groupby(f0,group_keys=False).head(cfg.get("k",2))
    msgs=[]
    for _,r in d.iterrows():
        inp={c:str(r.get(c,""))[:300] for c in tcols}
        out={f:r.get(f,"") for f in cb["out_fields"]}; out["rationale"]="(gold)"
        msgs.append(("user",json.dumps(inp,ensure_ascii=False))); msgs.append(("assistant",json.dumps(out,ensure_ascii=False)))
    return msgs

def annotate_one(r, tcols, cb, fewshot, pv):
    """LIVE call (async/local). Returns (status, ann, (in_tok,out_tok))."""
    sys_msg=cb["system"]; usr=_user(r,tcols)
    for attempt in range(4):
        try:
            if pv["provider"]=="anthropic":
                msgs=[{"role":r0,"content":c} for r0,c in fewshot]+[{"role":"user","content":usr}]
                resp=_ant(pv).messages.create(model=pv["model"],max_tokens=300,temperature=0,system=sys_msg,messages=msgs)
                txt=resp.content[0].text; usage=(resp.usage.input_tokens,resp.usage.output_tokens)
            else:  # openai / local (OpenAI-compatible)
                msgs=[{"role":"system","content":sys_msg}]+[{"role":r0,"content":c} for r0,c in fewshot]+[{"role":"user","content":usr}]
                kw=dict(_oai_kw(pv["model"]))
                if pv["provider"]=="openai": kw["response_format"]={"type":"json_object"}
                resp=_oai(pv).chat.completions.create(model=pv["model"],messages=msgs,**kw)
                txt=resp.choices[0].message.content
                u=getattr(resp,"usage",None); usage=((u.prompt_tokens,u.completion_tokens) if u else (0,0))
            return ("ok",_parse(txt,cb),usage)
        except Exception as e:
            low=str(e).lower()
            if any(k in low for k in ("insufficient","balance","quota exceeded")): raise QuotaStop(str(e)[:120])
            time.sleep(min(2**attempt,8))
    return ("retry",{},(0,0))

# ---------------- BATCH strategy ----------------
def run_batch(df, idc, strata, tcols, cb, fewshot, pv, out_dir, HEAD, ckpt):
    """Submit + poll + collect a managed Batch (OpenAI or Anthropic). Writes annot_batch.csv."""
    data=df.to_dict("records"); state=os.path.join(out_dir,"batch_state.json")
    sysm=cb["system"]
    if pv["provider"]=="anthropic":
        ac=_ant(pv)
        reqs=[{"custom_id":str(r[idc]),
               "params":{"model":pv["model"],"max_tokens":300,"temperature":0,"system":sysm,
                         "messages":[{"role":a,"content":c} for a,c in fewshot]+[{"role":"user","content":_user(r,tcols)}]}}
              for r in data]
        b=ac.messages.batches.create(requests=reqs); json.dump({"anthropic":b.id},open(state,"w"))
        print("anthropic batch:",b.id,"— poll then re-run to collect")
        while ac.messages.batches.retrieve(b.id).processing_status!="ended": time.sleep(30)
        out=os.path.join(out_dir,"annot_batch.csv"); w=csv.DictWriter(open(out,"w",newline=""),fieldnames=HEAD); w.writeheader()
        ck=open(ckpt,"a")
        for res in ac.messages.batches.results(b.id):
            if res.result.type=="succeeded":
                ann=_parse(res.result.message.content[0].text,cb); w.writerow({idc:res.custom_id,strata:"",**ann}); ck.write(res.custom_id+"\n")
        print("wrote",out)
    else:  # openai
        oc=_oai(pv); jl=io.StringIO()
        for r in data:
            msgs=[{"role":"system","content":sysm}]+[{"role":a,"content":c} for a,c in fewshot]+[{"role":"user","content":_user(r,tcols)}]
            body={"model":pv["model"],"response_format":{"type":"json_object"},"messages":msgs, **_oai_kw(pv["model"])}
            jl.write(json.dumps({"custom_id":str(r[idc]),"method":"POST","url":"/v1/chat/completions","body":body},ensure_ascii=False)+"\n")
        p=os.path.join(out_dir,"batch_input.jsonl"); open(p,"w").write(jl.getvalue())
        f=oc.files.create(file=open(p,"rb"),purpose="batch")
        b=oc.batches.create(input_file_id=f.id,endpoint="/v1/chat/completions",completion_window="24h")
        json.dump({"openai":b.id},open(state,"w")); print("openai batch:",b.id)
        while oc.batches.retrieve(b.id).status not in ("completed","failed","expired"): time.sleep(30)
        bb=oc.batches.retrieve(b.id)
        if bb.status!="completed": print("batch status:",bb.status); return
        out=os.path.join(out_dir,"annot_batch.csv"); w=csv.DictWriter(open(out,"w",newline=""),fieldnames=HEAD); w.writeheader()
        ck=open(ckpt,"a")
        for line in oc.files.content(bb.output_file_id).text.splitlines():
            o=json.loads(line)
            try: content=o["response"]["body"]["choices"][0]["message"]["content"]
            except Exception: content=""
            ann=_parse(content,cb); w.writerow({idc:o["custom_id"],strata:"",**ann}); ck.write(o["custom_id"]+"\n")
        print("wrote",out)

# ---------------- cost pre-flight ----------------
_PRICE={  # USD per 1M tokens (in,out) — VERIFY current pricing; local=0
 "gpt-4o-mini":(0.15,0.60),"gpt-4.1-mini":(0.40,1.60),"gpt-4.1":(2.0,8.0),
 "claude-opus-4-8":(5.0,25.0),"claude-sonnet-4-6":(3.0,15.0)}
def estimate_cost(pv, n, fewshot_len=0, in_tok=None, out_tok=220):
    if pv["provider"]=="local" or pv.get("strategy")=="local": return f"local model — $0 (compute only)"
    it=in_tok if in_tok else 400+fewshot_len*120; ot=out_tok
    pr=_PRICE.get(pv["model"],(2.0,8.0)); usd=n*(it*pr[0]+ot*pr[1])/1e6
    disc=" (Batch API ≈ half)" if pv.get("strategy")=="batch" else ""
    return f"~${usd:,.2f}{disc} (list price; VERIFY {pv['model']} pricing)"
