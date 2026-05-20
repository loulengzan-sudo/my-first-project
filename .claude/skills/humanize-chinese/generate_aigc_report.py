#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AIGC检测报告生成工具
仿照AIGC原文对照报告模板格式，生成PDF报告。

使用方法：
1. 修改下方 CONFIG 字典中的配置信息
2. 运行脚本：python3 generate_aigc_report.py
3. 输出文件：output_aigc_report.pdf
"""

import os
import platform
import random
import string
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, Color, white, black
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, KeepTogether, LongTable, Flowable
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, Rect, String, Wedge, Line
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics import renderPDF

# ============================================================
#  配置区域 - 请根据实际检测结果修改以下内容
# ============================================================

CONFIG = {
    # ---- 基本信息 ----
    "report_no": "",           # 报告编号（留空自动生成）
    "detect_time": "",         # 检测时间（留空自动使用当前时间）
    "title": "请填写论文题目",  # 论文题目
    "author": "请填写作者姓名", # 作者
    "institution": "请填写学校/单位名称",  # 检测所属单位

    # ---- 论文统计 ----
    "char_count": 0,           # 论文字符数
    "page_count": 0,           # 论文页数
    "table_count": 0,          # 表格数量
    "image_count": 0,          # 图片数量

    # ---- 检测结果 ----
    "aigc_rate": 0.0,          # 疑似AIGC生成占比（百分比，如 9.31）
    "human_rate": 0.0,         # 人工撰写占比（百分比，如 90.69）

    # ---- 各章节检测结果 ----
    # 每项: (章节名, 人工撰写字数, 章节总字数)
    "chapters": [
        ("摘要", 0, 0),
        ("Abstract", 0, 0),
        ("1 绪论", 0, 0),
        ("2 相关理论及主要技术", 0, 0),
        ("3 需求分析", 0, 0),
        ("4 系统设计", 0, 0),
        ("5 数据采集", 0, 0),
        ("6 系统实现", 0, 0),
        ("7 系统测试", 0, 0),
        ("8 总结与展望", 0, 0),
    ],

    # ---- 原文内容（用于原文对照部分）----
    # 每项: (文本内容, 是否为疑似AIGC生成 True/False)
    "original_text": [
        ("请填写论文原文内容...", False),
    ],

    # ---- 输出文件名 ----
    "output_file": "output_aigc_report.pdf",
}

# ============================================================
#  以下为脚本逻辑，一般不需要修改
# ============================================================

# 颜色定义
PRIMARY_COLOR = HexColor('#1a365d')
ACCENT_COLOR = HexColor('#2b6cb0')
AIGC_COLOR = HexColor('#e53e3e')       # 疑似AIGC - 红色
HUMAN_COLOR = HexColor('#38a169')      # 人工撰写 - 绿色
LIGHT_GRAY = HexColor('#f7fafc')
MEDIUM_GRAY = HexColor('#e2e8f0')
DARK_TEXT = HexColor('#2d3748')
SUBTITLE_COLOR = HexColor('#4a5568')
AIGC_BG = HexColor('#fff5f5')          # AIGC标注背景
HUMAN_BG = HexColor('#f0fff4')         # 人工撰写标注背景
AIGC_TEXT_COLOR = HexColor('#c53030')
HUMAN_TEXT_COLOR = HexColor('#276749')

PAGE_WIDTH, PAGE_HEIGHT = A4
CONTENT_WIDTH = PAGE_WIDTH - 50 * mm  # 左右边距各25mm


def register_cjk_font():
    """注册中文字体"""
    system = platform.system()
    font_paths = []
    if system == "Darwin":
        font_paths = [
            "/System/Library/Fonts/PingFang.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
            "/System/Library/Fonts/STHeiti Medium.ttc",
        ]
    elif system == "Windows":
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simsun.ttc",
        ]
    else:
        font_paths = [
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        ]
    for fp in font_paths:
        if os.path.exists(fp):
            pdfmetrics.registerFont(TTFont("CJKFont", fp, subfontIndex=0))
            return "CJKFont"
    raise RuntimeError("未找到可用的中文字体，请安装 Noto Sans CJK 或 WenQuanYi 字体")


def get_styles(cjk_font='CJKFont'):
    """获取专业样式"""
    return {
        'report_title': ParagraphStyle(
            'ReportTitle', fontName=cjk_font, fontSize=18, leading=24,
            textColor=PRIMARY_COLOR, alignment=TA_CENTER, spaceAfter=4,
            wordWrap='CJK'
        ),
        'report_no': ParagraphStyle(
            'ReportNo', fontName=cjk_font, fontSize=9, leading=13,
            textColor=SUBTITLE_COLOR, alignment=TA_LEFT, spaceAfter=2,
            wordWrap='CJK'
        ),
        'info_label': ParagraphStyle(
            'InfoLabel', fontName=cjk_font, fontSize=10, leading=15,
            textColor=DARK_TEXT, alignment=TA_LEFT, wordWrap='CJK'
        ),
        'info_value': ParagraphStyle(
            'InfoValue', fontName=cjk_font, fontSize=10, leading=15,
            textColor=PRIMARY_COLOR, alignment=TA_LEFT, wordWrap='CJK'
        ),
        'section_title': ParagraphStyle(
            'SectionTitle', fontName=cjk_font, fontSize=14, leading=20,
            textColor=PRIMARY_COLOR, spaceBefore=16, spaceAfter=10,
            wordWrap='CJK'
        ),
        'rate_big': ParagraphStyle(
            'RateBig', fontName=cjk_font, fontSize=36, leading=42,
            textColor=AIGC_COLOR, alignment=TA_CENTER, wordWrap='CJK'
        ),
        'rate_label': ParagraphStyle(
            'RateLabel', fontName=cjk_font, fontSize=10, leading=14,
            textColor=SUBTITLE_COLOR, alignment=TA_CENTER, spaceAfter=4,
            wordWrap='CJK'
        ),
        'rate_green': ParagraphStyle(
            'RateGreen', fontName=cjk_font, fontSize=36, leading=42,
            textColor=HUMAN_COLOR, alignment=TA_CENTER, wordWrap='CJK'
        ),
        'table_header': ParagraphStyle(
            'TableHeader', fontName=cjk_font, fontSize=9, leading=13,
            textColor=white, alignment=TA_CENTER, wordWrap='CJK'
        ),
        'table_cell': ParagraphStyle(
            'TableCell', fontName=cjk_font, fontSize=9, leading=13,
            textColor=DARK_TEXT, alignment=TA_CENTER, wordWrap='CJK'
        ),
        'table_cell_left': ParagraphStyle(
            'TableCellLeft', fontName=cjk_font, fontSize=9, leading=13,
            textColor=DARK_TEXT, alignment=TA_LEFT, wordWrap='CJK'
        ),
        'body': ParagraphStyle(
            'Body', fontName=cjk_font, fontSize=10.5, leading=18,
            textColor=DARK_TEXT, spaceBefore=0, spaceAfter=6,
            firstLineIndent=21, wordWrap='CJK'
        ),
        'body_no_indent': ParagraphStyle(
            'BodyNoIndent', fontName=cjk_font, fontSize=10.5, leading=18,
            textColor=DARK_TEXT, spaceBefore=0, spaceAfter=6,
            wordWrap='CJK'
        ),
        'note': ParagraphStyle(
            'Note', fontName=cjk_font, fontSize=8, leading=12,
            textColor=SUBTITLE_COLOR, alignment=TA_LEFT, wordWrap='CJK'
        ),
        'legend_title': ParagraphStyle(
            'LegendTitle', fontName=cjk_font, fontSize=11, leading=16,
            textColor=PRIMARY_COLOR, spaceBefore=12, spaceAfter=6,
            wordWrap='CJK'
        ),
        'legend_item': ParagraphStyle(
            'LegendItem', fontName=cjk_font, fontSize=9, leading=13,
            textColor=DARK_TEXT, alignment=TA_LEFT, wordWrap='CJK'
        ),
        'footer': ParagraphStyle(
            'Footer', fontName=cjk_font, fontSize=8, leading=12,
            textColor=SUBTITLE_COLOR, alignment=TA_CENTER, wordWrap='CJK'
        ),
        'aigc_text': ParagraphStyle(
            'AIGCText', fontName=cjk_font, fontSize=10.5, leading=18,
            textColor=AIGC_TEXT_COLOR, spaceBefore=0, spaceAfter=6,
            firstLineIndent=21, wordWrap='CJK'
        ),
        'human_text': ParagraphStyle(
            'HumanText', fontName=cjk_font, fontSize=10.5, leading=18,
            textColor=HUMAN_TEXT_COLOR, spaceBefore=0, spaceAfter=6,
            firstLineIndent=21, wordWrap='CJK'
        ),
    }


class ColoredDivider(Flowable):
    """彩色分割线"""
    def __init__(self, width, height=2, color=ACCENT_COLOR, space_before=6, space_after=12):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self.color = color
        self.spaceAfter = space_after
        self.spaceBefore = space_before

    def draw(self):
        self.canv.setFillColor(self.color)
        self.canv.rect(0, 0, self.width, self.height, fill=1, stroke=0)


class PieChartDrawing(Flowable):
    """饼图"""
    def __init__(self, width, height, aigc_rate, human_rate):
        Flowable.__init__(self)
        self.width = width
        self.height = height
        self._aigc_rate = aigc_rate
        self._human_rate = human_rate

    def draw(self):
        cx, cy = self.width / 2, self.height / 2
        r = min(cx, cy) - 10

        aigc_angle = self._aigc_rate / 100.0 * 360
        human_angle = self._human_rate / 100.0 * 360

        # 人工撰写（绿色）- 从顶部开始
        self.canv.setFillColor(HUMAN_COLOR)
        self.canv.setStrokeColor(white)
        self.canv.setLineWidth(2)
        self.canv.wedge(cx - r, cy - r, cx + r, cy + r,
                        90, -human_angle, fill=1, stroke=1)

        # AIGC（红色）
        self.canv.setFillColor(AIGC_COLOR)
        self.canv.setStrokeColor(white)
        self.canv.setLineWidth(2)
        self.canv.wedge(cx - r, cy - r, cx + r, cy + r,
                        90 - human_angle, -aigc_angle, fill=1, stroke=1)

        # 中心白圆（环形图效果）
        inner_r = r * 0.55
        self.canv.setFillColor(white)
        self.canv.circle(cx, cy, inner_r, fill=1, stroke=0)

        # 中心文字
        self.canv.setFillColor(AIGC_COLOR)
        self.canv.setFont("CJKFont", 20)
        self.canv.drawCentredString(cx, cy + 4, f"{self._aigc_rate}%")
        self.canv.setFillColor(SUBTITLE_COLOR)
        self.canv.setFont("CJKFont", 8)
        self.canv.drawCentredString(cx, cy - 14, "疑似AIGC生成")


class BarIndicator(Flowable):
    """水平条形指示器"""
    def __init__(self, width, aigc_rate, human_rate, space_before=8, space_after=8):
        Flowable.__init__(self)
        self.width = width
        self.height = 30
        self.aigc_rate = aigc_rate
        self.human_rate = human_rate
        self.spaceAfter = space_after
        self.spaceBefore = space_before

    def draw(self):
        bar_height = 16
        y = (self.height - bar_height) / 2
        total_w = self.width

        # 背景
        self.canv.setFillColor(MEDIUM_GRAY)
        self.canv.roundRect(0, y, total_w, bar_height, 4, fill=1, stroke=0)

        # 人工撰写部分（绿色）
        human_w = total_w * self.human_rate / 100.0
        if human_w > 0:
            self.canv.setFillColor(HUMAN_COLOR)
            self.canv.roundRect(0, y, human_w, bar_height, 4, fill=1, stroke=0)

        # AIGC部分（红色）
        aigc_w = total_w * self.aigc_rate / 100.0
        if aigc_w > 0:
            self.canv.setFillColor(AIGC_COLOR)
            self.canv.roundRect(human_w, y, aigc_w, bar_height, 4, fill=1, stroke=0)


def generate_report_no():
    """生成随机报告编号"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(16))


