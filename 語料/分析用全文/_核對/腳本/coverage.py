"""Omission check: every RapidOCR line (machine reading, noisy) should fuzzily appear in the final transcription.
usage: python3 coverage.py RAPIDOCR.txt FINAL.txt  -> lists OCR lines with weak match (possible omitted line)."""
import re, sys, difflib
WS = re.compile(r"[\s　]+")
final = WS.sub("", re.sub(r"〔[^〕]*〕", "", open(sys.argv[2], encoding="utf-8").read()))
weak = 0
for line in open(sys.argv[1], encoding="utf-8"):
    l = WS.sub("", line)
    if len(l) < 6: continue
    best = 0.0; n = len(l)
    for i in range(0, max(1, len(final) - n + 1), 2):
        r = difflib.SequenceMatcher(None, l, final[i:i+n], autojunk=False).quick_ratio()
        if r > best:
            best = max(best, difflib.SequenceMatcher(None, l, final[i:i+n], autojunk=False).ratio())
            if best > 0.8: break
    if best < 0.55:
        weak += 1; print(f"WEAK {best:.2f}: {l}")
print(f"weak_lines={weak}")
