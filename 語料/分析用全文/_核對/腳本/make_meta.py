"""Write tools/corpus_meta.json: per-work header data for the 分析用全文 files (source of truth: thesis 第三章第二節 corpus table + crawl results)."""
import json, os
S = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
crawl = json.load(open(f"{S}/crawl/html/_crawl_meta.json", encoding="utf-8"))
D = "2026-10-11"

PP = "https://www.paulpangstory.com/blog/"
WEB = {  # W: (title, short, date line, url slug, collected-in note)
    "W01": ("當天使飛過松山", "當天使飛過松山", "《澳門日報》「生活——伴我成長」兒童文學版，2014-05-29", "2014-05-29-2015-2015-11", "後收《澳門作家文集2015》（2015-11）"),
    "W02": ("東望洋山上的小英雄", "東望洋山上的小英雄", "《澳門日報》「生活——伴我成長」兒童文學版，2014-06-26", "2014-06-26-2015-07", "後收《小城小世界——澳門兒童文學精選》（2015-07）"),
    "W03": ("旅遊塔上的天才", "旅遊塔上的天才", "《澳門日報》「生活——伴我成長」兒童文學版，2014-10-16", "2014-10-16", ""),
    "W04": ("龜兔競賽", "龜兔競賽", "《小城大世界——澳門兒童文學精選》（民政總署，2014年9月）", "2014-09-01a628a8-f917-4d92-b46d-ca47af60e244", ""),
}
REG = {"W01": "S9", "W02": "S9", "W03": "S9", "W04": "S1"}

# text-layer works: W -> (title, author, issue, issue date, printed pages, pdf pages)
TL = {
    "W07": ("小王子寶石城之旅", "羊豬老師（楊穎虹）", 3, "2016年5月"),
    "W08": ("尋找時間的小黑貓", "彭執中", 3, "2016"),
    "W09": ("想當作家的小姑娘", "劉居上", 4, "2016"),
    "W10": ("路環「七星伴月」傳說", "黃健威", 6, "2017"),
    "W11": ("頑皮的小天鴿", "黃子超", 8, "2018"),
    "W12": ("燈塔的守護", "黃健威", 8, "2018"),
    "W13": ("假期中的快快與慢慢", "月鳥", 10, "2019年12月"),
    "W18": ("疫症裡的星光", "黃耀鋒", 11, "2020年11月，疫情特輯"),
    "W19": ("澳門小王子放學路上", "陳康妮", 12, "2020年12月"),
    "W20": ("跳到對岸去", "土皮", 13, "2021年11月"),
    "W21": ("相互補足", "雙口（呂志鵬）", 13, "2021年11月"),
    "W22": ("林茂塘之夢", "愛竹（呂志鵬）", 15, "2022年11月"),
    "W23": ("豆丁和他最愛吃的「豆腐花」", "安娜", 19, "2024年7月"),
    "W24": ("青洲山裡的原住民", "壹壹", 19, "2024年7月"),
    "W25": ("三石", "三十三", 21, "2025年6月"),
    "W26": ("尋找綠豆的斑鳩", "劉安琪", 22, "2025年11月"),
}
SCAN = {  # W: (title, author, venue, src key, printed pages, pdf pages)
    "W05": ("披着白毛毛的小妖怪", "紫菱", "《童一枝筆》第2期（澳門筆會，2015年9月）", "tyzb02", "09–10", "12–13"),
    "W06": ("海角遊魂的鬼故", "夢雲澤", "《童一枝筆》第2期（澳門筆會，2015年9月）", "tyzb02", "21–22", "24–25"),
    "W14": ("一六二二年", "黃耀鋒", "《澳門奇趣門——少年小說精選》（澳門筆會，2020年1月）", "qiqumen", "33–36", "33–36"),
    "W15": ("草堆街醫學之旅", "譚健鍬", "《澳門奇趣門——少年小說精選》（澳門筆會，2020年1月）", "qiqumen", "61–72", "61–72"),
    "W16": ("東望洋山尋寶記", "彭執中", "《澳門奇趣門——少年小說精選》（澳門筆會，2020年1月）", "qiqumen", "111–130", "112–131"),
    "W17": ("飛走的泳棚", "太皮", "《澳門奇趣門——少年小說精選》（澳門筆會，2020年1月）", "qiqumen", "181–189", "182–190"),
}
TL_PAGES = json.load(open(f"{S}/tools/tl_pages.json", encoding="utf-8")) if os.path.exists(f"{S}/tools/tl_pages.json") else {}
SCAN_NOTE = json.load(open(f"{S}/tools/scan_notes.json", encoding="utf-8")) if os.path.exists(f"{S}/tools/scan_notes.json") else {}

