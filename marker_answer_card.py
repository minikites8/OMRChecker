"""Create a two-page answer card with four OMR markers on each page."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "output" / "pdf" / "exam_answer_cards" / "20260917"
SOURCE_PDF = SOURCE_DIR / "15th\u8f6f\u4ef6\u65b9\u5411\u4e8c\u9762\u8bd5\u9898A\u5377\u7b54\u9898\u5361.pdf"
MARKER_SOURCE = ROOT / "samples" / "sample1" / "omr_marker.jpg"
OUTPUT_DIR = ROOT / "output" / "pdf" / "exam_answer_cards" / "marker_version"
OUTPUT_PDF = OUTPUT_DIR / "15th\u8f6f\u4ef6\u65b9\u5411\u4e8c\u9762\u8bd5\u9898A\u5377\u7b54\u9898\u5361_\u5b9a\u4f4d\u6807\u8bb0\u7248.pdf"
MARKER_SIZE_PT = 18.0
MARKER_INSET_PT = 26.0
SIDE_MARK_COUNT = 41
SIDE_MARK_TOP_PT = 84.0
SIDE_MARK_HEIGHT_PT = 3.5
SIDE_MARK_WIDTH_PT = 9.0
PAPER_TYPE_OPTIONS = ("A", "B", "C")
PAPER_TYPE_BUBBLE_CENTERS = (458.0, 486.0, 514.0)
PAPER_TYPE_BUBBLE_Y = 259.0


def marker_centers(page_width, page_height):
    inset = MARKER_INSET_PT
    return [(inset, inset), (page_width - inset, inset),
            (inset, page_height - inset), (page_width - inset, page_height - inset)]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest().upper()


def _draw_paper_type_selector(page, page_index):
    # 覆盖原模板中的固定 A 卷文字，保留统一标题并绘制可填涂的 A/B/C 选项。
    page.draw_rect(fitz.Rect(388, 50, 420, 77), color=None, fill=(1, 1, 1), overlay=True)
    page.insert_text((388, 69.5), "试卷", fontsize=16, fontname="china-s", color=(0, 0, 0), overlay=True)
    if page_index == 0:
        page.draw_rect(fitz.Rect(410, 245, 540, 275), color=None, fill=(1, 1, 1), overlay=True)
        page.insert_text((414, 264), "试卷类型", fontsize=9, fontname="china-s", color=(0, 0, 0), overlay=True)
        for center_x, option in zip(PAPER_TYPE_BUBBLE_CENTERS, PAPER_TYPE_OPTIONS):
            page.draw_circle((center_x, PAPER_TYPE_BUBBLE_Y), 5.2, color=(0, 0, 0), width=0.8, fill=None, overlay=True)
            page.insert_text((center_x + 7, PAPER_TYPE_BUBBLE_Y + 2.8), option, fontsize=7.5, fontname="helv", color=(0, 0, 0), overlay=True)
    else:
        # 第 2 页不保留姓名、学号、试卷类型填写栏，题目内容从原位置开始。
        page.draw_rect(fitz.Rect(34, 104, 561, 140), color=None, fill=(1, 1, 1), overlay=True)


def create_marker_answer_card(source_pdf=SOURCE_PDF, output_pdf=OUTPUT_PDF, marker_path=MARKER_SOURCE):
    source_pdf = Path(source_pdf)
    output_pdf = Path(output_pdf)
    marker_path = Path(marker_path)
    if not source_pdf.is_file() or not marker_path.is_file():
        raise FileNotFoundError("Source PDF or marker asset is missing")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open(str(source_pdf))
    try:
        for page_index, page in enumerate(document):
            page_width, page_height = page.rect.width, page.rect.height
            _draw_paper_type_selector(page, page_index)
            # The old frame's continuous vertical edges would merge with the tick bars.
            for x in (MARKER_INSET_PT, page_width - MARKER_INSET_PT):
                page.draw_rect(fitz.Rect(x - 2, 27, x + 2, page_height - 27),
                               color=None, fill=(1, 1, 1), overlay=True)
            step = (page_height - 2 * SIDE_MARK_TOP_PT) / (SIDE_MARK_COUNT - 1)
            for index in range(SIDE_MARK_COUNT):
                y = SIDE_MARK_TOP_PT + index * step
                for x in (MARKER_INSET_PT, page_width - MARKER_INSET_PT):
                    page.draw_rect(fitz.Rect(x - SIDE_MARK_WIDTH_PT / 2, y,
                                             x + SIDE_MARK_WIDTH_PT / 2, y + SIDE_MARK_HEIGHT_PT),
                                   color=None, fill=(0, 0, 0), overlay=True)
            half = MARKER_SIZE_PT / 2
            for cx, cy in marker_centers(page_width, page_height):
                bounds = fitz.Rect(cx - half, cy - half, cx + half, cy + half)
                page.insert_image(bounds, filename=str(marker_path), overlay=True, keep_proportion=True)
        metadata = dict(document.metadata or {})
        metadata["title"] = "\u7b54\u9898\u5361 A/B/C \u8bd5\u5377\u7c7b\u578b \u56db\u89d2\u5b9a\u4f4d\u6807\u8bb0\u7248"
        metadata["subject"] = "Four OMR corner markers per page for phone capture; paper type A/B/C bubbles"
        document.set_metadata(metadata)
        document.save(str(output_pdf), garbage=4, deflate=True)
        page_count = len(document)
        page_size = [document[0].rect.width, document[0].rect.height]
    finally:
        document.close()
    marker_copy = output_pdf.parent / "omr_marker.jpg"
    shutil.copy2(marker_path, marker_copy)
    info = {
        "source_pdf": str(source_pdf),
        "output_pdf": str(output_pdf),
        "marker_path": str(marker_copy),
        "marker_size_pt": MARKER_SIZE_PT,
        "marker_inset_pt": MARKER_INSET_PT,
        "side_mark_count_per_side": SIDE_MARK_COUNT,
        "side_mark_top_pt": SIDE_MARK_TOP_PT,
        "side_mark_width_pt": SIDE_MARK_WIDTH_PT,
        "side_mark_height_pt": SIDE_MARK_HEIGHT_PT,
        "paper_type_options": list(PAPER_TYPE_OPTIONS),
        "paper_type_bubble_centers_pt": list(PAPER_TYPE_BUBBLE_CENTERS),
        "paper_type_bubble_y_pt": PAPER_TYPE_BUBBLE_Y,
        "page_count": page_count,
        "page_size_pt": page_size,
        "source_sha256": sha256(source_pdf),
        "output_sha256": sha256(output_pdf),
        "marker_sha256": sha256(marker_copy),
    }
    (output_pdf.parent / "marker_layout.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return info


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=SOURCE_PDF)
    parser.add_argument("--output", type=Path, default=OUTPUT_PDF)
    parser.add_argument("--marker", type=Path, default=MARKER_SOURCE)
    args = parser.parse_args()
    print(json.dumps(create_marker_answer_card(args.source, args.output, args.marker), ensure_ascii=False))
