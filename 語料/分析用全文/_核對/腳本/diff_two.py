"""Char-level diff of two transcriptions (whitespace and 〔…〕 markers ignored).
usage: python3 diff_two.py A.txt B.txt  -> prints each disagreement with context."""
import re, sys, difflib
WS = re.compile(r"[\s　 ​﻿]+")
def norm(p): return WS.sub("", re.sub(r"〔[^〕]*〕", "", open(p, encoding="utf-8").read()))
a, b = norm(sys.argv[1]), norm(sys.argv[2])
sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
n = 0
for op, i1, i2, j1, j2 in sm.get_opcodes():
    if op == "equal": continue
    n += 1
    print(f"#{n} {op}: A={a[i1:i2]!r} B={b[j1:j2]!r}  ctxA: {a[max(0,i1-15):i1]}【{a[i1:i2]}】{a[i2:i2+15]}")
print(f"lenA={len(a)} lenB={len(b)} disagreements={n} similarity={sm.ratio():.4f}")
