"""Verify a cleaned text is the raw text with ONLY deletions (plus whitespace and 〔…〕 markers).
usage: python3 check_clean.py RAW CLEAN
RAW: body after the first line made of '=' (if present). CLEAN: same convention.
Exit 0 iff no insertions/replacements. Prints every deletion with context for review."""
import re, sys, difflib
def body(path):
    t = open(path, encoding="utf-8").read()
    m = re.search(r"^={8,}\s*$", t, re.M)
    return t[m.end():] if m else t
WS = re.compile(r"[\s　 ​﻿]+")
raw = WS.sub("", body(sys.argv[1]))
clean = WS.sub("", re.sub(r"〔[^〕]*〕", "", body(sys.argv[2])))
sm = difflib.SequenceMatcher(None, raw, clean, autojunk=False)
bad = 0; dels = []
for op, i1, i2, j1, j2 in sm.get_opcodes():
    if op == "equal": continue
    ctx = raw[max(0, i1-12):i1] + "【" + raw[i1:i2] + "】" + raw[i2:i2+12]
    if op == "delete": dels.append((raw[i1:i2], ctx))
    else:
        bad += 1
        print(f"!! {op.upper()} raw[{i1}:{i2}]={raw[i1:i2]!r} -> clean={clean[j1:j2]!r}  ctx: {ctx}")
print(f"raw_chars={len(raw)} clean_chars={len(clean)} deletions={len(dels)} deleted_chars={sum(len(d) for d,_ in dels)} violations={bad}")
for d, ctx in dels: print(f"  - DEL {d!r}  ctx: {ctx}")
sys.exit(1 if bad else 0)
