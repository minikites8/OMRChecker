"""生成第十六届软件方向二面试题 A/B/C 卷定位标记答题卡。"""
from __future__ import annotations

import argparse
import hashlib
import fitz
import json
import math
import shutil
from pathlib import Path
from typing import Iterable

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

ROOT = Path(__file__).resolve().parent
OUTPUT_DIR = ROOT / "output" / "pdf" / "exam_16th_answer_card_unified_2026"
MARKER_PATH = ROOT / "output" / "pdf" / "exam_answer_cards" / "marker_version" / "omr_marker.jpg"
REFERENCE_15TH_PDF = ROOT / "output" / "pdf" / "exam_answer_cards" / "marker_version" / "15th软件方向二面试题A卷答题卡_定位标记版.pdf"
PAGE_W, PAGE_H = A4
MARGIN = 42.0
MARKER_INSET = 25.0
MARKER_SIZE = 18.0
DARK = HexColor("#18383c")
ACCENT = HexColor("#2f6f73")
LIGHT = HexColor("#edf5f4")
GRID = HexColor("#81999a")
MUTED = HexColor("#53686b")


def register_fonts():
    try:
        pdfmetrics.getFont("STSong-Light")
    except KeyError:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))


def font_for(text: str, bold: bool = False) -> str:
    if any("\u2e80" <= ch <= "\u9fff" for ch in str(text)):
        return "STSong-Light"
    return "Helvetica-Bold" if bold else "Helvetica"


def text(c: canvas.Canvas, value: str, x: float, y: float, size: float = 9,
         color=black, bold: bool = False, align: str = "left"):
    c.setFillColor(color)
    c.setFont(font_for(value, bold), size)
    if align == "center":
        c.drawCentredString(x, y, value)
    elif align == "right":
        c.drawRightString(x, y, value)
    else:
        c.drawString(x, y, value)


def fit_text(c: canvas.Canvas, value: str, x: float, y: float, max_width: float,
             size: float = 9, min_size: float = 6.5, color=black, bold: bool = False):
    current = size
    font = font_for(value, bold)
    while current > min_size and pdfmetrics.stringWidth(value, font, current) > max_width:
        current -= 0.25
    text(c, value, x, y, current, color=color, bold=bold)


def wrap_text(value: str, max_chars: int) -> list[str]:
    value = str(value)
    return [value[i:i + max_chars] for i in range(0, len(value), max_chars)] or [""]


def draw_marker(c: canvas.Canvas, x: float, y: float, marker: ImageReader):
    c.drawImage(marker, x, y, width=MARKER_SIZE, height=MARKER_SIZE, preserveAspectRatio=True, mask="auto")


def draw_markers(c: canvas.Canvas, page_no: int, total_pages: int, marker: ImageReader):
    for x in (MARKER_INSET, PAGE_W - MARKER_INSET - MARKER_SIZE):
        for y in (PAGE_H - MARKER_INSET - MARKER_SIZE, MARKER_INSET):
            draw_marker(c, x, y, marker)
    # 两侧时序标记，手机拍照与透视校正更稳定。
    top = 84.0
    bottom = PAGE_H - 84.0
    count = 29
    c.setFillColor(black)
    for i in range(count):
        y = top + (bottom - top) * i / (count - 1)
        for x in (MARKER_INSET + 7.0, PAGE_W - MARKER_INSET - 7.0):
            c.rect(x - 4.5, y, 9.0, 3.2, stroke=0, fill=1)
    text(c, f"第 {page_no} 页 / 共 {total_pages} 页", PAGE_W - 48, 30, 7.5, color=MUTED, align="right")


def draw_title(c: canvas.Canvas, version: str, page_label: str):
    text(c, f"华南农业大学第十六届学生科技创新与创业联合会", PAGE_W / 2, PAGE_H - 38, 12, color=DARK, bold=True, align="center")
    text(c, f"自然科学部第二轮面试答题卡  {version}卷", PAGE_W / 2, PAGE_H - 56, 15, color=DARK, bold=True, align="center")
    text(c, page_label, PAGE_W / 2, PAGE_H - 73, 8.5, color=MUTED, align="center")


def draw_rule(c: canvas.Canvas, x1: float, y: float, x2: float, color=GRID, width=0.5):
    c.setStrokeColor(color)
    c.setLineWidth(width)
    c.line(x1, y, x2, y)