def build_cover_page(story, styles, config):
    """构建封面页"""
    story.append(Spacer(1, 30 * mm))

    # 报告标题
    story.append(Paragraph("原文对照报告（PDF）· 通用版", styles['report_title']))
    story.append(Spacer(1, 6 * mm))

    # 报告编号和检测时间
    report_no = config["report_no"] or generate_report_no()
    detect_time = config["detect_time"] or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    story.append(Paragraph(
        f"NO. {report_no}  {detect_time}",
        styles['report_no']
    ))
    story.append(Spacer(1, 10 * mm))

    # 信息表格
    info_data = [
        [Paragraph("题目：", styles['info_label']),
         Paragraph(config["title"], styles['info_value'])],
        [Paragraph("作者：", styles['info_label']),
         Paragraph(config["author"], styles['info_value'])],
        [Paragraph("检测所属单位：", styles['info_label']),
         Paragraph(config["institution"], styles['info_value'])],
        [Paragraph("论文字符数：", styles['info_label']),
         Paragraph(
             f"{config['char_count']}  论文页数：{config['page_count']}  "
             f"表格数量：{config['table_count']}  图片数量：{config['image_count']}",
             styles['info_value']
         )],
    ]

    info_table = Table(info_data, colWidths=[35 * mm, CONTENT_WIDTH - 35 * mm])
    info_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, MEDIUM_GRAY),
    ]))
    story.append(info_table)
    story.append(Spacer(1, 12 * mm))

    # 分割线
    story.append(ColoredDivider(CONTENT_WIDTH, height=2, color=ACCENT_COLOR))

    return report_no, detect_time


