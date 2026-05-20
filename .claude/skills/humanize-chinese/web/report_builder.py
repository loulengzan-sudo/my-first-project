#!/usr/bin/env python3
"""AIGC 检测报告 PDF 生成器（专业版）

纯代码生成 PDF，不依赖 OCR 或图片识别。
支持词级高亮标注，标注推高 AIGC 率的具体词/短语并给出原因。

用法:
    from report_builder import build_report_pdf
    pdf_bytes = build_report_pdf(report_data)
"""

import io
import os
import platform
import random
import string
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor, white, black, Color
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, LongTable, Flowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ─── 颜色体系（学术蓝为主色调）───
C_PRIMARY = HexColor('#1a365d')
C_ACCENT = HexColor('#2b6cb0')
C_ACCENT_LIGHT = HexColor('#ebf4ff')
C_AIGC = HexColor('#dc2626')        # 标注红
C_AIGC_BG = HexColor('#fef2f2')     # 标注红背景
C_AIGC_BORDER = HexColor('#fca5a5')
C_HUMAN = HexColor('#059669')       # 人工绿
C_TEXT = HexColor('#1e293b')
C_TEXT_LIGHT = HexColor('#475569')
C_MUTED = HexColor('#94a3b8')
C_BORDER = HexColor('#cbd5e1')
C_BG_LIGHT = HexColor('#f8fafc')
C_BG_ALT = HexColor('#f1f5f9')
C_WHITE = white
C_DANGER = HexColor('#b91c1c')

PAGE_W, PAGE_H = A4
CW = PAGE_W - 50 * mm

# ─── 字体 ───
_cjk = None

def _font():
    global _cjk
    if _cjk: return _cjk
    _register_font()
    return _cjk

def _register_font():
    global _cjk
    if _cjk: return _cjk
    s = platform.system()

    # 1. 优先尝试 TTF/TTC 格式的 TrueType 字体
    ttf_paths = (
        ['/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
         '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
         '/usr/share/fonts/opentype/smiley-sans/SmileySans-Oblique.ttf']
        if s == 'Linux' else
        ['/System/Library/Fonts/PingFang.ttc']
        if s == 'Darwin' else
        ['C:/Windows/Fonts/msyh.ttc', 'C:/Windows/Fonts/simsun.ttc']
    )
    for p in ttf_paths:
        if os.path.exists(p):
            try:
                pdfmetrics.registerFont(TTFont('CJK', p, subfontIndex=0))
                _cjk = 'CJK'; return _cjk
            except Exception:
                continue

    # 2. 回退到 reportlab 内置 CID 字体（支持 CFF/PostScript 轮廓）
    cid_names = ['STSong-Light', 'MSung-Light', 'HYSMyeongJo-Medium']
    for name in cid_names:
        try:
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            f = UnicodeCIDFont(name)
            pdfmetrics.registerFont(f)
            _cjk = name; return _cjk
        except Exception:
            continue

    raise RuntimeError('未找到可用的中文字体')