meta = {}
for w in sorted(set(WEB) | set(TL) | set(SCAN)):
    if w in WEB:
        t, short, line, slug, note = WEB[w]
        meta[w] = dict(kind="web", title=t, short=short, author="彭執中",
            venue=line + (f"；{note}" if note else ""),
            register=f"{REG[w]}｜2014｜{'官方委製' if w == 'W04' else '民間創作'}（第三章第二節核心語料表）",
            source=f"作者網站公開轉載 {PP}{slug}（{D} 以 crawl4ai 重爬，正文與 2026-09 存檔逐字一致）；存檔：語料/逐件全文/{w}_*.html／.txt",
            method="取網頁正文，刪網站導航、上下篇連結與 cookie 說明；正文一字未改（腳本驗證僅有刪除、無增改）。分段依網頁原有段落與換行。報端原版未見，全文出自作者自建網站，記「來源自涉」旗標（見第三章第二節）。",
            pages_note="網頁轉載無頁碼，不設〔頁N〕標記。")
    elif w in TL:
        t, a, iss, date = TL[w]
        pg = TL_PAGES.get(w, {})
        key = f"tyzb{iss:02d}"
        meta[w] = dict(kind="textlayer", title=t, short=t.replace("「", "").replace("」", ""), author=a,
            venue=f"《童一枝筆》第{iss}期（澳門筆會，{date}）" + (f"，頁{pg['printed']}" if pg.get("printed") else ""),
            register=f"S4｜{date[:4]}｜民間創作（第三章第二節核心語料表）",
            source=f"澳門虛擬圖書館數碼化原刊 https://www.macaudata.mo/showbook?bno=j7600{iss:02d} ，PDF {crawl[key].get('pdf_url', '')}"
                   + (f" 第{pg['pdf']}頁" if pg.get("pdf") else "") + f"；出版方 PDF 文字層（{D} 重爬，與 2026-09 存檔逐字一致）",
            method="以出版方文字層為底本，逐頁對照原刊頁影核對完整性、字形映射與閱讀次序；僅刪頁碼、欄目書眉、與檔頭重複的篇名署名及版面符號，正文一字未改（腳本驗證僅有刪除、無增改）。分段依原刊頁影。" + pg.get("note", ""),
            pages_note="〔頁N〕為原刊印刷頁碼，標於該頁正文起處；段落跨頁時標於段中。")
    else:
        t, a, venue, key, printed, pdfp = SCAN[w]
        url = "https://www.macaudata.mo/showbook?bno=" + ("j760002" if key == "tyzb02" else "b001978")
        meta[w] = dict(kind="scan", title=t, short=t, author=a, venue=f"{venue}，頁{printed}",
            register=f"{'S4｜2015' if key == 'tyzb02' else 'S2｜2020'}｜民間創作（第三章第二節核心語料表）",
            source=f"澳門虛擬圖書館數碼化掃描 {url} ，PDF {crawl[key].get('pdf_url', '')} 第{pdfp}頁（{D} 重爬，與存檔全帙 MD5 一致）；存檔切頁：語料/逐件全文/{w}_*.pdf",
            method="掃描本無文字層，逐頁兩次獨立逐字轉錄，逐字比對後就分歧處對照原頁放大影像裁決，並以機器 OCR 核查漏行；逐字照錄原刊，錯字、異體、標點不改。" + SCAN_NOTE.get(w, ""),
            pages_note="〔頁N〕為原刊印刷頁碼，標於該頁正文起處；段落跨頁時標於段中。")
json.dump(meta, open(f"{S}/tools/corpus_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(meta), "works")