def build_detection_result(story, styles, config):
    """构建检测结果部分"""
    story.append(Paragraph("检测结果", styles['section_title']))
    story.append(ColoredDivider(CONTENT_WIDTH * 0.15, height=2, color=ACCENT_COLOR, space_after=8))

    aigc_rate = config["aigc_rate"]
    human_rate = config["human_rate"]

    # 大数字展示
    result_data = [[
        Paragraph(f"{aigc_rate}%", styles['rate_big']),
        Paragraph(f"{human_rate}%", styles['rate_green']),
    ], [
        Paragraph("疑似AIGC生成占比", styles['rate_label']),
        Paragraph("人工撰写占比", styles['rate_label']),
    ]]

    result_table = Table(result_data, colWidths=[CONTENT_WIDTH / 2, CONTENT_WIDTH / 2])
    result_table.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 1), (-1, 1), 8),
    ]))
    story.append(result_table)
    story.append(Spacer(1, 6 * mm))

    # 进度条
    story.append(BarIndicator(CONTENT_WIDTH, aigc_rate, human_rate))
    story.append(Spacer(1, 6 * mm))

    # 饼图
    pie = PieChartDrawing(160, 160, aigc_rate, human_rate)
    pie.hAlign = 'CENTER'
    story.append(pie)


def build_chapter_table(story, styles, config):
    """构建章节检测结果表格"""
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("结果分布", styles['section_title']))
    story.append(ColoredDivider(CONTENT_WIDTH * 0.15, height=2, color=ACCENT_COLOR, space_after=8))

    # 表头
    header = [
        Paragraph("序号", styles['table_header']),
        Paragraph("章节", styles['table_header']),
        Paragraph("人工撰写<br/>文字/章节总字数", styles['table_header']),
        Paragraph("人工撰写<br/>章节占比", styles['table_header']),
        Paragraph("疑似AIGC生成<br/>章节占比", styles['table_header']),
    ]

    data = [header]
    for i, (name, human_chars, total_chars) in enumerate(config["chapters"], 1):
        if total_chars > 0:
            h_rate = human_chars / total_chars * 100
            a_rate = 100 - h_rate
        else:
            h_rate = 0
            a_rate = 0

        row = [
            Paragraph(str(i), styles['table_cell']),
            Paragraph(name, styles['table_cell_left']),
            Paragraph(f"{human_chars}/{total_chars}", styles['table_cell']),
            Paragraph(f"{h_rate:.1f}%", styles['table_cell']),
            Paragraph(f"<font color='#e53e3e'>{a_rate:.1f}%</font>", styles['table_cell']),
        ]
        data.append(row)

    col_widths = [12 * mm, 45 * mm, 35 * mm, 25 * mm, 25 * mm]
    chapter_table = LongTable(data, colWidths=col_widths, repeatRows=1)
    chapter_table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), ACCENT_COLOR),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, -1), 'CJKFont'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 9),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 1), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT_GRAY, white]),
        ('GRID', (0, 0), (-1, -1), 0.5, MEDIUM_GRAY),
    ]))
    story.append(chapter_table)

    # 注释
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(
        "*注:格式规范的情况下可准确识别章节，若论文中无章节，可能会识别有误。",
        styles['note']
    ))