# ─── 样式 ───
def _S():
    f = _font()
    return {
        'cover_title': ParagraphStyle('ct', fontName=f, fontSize=22, leading=30,
            textColor=C_WHITE, alignment=TA_CENTER, wordWrap='CJK'),
        'cover_sub': ParagraphStyle('cs', fontName=f, fontSize=11, leading=16,
            textColor=HexColor('#93c5fd'), alignment=TA_CENTER, wordWrap='CJK'),
        'cover_info': ParagraphStyle('ci', fontName=f, fontSize=10, leading=15,
            textColor=C_TEXT_LIGHT, wordWrap='CJK'),
        'cover_info_v': ParagraphStyle('civ', fontName=f, fontSize=10, leading=15,
            textColor=C_TEXT, wordWrap='CJK'),
        'h1': ParagraphStyle('h1', fontName=f, fontSize=16, leading=22,
            textColor=C_PRIMARY, spaceBefore=18, spaceAfter=8, wordWrap='CJK'),
        'h2': ParagraphStyle('h2', fontName=f, fontSize=12, leading=17,
            textColor=C_ACCENT, spaceBefore=12, spaceAfter=6, wordWrap='CJK'),
        'body': ParagraphStyle('bd', fontName=f, fontSize=10, leading=17,
            textColor=C_TEXT, wordWrap='CJK'),
        'body_sm': ParagraphStyle('bs', fontName=f, fontSize=9, leading=14,
            textColor=C_TEXT_LIGHT, wordWrap='CJK'),
        'note': ParagraphStyle('nt', fontName=f, fontSize=8, leading=12,
            textColor=C_MUTED, wordWrap='CJK'),
        'th': ParagraphStyle('th', fontName=f, fontSize=9, leading=13,
            textColor=C_WHITE, alignment=TA_CENTER, wordWrap='CJK'),
        'tc': ParagraphStyle('tc', fontName=f, fontSize=9, leading=13,
            textColor=C_TEXT, alignment=TA_CENTER, wordWrap='CJK'),
        'tcl': ParagraphStyle('tcl', fontName=f, fontSize=9, leading=13,
            textColor=C_TEXT, alignment=TA_LEFT, wordWrap='CJK'),
        'big_num': ParagraphStyle('bn', fontName=f, fontSize=32, leading=38,
            textColor=C_AIGC, alignment=TA_CENTER, wordWrap='CJK'),
        'big_num_g': ParagraphStyle('bng', fontName=f, fontSize=32, leading=38,
            textColor=C_HUMAN, alignment=TA_CENTER, wordWrap='CJK'),
        'label': ParagraphStyle('lb', fontName=f, fontSize=10, leading=14,
            textColor=C_MUTED, alignment=TA_CENTER, wordWrap='CJK'),
        'reason': ParagraphStyle('rs', fontName=f, fontSize=8.5, leading=13,
            textColor=C_DANGER, wordWrap='CJK'),
        'ftr': ParagraphStyle('ft', fontName=f, fontSize=8, leading=12,
            textColor=C_MUTED, alignment=TA_CENTER, wordWrap='CJK'),
    }


# ─── 自定义组件 ───
class CoverBanner(Flowable):
    """封面顶部蓝色横幅"""
    def __init__(self, w, h=55*mm):
        Flowable.__init__(self)
        self.width = w; self.height = h
    def draw(self):
        self.canv.setFillColor(C_PRIMARY)
        self.canv.roundRect(-25*mm, 0, PAGE_W, self.height, 0, fill=1, stroke=0)
        # 装饰线
        self.canv.setStrokeColor(HexColor('#3b82f6'))
        self.canv.setLineWidth(0.5)
        self.canv.line(-25*mm, 0, PAGE_W - 25*mm, 0)


class Divider(Flowable):
    def __init__(self, w, h=1.5, color=C_BORDER, sb=6, sa=8):
        Flowable.__init__(self)
        self.width = w; self.height = h; self.color = color
        self.spaceAfter = sa; self.spaceBefore = sb
    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)


class DonutChart(Flowable):
    def __init__(self, w, h, aigc_pct, human_pct):
        Flowable.__init__(self)
        self.width = w; self.height = h
        self._a = aigc_pct; self._h = human_pct
    def draw(self):
        cx, cy = self.width/2, self.height/2
        r = min(cx, cy) - 8
        aa = self._a/100*360; ha = self._h/100*360
        self.canv.setFillColor(C_HUMAN)
        self.canv.setStrokeColor(C_WHITE); self.canv.setLineWidth(2)
        self.canv.wedge(cx-r, cy-r, cx+r, cy+r, 90, -ha, fill=1, stroke=1)
        self.canv.setFillColor(C_AIGC)
        self.canv.setStrokeColor(C_WHITE); self.canv.setLineWidth(2)
        self.canv.wedge(cx-r, cy-r, cx+r, cy+r, 90-ha, -aa, fill=1, stroke=1)
        ir = r*0.58
        self.canv.setFillColor(C_WHITE)
        self.canv.circle(cx, cy, ir, fill=1, stroke=0)
        self.canv.setFillColor(C_AIGC)
        try: self.canv.setFont(_font(), 18)
        except: self.canv.setFont('Helvetica', 18)
        self.canv.drawCentredString(cx, cy+2, f'{self._a}%')
        self.canv.setFillColor(C_MUTED)
        try: self.canv.setFont(_font(), 7)
        except: self.canv.setFont('Helvetica', 7)
        self.canv.drawCentredString(cx, cy-12, '\u7591\u4f3cAIGC\u751f\u6210')


