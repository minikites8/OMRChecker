"""题号局部配准、保守填涂判断和多行 OCR 的自包含回归测试。"""
import threading
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

import exam_review as er
from answer_alignment import PAGE_H, PAGE_W, align_printed_region, region_anchors


def synthetic_reference(region):
    image = np.full((1684, 1191), 255, np.uint8)
    for index, (x, y, w, h) in enumerate(region_anchors(region)):
        text = str(31 + index) if region != "material" else str(61 + index)
        cv2.putText(image, text, (round(x * 2) + 2, round((y + h) * 2) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, 0, 1, cv2.LINE_AA)
    return image


@pytest.mark.parametrize("region", ["program", "single", "multiple_tf", "correction", "material"])
@pytest.mark.parametrize("dx,dy,scale", [(-20, 12, 1.0), (8, -12, 0.96)])
def test_local_anchors_recover_shift_and_scale(region, dx, dy, scale):
    ref = synthetic_reference(region)
    matrix = np.float64([[scale, 0, dx + (1-scale)*300], [0, scale, dy + (1-scale)*900]])
    image = cv2.warpAffine(ref, matrix, (ref.shape[1], ref.shape[0]), borderValue=255)
    _, info = align_printed_region(image, ref, region, initial_shift_y=dy/2)
    assert info["status"] == "已校正", info
    anchors = np.float32([[2*(x+w/2), 2*(y+h/2)] for x,y,w,h in region_anchors(region)])
    predicted = cv2.transform(anchors[None], np.array(info["matrix"]))[0]
    actual = cv2.transform(anchors[None], matrix)[0]
    assert np.max(np.linalg.norm(predicted-actual, axis=1)) < 4
    assert info["inliers"] >= (3 if region == "material" else 5)


def test_blank_reference_keeps_image_and_reports_review():
    image = np.full((1684, 1191), 255, np.uint8)
    aligned, info = align_printed_region(image, image.copy(), "program")
    assert aligned is image
    assert info["status"] == "待复核"
    assert "matrix" not in info


def test_extreme_deformation_is_rejected():
    ref = synthetic_reference("program")
    image = cv2.warpAffine(ref, np.float32([[1, 0, 200], [0, 1, 0]]), (1191, 1684), borderValue=255)
    _, info = align_printed_region(image, ref, "program")
    assert info["status"] == "待复核"
    assert "matrix" not in info


def test_single_choices_require_separation_and_one_mark():
    assert er._single_bubble_answer([("A", .88), ("B", .83), ("C", .70), ("D", .63)]) == ""
    assert er._single_bubble_answer([("T", .13), ("F", .17)]) == ""
    assert er._single_bubble_answer([("A", .26), ("B", .24)]) == ""
    assert er._single_bubble_answer([("A", .80), ("B", .11)]) == "A"


def test_ambiguous_objective_requires_manual_review_even_with_answer(monkeypatch):
    monkeypatch.setattr(er, "extract_answer_card", lambda *a, **kw: {
        "objective": {"15": "A"}, "objective_flags": {"15": "多处填涂"},
        "text_fields": {}, "crop_paths": {}, "ocr_errors": [],
    })
    review = er._build_review_from_maps({}, {"15":"A"}, {}, [])
    item = next(i for i in review["objective"] if i["question"] == "15")
    assert item["auto_status"] == "需人工复核"
    assert item["recognition_warning"] == "多处填涂"
    assert item["recognized_label"] == "多处填涂"
    assert item["final_answer"] == ""


def test_mapped_crop_contains_shifted_ink_and_excludes_next_row():
    page = np.full((1684, 1191), 255, np.uint8)
    # Reference Q31 box shifted 15 pixels left and 20 down.
    sx, sy = page.shape[1]/PAGE_W, page.shape[0]/PAGE_H
    top = round((PAGE_H-180-22)*sy) + 20
    left = round(62*sx) - 15
    page[top+5:top+15, left+3:left+18] = 0
    page[top+58:top+64, left+3:left+18] = 60
    crop = er._crop_aligned_rect(page, (62,180,153,22), [[1,0,-15],[0,1,20]])
    assert (crop == 0).sum() == 150
    assert (crop == 60).sum() == 0


def test_multiline_split_preserves_each_line_and_skips_blank():
    image = np.full((240, 440), 255, np.uint8)
    cv2.putText(image, "int a = 1;", (20,50), cv2.FONT_HERSHEY_SIMPLEX, .8, 0, 2)
    cv2.putText(image, "return a;", (20,105), cv2.FONT_HERSHEY_SIMPLEX, .8, 0, 2)
    cv2.line(image, (0,180), (439,180), 0, 1)
    lines = er._split_handwriting_lines(image)
    assert len(lines) == 2
    assert all(10 < line.shape[0] < 55 for line in lines)
    assert er._split_handwriting_lines(np.full_like(image, 255)) == []


def test_multiline_ocr_merges_all_lines_in_reading_order(monkeypatch):
    import src.ocr as ocr
    image = np.full((240,440),255,np.uint8)
    cv2.putText(image,'row one',(20,50),cv2.FONT_HERSHEY_SIMPLEX,.8,0,2)
    cv2.putText(image,'row two',(20,110),cv2.FONT_HERSHEY_SIMPLEX,.8,0,2)
    calls = []
    class Model:
        def recognize(self, images, labels):
            calls.append((len(images), labels))
            return [SimpleNamespace(text=f"line-{i+1}",confidence=.9,error=None) for i in range(len(images))]
    monkeypatch.setattr(ocr,'create_text_recognizer',lambda p:Model())
    monkeypatch.setattr(er,'_OCR_THREAD_LOCAL',threading.local())
    result=er._recognize_crops([image,np.full_like(image,255)],["64代码","64思路"])
    assert result[0].text == "line-1\nline-2"
    assert result[1].text == ""
    assert calls[0][0] == 2


def test_thought_and_code_preview_files_are_distinct(tmp_path, monkeypatch):
    page=np.full((1684,1191),255,np.uint8)
    monkeypatch.setattr(er,'_load_page',lambda p:[('p1',page),('p2',page)])
    monkeypatch.setattr(er,'_align_and_order_pages',lambda pages:(pages,[]))
    monkeypatch.setattr(er,'_reference_pages',lambda:[])
    monkeypatch.setattr(er,'_recognize_crops',lambda crops,labels:[SimpleNamespace(text='',confidence=0,error=None) for _ in labels])
    report=er.extract_answer_card([tmp_path/'card.pdf'], tmp_path/'crops')
    paths=report['crop_paths']
    assert paths['64思路'] != paths['64代码']
    assert paths['64思路'].endswith('64_thought.png')
    assert paths['64代码'].endswith('64_code.png')
    assert cv2.imread(paths['64代码']).shape[0] > cv2.imread(paths['64思路']).shape[0]
    assert all(question in paths for question in ("61", "62", "63"))
    assert all(question not in paths for question in ("61(1)", "61(2)", "63(3)"))


def test_16th_reference_and_subjective_crop_geometry():
    assert er.REFERENCE_PDF.name == "第十六届软件方向二面试题A_B_C通用答题卡_定位标记版.pdf"
    assert er.REFERENCE_PDF.is_file()
    assert region_anchors("material") == [
        (44, 577.0, 18, 12),
        (44, 598.5, 18, 12),
        (44, 620.0, 18, 12),
    ]