def draw_section(c: canvas.Canvas, label: str, y: float, note: str = ""):
    c.setFillColor(LIGHT)
    c.roundRect(MARGIN, y - 4, PAGE_W - 2 * MARGIN, 20, 4, stroke=0, fill=1)
    text(c, label, MARGIN + 8, y + 3, 10, color=DARK, bold=True)
    if note:
        text(c, note, PAGE_W - MARGIN - 8, y + 3, 7.5, color=MUTED, align="right")


def draw_bubble(c: canvas.Canvas, x: float, y: float, label: str, radius: float = 6.0, font_size: float = 7.2):
    c.setStrokeColor(DARK)
    c.setLineWidth(0.75)
    c.circle(x, y, radius, stroke=1, fill=0)
    text(c, label, x, y - 2.5, font_size, color=DARK, align="center")


def draw_identity(c: canvas.Canvas):
    text(c, "姓名", 48, 734, 9.5, color=DARK, bold=True)
    draw_rule(c, 80, 732, 245, color=DARK, width=0.8)
    text(c, "试卷类型", 300, 734, 9.5, color=DARK, bold=True)
    for index, option in enumerate("ABC"):
        draw_bubble(c, 370 + index * 42, 734, option, radius=7.0, font_size=8.2)
    text(c, "请填涂 A / B / C", 468, 731, 7.2, color=MUTED)

    text(c, "学号（12位）", 48, 704, 9.5, color=DARK, bold=True)
    text(c, "每列只填涂一个数字", 150, 704, 7.2, color=MUTED)
    x0 = 82.0
    col_gap = 35.0
    row_gap = 14.0
    y0 = 684.0
    for col in range(12):
        x = x0 + col * col_gap
        text(c, str(col + 1), x, 702, 6.7, color=MUTED, align="center")
        for digit in range(10):
            y = y0 - digit * row_gap
            draw_bubble(c, x, y, str(digit), radius=5.0, font_size=5.3)


def draw_objective_column(c: canvas.Canvas, x: float, y_top: float, start: int, end: int):
    label_x = x
    bubble_start = x + 28
    gap = 19
    for number in range(start, end + 1):
        row = number - start
        y = y_top - row * 21
        text(c, f"{number:02d}", label_x, y - 2.8, 8.2, color=DARK, bold=True)
        if number <= 20:
            for idx, option in enumerate("ABCD"):
                draw_bubble(c, bubble_start + idx * gap, y, option)
        else:
            draw_bubble(c, bubble_start + 12, y, "T")
            draw_bubble(c, bubble_start + 53, y, "F")


def draw_answer_box(c: canvas.Canvas, x: float, y: float, w: float, h: float, label: str, hint: str = ""):
    c.setStrokeColor(GRID)
    c.setLineWidth(0.65)
    c.roundRect(x, y, w, h, 3, stroke=1, fill=0)
    text(c, label, x + 7, y + h - 13, 8.3, color=DARK, bold=True)
    if hint:
        fit_text(c, hint, x + 7, y + h - 25, w - 14, size=6.8, color=MUTED)
    # 书写基线
    line_y = y + h - 37
    while line_y > y + 7:
        draw_rule(c, x + 7, line_y, x + w - 7, color=HexColor("#d9e3e2"), width=0.35)
        line_y -= 16


def draw_compact_answer(c: canvas.Canvas, x: float, y: float, w: float, h: float, label: str):
    c.setStrokeColor(GRID)
    c.setLineWidth(0.6)
    c.roundRect(x, y, w, h, 3, stroke=1, fill=0)
    text(c, label, x + 7, y + h - 13, 8, color=DARK, bold=True)
    draw_rule(c, x + 7, y + 9, x + w - 7, color=HexColor("#d9e3e2"), width=0.35)