class SeverityBar(Flowable):
    """严重度指示条"""
    def __init__(self, severity, w=60*mm, h=4, sb=2, sa=2):
        Flowable.__init__(self)
        self.width = w; self.height = h
        self._s = severity; self.spaceAfter = sa; self.spaceBefore = sb
    def draw(self):
        # 背景灰
        self.canv.setFillColor(C_BG_ALT)
        self.canv.roundRect(0, 0, self.width, self.height, 2, fill=1, stroke=0)
        # 填充
        fill_w = self.width * self._s
        if self._s > 0.7:
            c = C_AIGC
        elif self._s > 0.4:
            c = HexColor('#f59e0b')
        else:
            c = HexColor('#fbbf24')
        self.canv.setFillColor(c)
        self.canv.roundRect(0, 0, fill_w, self.height, 2, fill=1, stroke=0)


# ─── 页码 ───
def _pn(canvas, doc):
    canvas.saveState()
    try:
        canvas.setFont(_font(), 8)
    except Exception:
        canvas.setFont('Helvetica', 8)
    canvas.setFillColor(C_MUTED)
    canvas.drawCentredString(PAGE_W/2, 12*mm, str(canvas.getPageNumber()))
    canvas.restoreState()


# ─── 构建各部分 ───
def _build_cover(story, s, d):
    # 蓝色横幅
    story.append(CoverBanner(CW))
    story.append(Spacer(1, 12*mm))
    story.append(Paragraph('AIGC \u539f\u6587\u5bf9\u7167\u68c0\u6d4b\u62a5\u544a', s['cover_title']))
    story.append(Spacer(1, 4*mm))
    story.append(Paragraph('\u57fa\u4e8e N-gram \u8bed\u8a00\u6a21\u578b\u7684\u5b57\u7b26\u7ea7\u53ef\u9884\u6d4b\u6027\u5206\u6790', s['cover_sub']))
    story.append(Spacer(1, 20*mm))

    no = d.get('report_no') or ''.join(random.choice(string.ascii_lowercase+string.digits) for _ in range(16))
    t = d.get('detect_time') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    story.append(Paragraph(f'NO. {no}    {t}', s['note']))
    story.append(Spacer(1, 10*mm))

    info = [
        [Paragraph('\u8bba\u6587\u9898\u76ee', s['cover_info']),
         Paragraph(d.get('title', ''), s['cover_info_v'])],
        [Paragraph('\u4f5c\u8005', s['cover_info']),
         Paragraph(d.get('author', '') or '\u672a\u586b\u5199', s['cover_info_v'])],
        [Paragraph('\u68c0\u6d4b\u5355\u4f4d', s['cover_info']),
         Paragraph(d.get('institution', '') or '\u672a\u586b\u5199', s['cover_info_v'])],
        [Paragraph('\u8bba\u6587\u89c4\u6a21', s['cover_info']),
         Paragraph(f'\u5b57\u7b26\u6570 {d.get("char_count",0)}  |  \u9875\u6570 {d.get("page_count",0)}  |  '
                   f'\u8868\u683c {d.get("table_count",0)}  |  \u56fe\u7247 {d.get("image_count",0)}',
                   s['cover_info_v'])],
    ]
    tbl = Table(info, colWidths=[22*mm, CW-22*mm])
    tbl.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('LINEBELOW', (0,0), (-1,-2), 0.5, C_BORDER),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 15*mm))
    story.append(Divider(CW, 1.5, C_ACCENT))


