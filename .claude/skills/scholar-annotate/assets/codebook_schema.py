#!/usr/bin/env python3
"""
Compile a human-authored codebook (markdown) into the single source of truth for annotation:
the system prompt + the output JSON schema (field names + allowed labels) + a validator.

A codebook.md must contain ONE fenced ```json block declaring the machine schema, e.g.:

    ```json
    {"out_fields": ["relevance", "discourse_frame", "confidence"],
     "labels": {
       "relevance": ["ON_TOPIC","ADJACENT","BACKGROUND","PROMOTIONAL","OFFTOPIC","UNCLEAR"],
       "discourse_frame": ["frame_a","frame_b","...","none"],
       "confidence": ["high","medium","low"]},
     "conditional": {"discourse_frame": {"only_if": {"relevance": "ON_TOPIC"}, "else": "none"}}}
    ```

Everything else in the markdown (the class definitions, boundary rules, decision order) becomes
the system-prompt body. `rationale` is always added as a free-text field.
"""
import json, re, os

def load(path):
    md=open(path, encoding="utf-8", errors="ignore").read()
    m=re.search(r"```json\s*(\{.*?\})\s*```", md, re.S)
    if not m: raise ValueError(f"{path}: no ```json schema block found (see codebook_schema.py docstring)")
    schema=json.loads(m.group(1))
    out_fields=schema["out_fields"]; labels=schema.get("labels",{}); cond=schema.get("conditional",{})
    prose=md.replace(m.group(0),"").strip()
    fields_desc=" ".join(f'"{f}":"one of [{"|".join(labels.get(f,[]))}]"' if f in labels else f'"{f}":"…"'
                         for f in out_fields+["rationale"])
    system=(prose+"\n\n"
            "Classify using ONLY the metadata provided. Output STRICT JSON with exactly these fields: "
            "{"+fields_desc+"}. rationale = one short sentence, invent nothing. Respond with the JSON object only.")
    def validate(obj):
        out={}
        for f in out_fields:
            v=obj.get(f)
            allowed=labels.get(f)
            if allowed and v not in allowed:
                v=allowed[-1] if f in cond else (allowed[0] if allowed else "")
            out[f]=v if v is not None else ""
        # conditional coercion (e.g. discourse_frame='none' unless relevance==ON_TOPIC)
        for f,rule in cond.items():
            oi=rule.get("only_if",{})
            if not all(out.get(k)==val for k,val in oi.items()):
                out[f]=rule.get("else","")
        out["rationale"]=str(obj.get("rationale",""))[:300]
        return out
    return {"system":system,"out_fields":out_fields,"labels":labels,"validate":validate,
            "conditional":cond}