def draw_page_one(c: canvas.Canvas, version: str, marker: ImageReader):
    """两页版第1页：身份、客观题、程序填空题。"""
    draw_markers(c, 1, 2, marker)
    draw_title(c, version, "第1页：个人信息、客观题与程序填空")
    draw_identity(c)
    def local_section(x, w, label, note):
        c.setFillColor(LIGHT)
        c.roundRect(x, 498, w, 20, 4, stroke=0, fill=1)
        text(c, label, x + 8, 505, 9.2, color=DARK, bold=True)
        text(c, note, x + w - 8, 505, 7.1, color=MUTED, align="right")
    local_section(MARGIN, PAGE_W / 2 - MARGIN - 6, "一、单项选择题  1-15", "每题2分")
    local_section(PAGE_W / 2 + 6, PAGE_W / 2 - MARGIN - 6, "二、三、客观题  16-30", "多选 / 判断")
    draw_objective_column(c, 52, 480, 1, 15)
    draw_objective_column(c, PAGE_W / 2 + 18, 480, 16, 30)
    text(c, "16-20 选 A/B/C/D，21-30 选 T/F", PAGE_W / 2 + 18, 166, 7.0, color=MUTED)
    draw_section(c, "四、程序填空题  31-45", 175, "每空1分")
    cols = [(MARGIN, list(range(31, 36))), (PAGE_W / 3 + 2, list(range(36, 41))), (2 * PAGE_W / 3 - 28, list(range(41, 46)))]
    for x, numbers in cols:
        y = 153
        for q in numbers:
            draw_compact_answer(c, x, y - 17, PAGE_W / 3 - MARGIN - 7, 15, f"{q:02d}")
            y -= 19
    text(c, "填涂与书写均使用黑色笔，保持题号对应。", 48, 20, 7.3, color=MUTED)


def draw_page_two(c: canvas.Canvas, version: str, marker: ImageReader):
    """两页版第2页：逻辑改错与卷别专属主观题。"""
    draw_markers(c, 2, 2, marker)
    draw_title(c, version, "第2页：逻辑改错与主观题")
    left_x, left_w = MARGIN, PAGE_W / 2 - MARGIN - 12
    right_x, right_w = PAGE_W / 2 + 7, PAGE_W / 2 - MARGIN - 7
    def local_section(x, y, w, label, note):
        c.setFillColor(LIGHT)
        c.roundRect(x, y - 4, w, 20, 4, stroke=0, fill=1)
        text(c, label, x + 8, y + 3, 9.2, color=DARK, bold=True)
        text(c, note, x + w - 8, y + 3, 7.1, color=MUTED, align="right")
    local_section(left_x, 690, left_w, "五、逻辑改错题  46-60", "按行号填写正确代码")
    corrections = {
        46: "指针找最大值", 47: "指针找最大值", 48: "指针找最大值",
        49: "斐波那契数列", 50: "斐波那契数列", 51: "斐波那契数列", 52: "斐波那契数列", 53: "斐波那契数列",
        54: "学生信息输入输出", 55: "学生信息输入输出", 56: "学生信息输入输出",
        57: "奇数求和", 58: "奇数求和", 59: "圆面积计算", 60: "动态数组创建",
    }
    y = 664
    for q in range(46, 61):
        draw_compact_answer(c, left_x, y - 20, left_w, 17, f"{q:02d}")
        y -= 26
    text(c, "填写正确代码", left_x + 7, 296, 7.0, color=MUTED)
    text(c, "示例：46.（第3行）________正确代码________", left_x + 7, 281, 6.9, color=MUTED)
    local_section(right_x, 690, right_w, "六、材料问答题", "共9分")
    for index, q in enumerate((61, 62, 63)):
        draw_compact_answer(c, right_x, 640 - index * 34, right_w, 25, f"{q}  填空")
    local_section(right_x, 520, right_w, "算法题作答区", "7分")
    draw_answer_box(c, right_x, 70, right_w, 430, "算法思路与完整 C / C++ 代码", "请填写解题思路、关键步骤、边界处理和完整实现")