def _build_overview(story, s, d):
    story.append(Paragraph('\u4e00\u3001\u68c0\u6d4b\u7ed3\u679c\u6982\u89c8', s['h1']))
    story.append(Divider(CW*0.12, 2, C_ACCENT, sa=10))

    a = d.get('aigc_rate', 0)
    h = d.get('human_rate', round(100-a, 2))

    rd = [[Paragraph(f'{a}%', s['big_num']), Paragraph(f'{h}%', s['big_num_g'])],
          [Paragraph('\u7591\u4f3c AIGC \u751f\u6210\u5360\u6bd4', s['label']),
           Paragraph('\u4eba\u5de5\u64b0\u5199\u5360\u6bd4', s['label'])]]
    rt = Table(rd, colWidths=[CW/2, CW/2])
    rt.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('TOPPADDING', (0,0), (-1,0), 6),
        ('BOTTOMPADDING', (0,1), (-1,1), 6),
    ]))
    story.append(rt)
    story.append(Spacer(1, 4*mm))

    pie = DonutChart(130, 130, a, h)
    pie.hAlign = 'CENTER'
    story.append(pie)
    story.append(Spacer(1, 6*mm))

    # 检测方法说明
    story.append(Paragraph(
        '\u68c0\u6d4b\u65b9\u6cd5\uff1a\u57fa\u4e8e\u4e2d\u6587 N-gram \u8bed\u8a00\u6a21\u578b\uff0c'
        '\u901a\u8fc7\u8ba1\u7b97\u9010\u5b57\u7b26\u7684 trigram log-probability\uff0c'
        '\u5206\u6790\u6587\u672c\u5e8f\u5217\u7684\u53ef\u9884\u6d4b\u6027\u3002'
        'AI \u751f\u6210\u6587\u672c\u7684\u5b57\u7b26\u5e8f\u5217\u66f4\u201c\u53ef\u9884\u6d4b\u201d\u3001\u66f4\u201c\u5747\u5300\u201d\uff0c'
        '\u4eba\u5de5\u6587\u672c\u5219\u5177\u6709\u66f4\u591a\u7684\u201c\u610f\u5916\u9009\u62e9\u201d\u3002',
        s['body_sm']))


def _build_chapters(story, s, d):
    story.append(Spacer(1, 6*mm))
    story.append(Paragraph('\u4e8c\u3001\u7ae0\u8282\u68c0\u6d4b\u660e\u7ec6', s['h1']))
    story.append(Divider(CW*0.12, 2, C_ACCENT, sa=8))

    hdr = [Paragraph('\u5e8f\u53f7', s['th']), Paragraph('\u7ae0\u8282', s['th']),
           Paragraph('\u4eba\u5de5\u64b0\u5199', s['th']),
           Paragraph('\u7591\u4f3c AIGC', s['th'])]
    rows = [hdr]
    for i, ch in enumerate(d.get('chapters', []), 1):
        nm = ch.get('name', '')
        hc = ch.get('human_chars', 0)
        tc = ch.get('total_chars', 0)
        hr = hc/tc*100 if tc > 0 else 0
        ar = 100 - hr
        rows.append([
            Paragraph(str(i), s['tc']),
            Paragraph(nm, s['tcl']),
            Paragraph(f'{hc}/{tc} ({hr:.1f}%)', s['tc']),
            Paragraph(f'<font color="#dc2626">{ar:.1f}%</font>', s['tc']),
        ])

    cw = [12*mm, 50*mm, 40*mm, 30*mm]
    tbl = LongTable(rows, colWidths=cw, repeatRows=1)
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), C_ACCENT),
        ('TEXTCOLOR', (0,0), (-1,0), C_WHITE),
        ('FONTSIZE', (0,0), (-1,0), 9),
        ('FONTSIZE', (0,1), (-1,-1), 9),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,0), 7),
        ('BOTTOMPADDING', (0,0), (-1,0), 7),
        ('TOPPADDING', (0,1), (-1,-1), 5),
        ('BOTTOMPADDING', (0,1), (-1,-1), 5),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [C_BG_LIGHT, C_WHITE]),
        ('GRID', (0,0), (-1,-1), 0.5, C_BORDER),
    ]))
    story.append(tbl)


