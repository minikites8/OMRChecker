"""Marker-version answer card layout and perspective alignment regression tests."""

from pathlib import Path

import cv2
import fitz
import numpy as np

import exam_review
from marker_answer_card import MARKER_SOURCE, SOURCE_PDF, create_marker_answer_card


def _render_gray(page):
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY, alpha=False)
    return np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width).copy()


def test_marker_pdf_has_four_corner_marks_and_side_timing_bars(tmp_path):
    output = tmp_path / "marker.pdf"
    info = create_marker_answer_card(SOURCE_PDF, output, MARKER_SOURCE)
    assert info["page_count"] == 2
    assert info["side_mark_count_per_side"] == 41
    assert info["marker_size_pt"] == 18.0
    with fitz.open(str(SOURCE_PDF)) as source, fitz.open(str(output)) as result:
        assert len(source) == len(result) == 2
        for page_index, (original_page, marked_page) in enumerate(zip(source, result)):
            if page_index == 0:
                text = marked_page.get_text()
                assert "试卷类型" in text
                assert "A" in text and "B" in text and "C" in text
            assert len(marked_page.get_images(full=True)) == 4
            bars = [
                drawing for drawing in marked_page.get_drawings()
                if 8.5 <= drawing["rect"].width <= 9.5
                and 3.0 <= drawing["rect"].height <= 4.0
                and drawing.get("fill") and all(channel < 0.1 for channel in drawing["fill"])
            ]
            assert len(bars) == 82


def test_marker_processor_rectifies_perspective_photo_and_legacy_page():
    marker_pdf = next((Path(__file__).resolve().parents[2] / "output" / "pdf" / "exam_answer_cards" / "marker_version").glob("*.pdf"))
    with fitz.open(str(marker_pdf)) as document:
        page = _render_gray(document[0])
    height, width = page.shape
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    destination = np.float32([[146, 115], [1268, 70], [1312, 1755], [74, 1815]])
    photo = cv2.warpPerspective(page, cv2.getPerspectiveTransform(source, destination), (1400, 1900), borderValue=190)
    aligned = exam_review._warp_marker_page(photo)
    assert aligned is not None
    assert aligned.shape == (int(exam_review.PAGE_H * 2), int(exam_review.PAGE_W * 2))
    reference = cv2.resize(exam_review._reference_pages()[0], (aligned.shape[1], aligned.shape[0]))
    assert float(np.mean(np.abs(aligned.astype(np.int16) - reference.astype(np.int16)))) < 5.0
    with fitz.open(str(SOURCE_PDF)) as document:
        legacy = _render_gray(document[0])
    assert exam_review._warp_marker_page(legacy) is None


def test_filled_marker_photo_reads_single_multiple_and_true_false(tmp_path, monkeypatch):
    from types import SimpleNamespace

    marker_pdf = Path(__file__).resolve().parents[2] / "output" / "pdf" / "exam_answer_cards" / "marker_version" / "15th软件方向二面试题A卷答题卡_定位标记版.pdf"
    with fitz.open(str(marker_pdf)) as document:
        first = _render_gray(document[0])
        second = _render_gray(document[1])
    for x, y in [(80, 348), (107, 368.5), (90, 503), (148, 503), (402, 503), (477, 503)]:
        cv2.circle(first, (round(x * 2), round(y * 2)), 7, 0, -1)
    cv2.circle(first, (round(486 * 2), round(259 * 2)), 8, 0, -1)
    height, width = first.shape
    source = np.float32([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]])
    destination = np.float32([[146, 115], [1268, 70], [1312, 1755], [74, 1815]])
    transform = cv2.getPerspectiveTransform(source, destination)
    photos = []
    for index, page in enumerate((second, first)):
        photo = cv2.warpPerspective(page, transform, (1400, 1900), borderValue=190)
        path = tmp_path / f"photo_{index}.png"
        assert cv2.imwrite(str(path), photo)
        photos.append(path)
    monkeypatch.setattr(exam_review, "_recognize_crops", lambda crops, labels: [
        SimpleNamespace(text="", confidence=0.0, error=None) for _ in labels
    ])
    result = exam_review.extract_answer_card(photos)
    for number, answer in {"1": "A", "2": "B", "16": "AC", "21": "F", "26": "T"}.items():
        assert result["objective"][number] == answer, (number, result["objective"][number], result["alignment_scores"])
    assert result["paper_type"] == "B"
    assert result["paper_type_status"] == "自动识别"



def test_marker_warp_keeps_full_page_for_camera_scan_with_narrower_page_ratio():
    """相机页比例偏窄时，四角定位仍应把整张纸铺满标准画布。"""
    raw_path = (
        Path(__file__).resolve().parents[2]
        / "outputs" / "answer_review" / "20260922-005356-e7f5d4"
        / "input" / "raw1.png"
    )
    if not raw_path.is_file():
        return
    raw = cv2.imread(str(raw_path), cv2.IMREAD_GRAYSCALE)
    aligned = exam_review._warp_page(raw)
    assert aligned is not None
    height, width = aligned.shape
    assert (height, width) == (int(exam_review.PAGE_H * 2), int(exam_review.PAGE_W * 2))
    # 底部定位标记和页边线保持在画布底部，禁止出现大段空白压缩。
    bottom = aligned[height - 120:height, :]
    assert int(np.count_nonzero(bottom < 100)) > 100
    assert float(np.mean(aligned[int(height * 0.75):])) < 252.0