def draw_page_two_unified(c: canvas.Canvas, marker: ImageReader):
    """A/B/C共用第2页：题号清晰、作答区按卷型说明使用。"""
    draw_markers(c, 2, 2, marker)
    draw_title(c, "ABC", "第2页：逻辑改错与主观题")
    left_x, left_w = MARGIN, PAGE_W / 2 - MARGIN - 12
    right_x, right_w = PAGE_W / 2 + 7, PAGE_W / 2 - MARGIN - 7

    def local_section(x, y, w, label, note):
        c.setFillColor(LIGHT)
        c.roundRect(x, y - 4, w, 20, 4, stroke=0, fill=1)
        text(c, label, x + 8, y + 3, 9.2, color=DARK, bold=True)
        text(c, note, x + w - 8, y + 3, 7.1, color=MUTED, align="right")

    local_section(left_x, 690, left_w, "五、逻辑改错题  46-60", "每题填写对应行的正确代码")
    y = 664
    for q in range(46, 61):
        draw_compact_answer(c, left_x, y - 20, left_w, 17, f"{q:02d}")
        y -= 26
    text(c, "每个题号后填写该错误行的正确代码。", left_x + 7, 286, 7.0, color=MUTED)
    text(c, "代码较长时使用右侧对应主观作答区。", left_x + 7, 272, 7.0, color=MUTED)

    local_section(right_x, 690, right_w, "六、材料问答题", "共9分")
    for index, q in enumerate((61, 62, 63)):
        draw_compact_answer(c, right_x, 640 - index * 34, right_w, 25, f"{q}  填空")
    local_section(right_x, 520, right_w, "算法题作答区", "7分")
    draw_answer_box(c, right_x, 70, right_w, 430, "算法思路与完整 C / C++ 代码", "请填写解题思路、关键步骤、边界处理和完整实现")

def _insert_centered(page, value: str, y: float, fontsize: float, rect_left: float = 40.0, rect_right: float = 555.0, color=(0, 0, 0)):
    rect = fitz.Rect(rect_left, y - fontsize - 4, rect_right, y + 6)
    page.insert_textbox(rect, value, fontname="china-s", fontsize=fontsize, color=color, align=1, overlay=True)


def _insert_centered_title_with_bold_direction(page, y: float = 69.0, fontsize: float = 13.0):
    """绘制居中标题，其中“软件方向”使用微软雅黑粗体。"""
    regular_path = Path(r"C:\Windows\Fonts\msyh.ttc")
    bold_path = Path(r"C:\Windows\Fonts\msyhbd.ttc")
    regular_name = "msyh_regular"
    bold_name = "msyh_bold"
    page.insert_font(fontname=regular_name, fontfile=str(regular_path))
    page.insert_font(fontname=bold_name, fontfile=str(bold_path))
    prefix = "自然科学部第二轮面试答题卡  "
    emphasis = "软件方向"
    regular_font = fitz.Font(fontfile=str(regular_path))
    bold_font = fitz.Font(fontfile=str(bold_path))
    total_width = regular_font.text_length(prefix, fontsize=fontsize) + bold_font.text_length(emphasis, fontsize=fontsize)
    start_x = (page.rect.width - total_width) / 2
    page.insert_text((start_x, y), prefix, fontname=regular_name, fontsize=fontsize, color=(0, 0, 0), overlay=True)
    prefix_width = regular_font.text_length(prefix, fontsize=fontsize)
    page.insert_text((start_x + prefix_width, y), emphasis, fontname=bold_name, fontsize=fontsize, color=(0, 0, 0), overlay=True)