def _build_highlights(story, s, d):
    """构建词级高亮分析——在完整段落中内联高亮AI片段"""
    ots = d.get('original_text', [])
    if not ots:
        return

    story.append(Spacer(1, 8*mm))
    story.append(Paragraph('\u4e09\u3001\u8bcd\u7ea7\u53ef\u9884\u6d4b\u6027\u5206\u6790', s['h1']))
    story.append(Divider(CW*0.12, 2, C_ACCENT, sa=6))
    story.append(Paragraph(
        '\u4ee5\u4e0b\u5bf9\u6bcf\u6bb5\u6587\u672c\u8fdb\u884c\u591a\u4fe1\u53f7\u878d\u5408\u5206\u6790\uff0c'
        '\u5305\u62ecAI\u8fc7\u6e21\u8bcd\u3001\u89c4\u5219\u5339\u914d\u77ed\u8bed\u3001\u9ad8\u53ef\u9884\u6d4b\u6027\u5b57\u7b26\u6bb5\u7b49\uff0c'
        '\u5728\u539f\u6587\u4e2d\u4ee5\u7ea2\u8272\u80cc\u666f\u6807\u6ce8\u7591\u4f3c AI \u751f\u6210\u7684\u8bcd/\u77ed\u8bed\u3002',
        s['body_sm']))

    # 检测方法提示
    method_info = d.get('method_info', {})
    if method_info.get('note'):
        story.append(Spacer(1, 2*mm))
        note_style = ParagraphStyle('mn', fontName=_font(), fontSize=8.5, leading=13,
            textColor=HexColor('#b45309'), wordWrap='CJK')
        story.append(Paragraph(
            f'<b>\u26a0\ufe0f \u63d0\u793a\uff1a</b>{method_info["note"]}',
            note_style))
    story.append(Spacer(1, 4*mm))

    # 图例
    leg = [[Paragraph(
        '<font color="#dc2626"><b>\u25a0 \u7591\u4f3cAI\u751f\u6210\uff08\u7ea2\u8272\u6807\u6ce8\uff09</b></font>'
        '&nbsp;&nbsp;&nbsp;&nbsp;'
        '<font color="#1e293b">\u672a\u6807\u6ce8 = \u4eba\u5de5\u64b0\u5199</font>',
        s['body_sm'])]]
    lt = Table(leg, colWidths=[CW])
    lt.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('TOPPADDING', (0,0), (-1,-1), 4), ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('BACKGROUND', (0,0), (-1,-1), C_BG_LIGHT),
        ('BOX', (0,0), (-1,-1), 0.5, C_BORDER),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
    ]))
    story.append(lt)
    story.append(Spacer(1, 6*mm))

    for idx, item in enumerate(ots, 1):
        text = item.get('text', '')
        segs = item.get('segments', [])
        score = item.get('score', 0)

        # 段落标题
        score_color = '#dc2626' if score >= 50 else '#059669'
        story.append(Paragraph(
            f'<font color="{score_color}"><b>\u6bb5\u843d {idx}</b></font>'
            f'  <font color="#94a3b8">AI\u7387: {score}/100</font>',
            s['h2']))

        # 将 segments 合并为一个内联高亮的完整段落
        if segs:
            # 构建 HTML：AI片段用红色背景span，普通文本原样
            html_parts = []
            for seg in segs:
                seg_text = seg.get('text', '')
                is_aigc = seg.get('is_aigc', False)
                # 转义HTML特殊字符
                safe = (seg_text
                        .replace('&', '&amp;')
                        .replace('<', '&lt;')
                        .replace('>', '&gt;'))
                if is_aigc:
                    html_parts.append(
                        f'<font backColor="#fef2f2" color="#dc2626"><b>{safe}</b></font>')
                else:
                    html_parts.append(safe)
            full_html = ''.join(html_parts)
            story.append(Paragraph(full_html, s['body']))
        else:
            story.append(Paragraph(text, s['body']))

        story.append(Spacer(1, 4*mm))


