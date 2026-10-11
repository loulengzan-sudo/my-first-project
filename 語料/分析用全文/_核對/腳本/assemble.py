"""Assemble the 26 analysis-ready texts (分析用全文) from verified bodies.
Scan works: page files trans/<W>_pNN_final.txt (adjudicated) -> joined with 〔頁N〕 markers.
Text-layer / web works: clean/<W>_body.txt (already page-marked where pages exist).
usage: python3 assemble.py OUTDIR"""
import glob, json, os, re, sys, unicodedata

S = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = sys.argv[1]
META = json.load(open(f"{S}/tools/corpus_meta.json", encoding="utf-8"))


def scan_body(w):
    """Join adjudicated per-page transcriptions; a page that continues the previous paragraph is glued inline."""
    paras = []
    files = sorted(glob.glob(f"{S}/trans/{w}_p[0-9][0-9]_final.txt"))
    assert files, f"no final pages for {w}"
    for f in files:
        t = open(f, encoding="utf-8").read()
        head, _, body = t.partition("\n----\n")
        page = re.search(r"印刷頁碼:\s*(\S+)", head).group(1)
        cont = re.search(r"起首延續上頁段落:\s*(\S+)", head).group(1) == "是"
        lines = [l.strip() for l in body.split("\n") if l.strip()]
        marker = f"〔頁{page}〕"
        if cont and paras and lines:
            paras[-1] += marker + lines[0]
            lines = lines[1:]
        else:
            paras.append(marker) if not lines else None
            if lines:
                lines[0] = marker + lines[0]
        paras.extend(lines)
    return "\n".join(p for p in paras if p != "")


def char_count(body):
    """Count characters excluding whitespace, punctuation/symbols and 〔…〕 editorial markers."""
    b = re.sub(r"〔[^〕]*〕", "", body)
    return sum(1 for c in b if not c.isspace() and unicodedata.category(c)[0] not in "PSZ")


os.makedirs(OUT, exist_ok=True)
index = []
for w, m in META.items():
    body = scan_body(w) if m["kind"] == "scan" else open(f"{S}/clean/{w}_body.txt", encoding="utf-8").read().strip()
    n = char_count(body)
    head = [f"{w}〈{m['title']}〉", f"作者：{m['author']}", f"所在：{m['venue']}",
            f"語料表登記：{m['register']}", f"所據文本：{m['source']}", f"整理與核對：{m['method']}",
            f"頁碼標記：{m['pages_note']}", f"正文字數：{n}（不計標點、空白與標記）", "核對日期：2026-10-11", "=" * 40]
    fn = f"{w}_{m['short']}.txt"
    open(f"{OUT}/{fn}", "w", encoding="utf-8").write("\n".join(head) + "\n" + body + "\n")
    index.append((w, m["title"], m["author"], m["kind"], n, fn))
    print(w, n, fn)
json.dump(index, open(f"{S}/tools/assembled_index.json", "w", encoding="utf-8"), ensure_ascii=False)