def create_unified_15th_style_card(output_dir: Path):
    """以去年答题卡为版式底稿，保持同样的双栏、字号、间距和定位标记。"""
    if not REFERENCE_15TH_PDF.is_file():
        raise FileNotFoundError(f"去年答题卡模板不存在：{REFERENCE_15TH_PDF}")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_pdf = output_dir / "第十六届软件方向二面试题A_B_C通用答题卡_定位标记版.pdf"
    layout_path = output_dir / "第十六届软件方向二面试题A_B_C通用答题卡_layout.json"
    marker_copy = output_dir / "omr_marker.jpg"
    shutil.copy2(MARKER_PATH, marker_copy)
    doc = fitz.open(str(REFERENCE_15TH_PDF))
    try:
        for page_index, page in enumerate(doc):
            page.draw_rect(fitz.Rect(95, 28, 500, 80), color=None, fill=(1, 1, 1), overlay=True)
            _insert_centered(page, "华南农业大学第十六届学生科技创新与创业联合会", 49, 9.8, 100, 495)
            _insert_centered_title_with_bold_direction(page, 69, 13.0)
            page.draw_rect(fitz.Rect(40, 82, 285, 100), color=None, fill=(1, 1, 1), overlay=True)
            page.insert_text((42, 92), "考试时间 120分钟", fontsize=7.5, fontname="china-s", color=(0, 0, 0), overlay=True)
            if page_index == 1:
                # 第六部分按今年三套试卷统一重排：前部为编号填空，最后的算法题使用整栏自由作答区。
                page.add_redact_annot(fitz.Rect(38, 535, 299, 785), fill=(1, 1, 1))
                page.add_redact_annot(fitz.Rect(312, 140, 554, 785), fill=(1, 1, 1))
                page.apply_redactions()
                page.draw_rect(
                    fitz.Rect(38, 540, 290, 564),
                    color=(0.80, 0.84, 0.84),
                    fill=(0.95, 0.97, 0.97),
                    width=0.6,
                    overlay=True,
                )
                page.insert_text((49, 556), "六 材料问答题", fontsize=9.5, fontname="china-s", color=(0, 0, 0), overlay=True)
                page.insert_textbox(
                    fitz.Rect(230, 545, 282, 561),
                    "共9分",
                    fontsize=7.2,
                    fontname="china-s",
                    color=(86 / 255, 96 / 255, 100 / 255),
                    align=2,
                    overlay=True,
                )

                fill_top = 574
                fill_step = 21.5
                for offset, question_no in enumerate(range(61, 64)):
                    row_y = fill_top + offset * fill_step
                    page.insert_text((46, row_y + 12.3), str(question_no), fontsize=7.2, fontname="china-s", color=(0, 0, 0), overlay=True)
                    page.draw_rect(
                        fitz.Rect(63, row_y, 287, row_y + 17.5),
                        color=(0.78, 0.82, 0.83),
                        fill=(1, 1, 1),
                        width=0.55,
                        overlay=True,
                    )
                    page.draw_line(
                        fitz.Point(72, row_y + 12.5),
                        fitz.Point(278, row_y + 12.5),
                        color=(0.86, 0.88, 0.88),
                        width=0.45,
                        overlay=True,
                    )


                page.draw_rect(
                    fitz.Rect(320, 143, 553, 166),
                    color=(0.80, 0.84, 0.84),
                    fill=(0.93, 0.96, 0.95),
                    width=0.6,
                    overlay=True,
                )
                page.insert_text((328, 158), "64", fontsize=9.5, fontname="helv", color=(0, 0, 0), overlay=True)
                page.insert_text((343, 158), "算法题作答区", fontsize=9.5, fontname="china-s", color=(0, 0, 0), overlay=True)
                page.insert_textbox(
                    fitz.Rect(500, 147, 545, 163),
                    "7分",
                    fontsize=7.2,
                    fontname="china-s",
                    color=(86 / 255, 96 / 255, 100 / 255),
                    align=2,
                    overlay=True,
                )
                page.insert_text((320, 185), "请写明算法思路、关键步骤及C/C++代码。", fontsize=7.4, fontname="china-s", color=(0, 0, 0), overlay=True)
                page.insert_text((320, 201), "答题内容较长时可连续使用本栏。", fontsize=6.5, fontname="china-s", color=(0.32, 0.41, 0.42), overlay=True)
                page.draw_rect(
                    fitz.Rect(316, 214, 553, 780),
                    color=(0.76, 0.80, 0.81),
                    fill=(1, 1, 1),
                    width=0.65,
                    overlay=True,
                )
                answer_lines = page.new_shape()
                answer_line_left = 326.0
                answer_line_right = 542.0
                for line_y in range(234, 771, 20):
                    answer_lines.draw_line(
                        fitz.Point(answer_line_left, line_y),
                        fitz.Point(answer_line_right, line_y),
                    )
                answer_lines.finish(color=(0.82, 0.85, 0.86), width=0.60)
                answer_lines.commit(overlay=True)
        metadata = dict(doc.metadata or {})
        metadata["title"] = "第十六届软件方向二面试题通用答题卡"
        metadata["subject"] = "参考第十五届答题卡 UI 的两页通用定位标记答题卡"
        doc.set_metadata(metadata)
        doc.save(str(output_pdf), garbage=4, deflate=True)
    finally:
        doc.close()
    layout = {"page_size": "A4", "page_count": 2, "template": "15th_marker_answer_card_ui", "version": "ABC", "variant": "UNIFIED_15TH_STYLE", "identity": {"name": {"page": 1}, "student_id": {"digits": 12, "page": 1}, "paper_type": {"options": ["A", "B", "C"], "page": 1}}, "objective": {"single": list(range(1, 16)), "multiple": list(range(16, 21)), "true_false": list(range(21, 31)), "page": 1}, "program_fill": {"questions": list(range(31, 46)), "page": 1}, "logic_correction": {"questions": list(range(46, 61)), "page": 2}, "subjective": {"page": 2, "section": "六 材料问答题", "fill_questions": [61, 62, 63], "paper_fill_mapping": {"A": [61, 62, 63], "B": [61, 62, 63], "C": [61, 62, 63]}, "fill_score": 9, "algorithm_area": {"position": "right_column", "question": 64, "score": 7, "label": "算法题"}}, "marker": {"corner_count": 4, "side_timing_bars_per_side": 41}, "pdf": output_pdf.name, "pdf_sha256": sha256(output_pdf), "marker_sha256": sha256(marker_copy)}
    layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return output_pdf, layout_path