def _build_suggestions(story, s, d):
    """构建降低AIGC率的方法建议"""
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph('\u4e09\u3001\u964d\u4f4e AIGC \u7387\u7684\u5efa\u8bae', s['h1']))
    story.append(Divider(CW*0.12, 2, C_ACCENT, sa=8))

    # 收集报告中实际标注的AI模式词
    ai_patterns_found = set()
    for item in d.get('original_text', []):
        for seg in item.get('segments', []):
            if seg.get('is_aigc') and seg.get('reason'):
                # 提取"包含高频AI写作模式词：xxx"中的词
                reason = seg['reason']
                if '\u5305\u542b\u9ad8\u9891AI\u5199\u4f5c\u6a21\u5f0f\u8bcd' in reason:
                    try:
                        part = reason.split('\uff1a')[1]
                        for w in part.split('\u3001'):
                            ai_patterns_found.add(w.strip())
                    except (IndexError, AttributeError):
                        pass

    # 建议条目
    suggestions = []

    # 建议1：替换AI高频模式词
    if ai_patterns_found:
        words_str = '\u3001'.join(list(ai_patterns_found)[:8])
        suggestions.append((
            '\u66ff\u6362 AI \u9ad8\u9891\u6a21\u5f0f\u8bcd',
            f'\u672c\u6b21\u68c0\u6d4b\u4e2d\u53d1\u73b0\u4ee5\u4e0b\u9ad8\u9891 AI \u5199\u4f5c\u6a21\u5f0f\u8bcd\uff1a{words_str}\u3002'
            '\u8fd9\u4e9b\u8bcd\u5728 AI \u751f\u6210\u6587\u672c\u4e2d\u51fa\u73b0\u9891\u7387\u8fdc\u9ad8\u4e8e\u4eba\u5de5\u5199\u4f5c\uff0c'
            '\u5efa\u8bae\u7528\u66f4\u5177\u4f53\u3001\u66f4\u4e2a\u6027\u5316\u7684\u8868\u8fbe\u66ff\u6362\u3002'
            '\u4f8b\u5982\uff1a\u201c\u8be5\u7cfb\u7edf\u201d\u2192\u201c\u672c\u7814\u7a76\u6240\u5f00\u53d1\u7684\u7cfb\u7edf\u201d\uff0c'
            '\u201c\u5305\u542b\u201d\u2192\u201c\u6db5\u76d6\u4e86 / \u5177\u5907\u4e86\u201d\u3002'
        ))

    # 建议2：打破均匀句式
    suggestions.append((
        '\u6253\u7834\u5747\u5300\u53e5\u5f0f',
        'AI \u751f\u6210\u6587\u672c\u7684\u53e5\u5b50\u957f\u5ea6\u5f80\u5f80\u975e\u5e38\u5747\u5300\uff0c'
        '\u8fd9\u662f\u88ab\u68c0\u6d4b\u7684\u91cd\u8981\u7279\u5f81\u3002'
        '\u5efa\u8bae\u6df7\u5408\u4f7f\u7528\u957f\u77ed\u53e5\uff0c\u6dfb\u52a0\u4e00\u4e9b\u7b80\u77ed\u7684\u8865\u5145\u8bf4\u660e\u6216'
        '\u53cd\u95ee\u53e5\uff0c\u8ba9\u53e5\u957f\u5206\u5e03\u66f4\u81ea\u7136\u3002'
        '\u4f8b\u5982\uff1a\u5728\u8fde\u7eed\u51e0\u4e2a\u957f\u53e5\u540e\u63d2\u5165\u201c\u8fd9\u4e00\u70b9\u5f88\u5173\u952e\u201d'
        '\u6216\u201c\u4e3a\u4ec0\u4e48\uff1f\u56e0\u4e3a\u2026\u2026\u201d\u8fd9\u7c7b\u77ed\u53e5\u3002'
    ))

    # 建议3：增加具体细节和数据
    suggestions.append((
        '\u589e\u52a0\u5177\u4f53\u7ec6\u8282\u548c\u6570\u636e',
        'AI \u6587\u672c\u503e\u5411\u4e8e\u7b3c\u7edf\u6982\u62ec\uff0c\u7f3a\u4e4f\u5177\u4f53\u7684\u6570\u636e\u548c\u5b9e\u4f8b\u3002'
        '\u5efa\u8bae\u5728\u5173\u952e\u8bba\u70b9\u540e\u8865\u5145\u5b9e\u9645\u6570\u636e\u3001\u5177\u4f53\u6848\u4f8b\u6216'
        '\u4e2a\u4eba\u89c2\u5bdf\u3002'
        '\u4f8b\u5982\uff1a\u201c\u7cfb\u7edf\u6027\u80fd\u826f\u597d\u201d\u2192'
        '\u201c\u5728 1000 \u6761\u6d4b\u8bd5\u8bc4\u8bba\u4e0a\uff0c\u7cfb\u7edf\u7684\u60c5\u611f\u5206\u7c7b\u51c6\u786e\u7387\u8fbe\u5230\u4e86 87.3%\u201d\u3002'
    ))

    # 建议4：加入个人化表达
    suggestions.append((
        '\u52a0\u5165\u4e2a\u4eba\u5316\u8868\u8fbe',
        'AI \u6587\u672c\u7684\u7528\u8bcd\u5f80\u5f80\u201c\u592a\u6b63\u786e\u201d\uff0c\u7f3a\u4e4f\u4eba\u5de5\u5199\u4f5c\u4e2d'
        '\u5e38\u89c1\u7684\u53e3\u8bed\u5316\u3001\u4e2a\u6027\u5316\u8868\u8fbe\u3002'
        '\u5efa\u8bae\u9002\u5f53\u52a0\u5165\u4e00\u4e9b\u81ea\u7136\u7684\u8fc7\u6e21\u8bcd\uff08\u5982\u201c\u5176\u5b9e\u201d'
        '\u201c\u8bdd\u8bf4\u56de\u6765\u201d\u201c\u5766\u767d\u8bb2\u201d\uff09\uff0c'
        '\u6216\u7528\u66f4\u63a5\u8fd1\u53e3\u8bed\u7684\u65b9\u5f0f\u91cd\u65b0\u8868\u8ff0\u90e8\u5206\u5185\u5bb9\u3002'
    ))

    # 建议5：调整段落结构
    suggestions.append((
        '\u8c03\u6574\u6bb5\u843d\u7ed3\u6784',
        'AI \u751f\u6210\u7684\u6bb5\u843d\u7ed3\u6784\u5f80\u5f80\u5f88\u6a21\u5f0f\u5316\uff1a'
        '\u201c\u603b\u8ff0\u2192\u5206\u70b9\u2192\u603b\u7ed3\u201d\u3002'
        '\u5efa\u8bae\u6253\u7834\u8fd9\u79cd\u56fa\u5b9a\u6a21\u5f0f\uff0c\u53ef\u4ee5\u7528'
        '\u201c\u95ee\u9898\u2192\u5206\u6790\u2192\u89e3\u51b3\u201d\u6216'
        '\u201c\u73b0\u8c61\u2192\u539f\u56e0\u2192\u5f71\u54cd\u201d\u7b49\u66f4\u591a\u53d8\u4f53\u3002'
    ))

    # 建议6：人工审校和润色
    suggestions.append((
        '\u4eba\u5de5\u5ba1\u6821\u548c\u6da6\u8272',
        '\u6700\u7ec8\u5efa\u8bae\uff1a\u5b8c\u6210\u4ee5\u4e0a\u4fee\u6539\u540e\uff0c\u901a\u8bfb\u5168\u6587\uff0c'
        '\u786e\u4fdd\u8bed\u8a00\u6d41\u7545\u81ea\u7136\u3001\u524d\u540e\u903b\u8f91\u4e00\u81f4\u3002'
        '\u53ef\u4ee5\u8bf7\u540c\u5b66\u6216\u5bfc\u5e08\u5e2e\u5fd9\u5ba1\u8bfb\uff0c'
        '\u4eba\u5de5\u5ba1\u6821\u662f\u964d\u4f4e AIGC \u7387\u6700\u6709\u6548\u7684\u65b9\u6cd5\u3002'
    ))

    # 渲染建议列表
    tip_style = ParagraphStyle('tip', fontName=_font(), fontSize=10, leading=16,
        textColor=C_TEXT, wordWrap='CJK')
    tip_title = ParagraphStyle('tipt', fontName=_font(), fontSize=10.5, leading=16,
        textColor=C_PRIMARY, spaceBefore=8, spaceAfter=2, wordWrap='CJK')

    for i, (title, content) in enumerate(suggestions, 1):
        # 建议卡片
        inner = [
            [Paragraph(f'<b>{i}. {title}</b>', tip_title)],
            [Paragraph(content, tip_style)],
        ]
        td = Table(inner, colWidths=[CW - 8*mm])
        td.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), C_BG_LIGHT if i % 2 == 0 else C_WHITE),
            ('TOPPADDING', (0,0), (-1,0), 6),
            ('BOTTOMPADDING', (0,-1), (-1,-1), 6),
            ('LEFTPADDING', (0,0), (-1,-1), 10),
            ('RIGHTPADDING', (0,0), (-1,-1), 10),
            ('BOX', (0,0), (-1,-1), 0.3, C_BORDER),
        ]))
        story.append(td)
        story.append(Spacer(1, 2*mm))