def build_legend(story, styles):
    """构建图例说明"""
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("片段分布", styles['section_title']))
    story.append(ColoredDivider(CONTENT_WIDTH * 0.15, height=2, color=ACCENT_COLOR, space_after=8))

    # 图例
    legend_data = [
        [Paragraph('<font color="#e53e3e">■</font> 疑似AIGC生成', styles['legend_item']),
         Paragraph('<font color="#38a169">■</font> 人工撰写', styles['legend_item'])],
    ]
    legend_table = Table(legend_data, colWidths=[CONTENT_WIDTH / 2, CONTENT_WIDTH / 2])
    legend_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 0.5, MEDIUM_GRAY),
    ]))
    story.append(legend_table)


def build_original_text(story, styles, config):
    """构建原文对照部分"""
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("文字标注", styles['section_title']))
    story.append(ColoredDivider(CONTENT_WIDTH * 0.15, height=2, color=ACCENT_COLOR, space_after=8))

    # 图例
    legend_data = [
        [Paragraph('<font color="#c53030"><b>疑似AIGC生成</b></font>', styles['legend_item']),
         Paragraph('<font color="#276749"><b>人工撰写</b></font>', styles['legend_item'])],
    ]
    legend_table = Table(legend_data, colWidths=[CONTENT_WIDTH / 2, CONTENT_WIDTH / 2])
    legend_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, 0), (-1, -1), LIGHT_GRAY),
        ('BOX', (0, 0), (-1, -1), 0.5, MEDIUM_GRAY),
    ]))
    story.append(legend_table)
    story.append(Spacer(1, 6 * mm))

    # 原文内容
    for text, is_aigc in config["original_text"]:
        if is_aigc:
            # AIGC内容 - 红色背景 + 红色文字
            bg_data = [[Paragraph(text, styles['aigc_text'])]]
            bg_table = Table(bg_data, colWidths=[CONTENT_WIDTH - 4 * mm])
            bg_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), AIGC_BG),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('BOX', (0, 0), (-1, -1), 0.5, HexColor('#feb2b2')),
            ]))
            story.append(bg_table)
            story.append(Spacer(1, 2 * mm))
        else:
            # 人工撰写 - 绿色背景 + 绿色文字
            bg_data = [[Paragraph(text, styles['human_text'])]]
            bg_table = Table(bg_data, colWidths=[CONTENT_WIDTH - 4 * mm])
            bg_table.setStyle(TableStyle([
                ('BACKGROUND', (0, 0), (-1, -1), HUMAN_BG),
                ('TOPPADDING', (0, 0), (-1, -1), 4),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
                ('LEFTPADDING', (0, 0), (-1, -1), 6),
                ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                ('BOX', (0, 0), (-1, -1), 0.5, HexColor('#9ae6b4')),
            ]))
            story.append(bg_table)
            story.append(Spacer(1, 2 * mm))