def build_layout(version: str, variant: str):
    common = {
        "page_size": "A4",
        "page_count": 2,
        "marker": {
            "asset": "omr_marker.jpg",
            "corner_count": 4,
            "side_timing_bars_per_side": 29,
            "inset_pt": MARKER_INSET,
            "size_pt": MARKER_SIZE,
        },
        "identity": {
            "name": {"type": "ocr_text", "page": 1},
            "student_id": {"type": "omr_digits", "digits": 12, "page": 1},
            "paper_type": {"type": "omr_single", "options": ["A", "B", "C"], "page": 1},
        },
        "objective": {
            "single": list(range(1, 16)),
            "multiple": list(range(16, 21)),
            "true_false": list(range(21, 31)),
            "page": 1,
        },
        "program_fill": {"questions": list(range(31, 46)), "page": 2},
        "logic_correction": {"questions": list(range(46, 61)), "page": 2},
        "version": version,
        "variant": variant,
    }
    common["subjective"] = {"page": 2, "blank_questions": [61, 62, 63], "algorithm_questions": [64]}
    return common


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def create_card(version: str, variant: str, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    if variant == "UNIFIED":
        output_pdf = output_dir / "第十六届软件方向二面试题A_B_C通用答题卡_定位标记版.pdf"
        layout_path = output_dir / "第十六届软件方向二面试题A_B_C通用答题卡_layout.json"
    else:
        output_pdf = output_dir / f"第十六届软件方向二面试题{version}卷答题卡_定位标记版.pdf"
        layout_path = output_dir / f"第十六届软件方向二面试题{version}卷答题卡_layout.json"
    marker_copy = output_dir / "omr_marker.jpg"
    shutil.copy2(MARKER_PATH, marker_copy)
    marker = ImageReader(str(marker_copy))
    c = canvas.Canvas(str(output_pdf), pagesize=A4, pageCompression=1)
    c.setTitle(f"第十六届软件方向二面试题{version}答题卡")
    c.setAuthor("OMRChecker")
    draw_page_one(c, version, marker)
    c.showPage()
    if variant == "UNIFIED":
        draw_page_two_unified(c, marker)
    else:
        draw_page_two(c, version, marker)
    c.save()
    layout = build_layout(version, variant)
    layout.update({"pdf": output_pdf.name, "pdf_sha256": sha256(output_pdf), "marker_sha256": sha256(marker_copy)})
    layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return output_pdf, layout_path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--separate", action="store_true", help="同时生成A、B、C三份独立答题卡")
    args = parser.parse_args()
    register_fonts()
    outputs = []
    if args.separate:
        variants = (("A", "A"), ("B", "BC"), ("C", "BC"))
        for version, variant in variants:
            pdf_path, layout_path = create_card(version, variant, args.output_dir)
            outputs.append({"version": version, "variant": variant, "pdf": str(pdf_path), "layout": str(layout_path)})
    else:
        pdf_path, layout_path = create_unified_15th_style_card(args.output_dir)
        outputs.append({"version": "ABC", "variant": "UNIFIED_15TH_STYLE", "pdf": str(pdf_path), "layout": str(layout_path)})
    print(json.dumps(outputs, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