def _build_footer(story, s):
    story.append(Spacer(1, 10*mm))
    story.append(Divider(CW, 1, C_BORDER, sa=6))
    story.append(Paragraph(
        '\u987b\u77e5\uff1a\u672c\u62a5\u544a\u57fa\u4e8e N-gram \u8bed\u8a00\u6a21\u578b\u7684\u5b57\u7b26\u7ea7\u53ef\u9884\u6d4b\u6027\u5206\u6790\uff0c'
        '\u7ed3\u679c\u4ec5\u4f9b\u53c2\u8003\u3002\u62a5\u544a\u7f16\u53f7\u7cfb\u672c\u62a5\u544a\u7684\u552f\u4e00\u7f16\u53f7\u3002',
        s['note']))


# ─── 主入口 ───
def build_report_pdf(data, output_path=None):
    from loguru import logger
    try:
        return _do_build_report_pdf(data, output_path)
    except Exception:
        logger.exception("build_report_pdf 失败")
        raise


def _do_build_report_pdf(data, output_path=None):
    """根据检测数据生成 PDF 报告。

    data 格式:
    {
        "title": "论文题目",
        "author": "作者",
        "institution": "学校",
        "char_count": 38850, "page_count": 89,
        "table_count": 16, "image_count": 22,
        "aigc_rate": 9.31, "human_rate": 90.69,
        "chapters": [
            {"name": "摘要", "human_chars": 0, "total_chars": 346},
        ],
        "original_text": [
            {
                "text": "段落全文...",
                "score": 75,
                "segments": [
                    {"text": "普通文本", "is_aigc": false, "reason": null, "severity": 0},
                    {"text": "AI片段", "is_aigc": true,
                     "reason": "字符序列可预测性极高（偏离均值1.5σ）", "severity": 0.8},
                ]
            },
        ],
    }
    """
    _font()
    s = _S()

    a = data.get('aigc_rate', 0)
    h = data.get('human_rate', 0)
    if h == 0 and a > 0: data['human_rate'] = round(100-a, 2)
    elif a == 0 and h > 0: data['aigc_rate'] = round(100-h, 2)

    buf = io.BytesIO() if output_path is None else None
    doc = SimpleDocTemplate(
        output_path or buf, pagesize=A4,
        leftMargin=25*mm, rightMargin=25*mm,
        topMargin=20*mm, bottomMargin=20*mm,
    )

    story = []
    _build_cover(story, s, data)
    _build_overview(story, s, data)
    _build_chapters(story, s, data)
    _build_suggestions(story, s, data)
    _build_footer(story, s)

    doc.build(story, onFirstPage=_pn, onLaterPages=_pn)
    return buf.getvalue() if buf else output_path