def build_footer(story, styles):
    """构建尾部说明"""
    story.append(Spacer(1, 12 * mm))
    story.append(ColoredDivider(CONTENT_WIDTH, height=1, color=MEDIUM_GRAY, space_after=8))

    notices = [
        "须知：",
        "报告编号系送检论文检测报告在本系统中的唯一编号。",
        "本报告结合AI大模型自动生成，结果仅供参考。",
        "报告支持中、英文内容检测。",
    ]
    for notice in notices:
        story.append(Paragraph(notice, styles['note']))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("微信公众号", styles['footer']))
    story.append(Paragraph(
        "唯一官网：https://vpcs.fanyu.com  客服邮箱：vpcs@fanyu.com  客服热线：400-607-5550  客服QQ：4006075550",
        styles['footer']
    ))


def add_page_number(canvas, doc):
    """添加页码"""
    canvas.saveState()
    canvas.setFont("CJKFont", 9)
    canvas.setFillColor(SUBTITLE_COLOR)
    page_num = canvas.getPageNumber()
    text = f"{page_num}"
    canvas.drawCentredString(PAGE_WIDTH / 2, 15 * mm, text)
    canvas.restoreState()


def generate_report(config=None):
    """生成AIGC检测报告"""
    if config is None:
        config = CONFIG

    # 注册字体
    cjk_font = register_cjk_font()
    styles = get_styles(cjk_font)

    # 自动计算比率
    if config["human_rate"] == 0 and config["aigc_rate"] > 0:
        config["human_rate"] = round(100 - config["aigc_rate"], 2)
    elif config["aigc_rate"] == 0 and config["human_rate"] > 0:
        config["aigc_rate"] = round(100 - config["human_rate"], 2)

    # 创建文档
    output_path = config.get("output_file", "output_aigc_report.pdf")
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm,
    )

    story = []

    # 1. 封面信息
    build_cover_page(story, styles, config)

    # 2. 检测结果
    build_detection_result(story, styles, config)

    # 3. 章节表格
    build_chapter_table(story, styles, config)

    # 4. 片段分布图例
    build_legend(story, styles)

    # 5. 原文对照
    if config.get("original_text"):
        build_original_text(story, styles, config)

    # 6. 尾部说明
    build_footer(story, styles)

    # 构建PDF
    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    print(f"✅ 报告已生成: {output_path}")
    return output_path


