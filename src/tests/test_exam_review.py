import base64
import json
import threading
import zipfile

import pytest
from pathlib import Path
from urllib.request import Request, urlopen

import scan_ui
import exam_review
from exam_import import import_exam_and_answers
from exam_review import (
    _multiple_bubble_answer,
    _objective_expected,
    _single_bubble_answer,
    extract_answer_card,
    extract_answer_key,
    extract_exam_content,
    judge_answer,
)


def write_docx(path: Path, paragraphs=(), table_rows=()):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = []
    for text in paragraphs:
        body.append(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>")
    if table_rows:
        rows = []
        for row in table_rows:
            cells = "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row)
            rows.append(f"<w:tr>{cells}</w:tr>")
        body.append(f"<w:tbl>{''.join(rows)}</w:tbl>")
    document = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="{ns}"><w:body>{''.join(body)}<w:sectPr/></w:body></w:document>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'''
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", content_types)
        package.writestr("word/document.xml", document)


def test_docx_extractors_and_auto_judge(tmp_path):
    exam = tmp_path / "exam.docx"
    answer = tmp_path / "answer.docx"
    write_docx(exam, paragraphs=["31. 程序填空题"], table_rows=[["___(31)___ i % 2"]])
    write_docx(answer, table_rows=[["31 i%2 != 0", "61 (1) x > y（1分）"]])

    exam_content = extract_exam_content(exam)
    answers = extract_answer_key(answer)
    assert "31" in exam_content["source"]
    assert answers["31"] == "i%2 != 0"
    assert answers["61"].startswith("x > y")
    assert judge_answer("i%2 != 0", "i % 2 != 0", 0.92)[0] == "自动通过"
    assert judge_answer("i%2 != 0", "", 0.0)[0] == "待复核"


def test_review_endpoint_saves_manual_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: False)
    imported = scan_ui.run_exam_import({
        "exam_text": json.dumps([
            {"name": "选择", "questions": [{"id": "1", "type": "single", "title": "1. 单选", "score": 2, "answer": "A"}]},
            {"name": "填空", "questions": [{"id": "31", "type": "blank", "title": "31. 程序填空", "score": 1, "answer": "i%2 != 0"}]},
        ], ensure_ascii=False),
    })
    report = {
        "ok": True,
        "review_id": "review-test",
        "source": {"answer_count": 1},
        "objective": [{"question": "1", "recognized": "B", "expected": "A", "auto_status": "需人工复核",
                       "final_answer": "B", "final_status": "需人工复核", "manual_status": "", "reviewed_answer": "", "override_answer": False, "score": 2}],
        "items": [{
            "question": "31", "source_content": "程序填空", "expected_answer": "i%2 != 0",
            "recognized_text": "i%2 != 0", "confidence": 0.9,
            "auto_status": "自动通过", "manual_status": "待复核", "manual_text": "", "score": 1,
        }],
        "ocr_errors": [],
        "review_summary": {"total": 1, "auto_pass": 1, "manual": 0},
    }
    def fake_build(exam, answers, cards, image_dir=None):
        folder = image_dir / "objective"
        folder.mkdir(parents=True)
        bubble = folder / "q01.png"
        bubble.write_bytes(b"\x89PNG-preview")
        return {**report, "objective": [{**report["objective"][0], "bubble_image": str(bubble)}]}

    monkeypatch.setattr(scan_ui, "build_review_from_structured", fake_build)
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        payload = {
            "import_id": imported["import_id"],
            "card_files": [{"name": "card.png", "data": "data:image/png;base64," + base64.b64encode(b"png").decode()}],
        }
        request = Request(base + "/api/review", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            created = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        review_id = created["review_id"]
        assert created["import_id"] == imported["import_id"]
        latest = json.loads((tmp_path / "reviews" / "latest.json").read_text(encoding="utf-8"))
        assert latest["review_id"] == review_id
        saved = json.loads((tmp_path / "reviews" / review_id / "output" / "review.json").read_text(encoding="utf-8"))
        assert saved["items"][0]["question"] == "31"
        assert saved["objective"][0]["bubble_url"].endswith("/objective/q01.png")
        assert "bubble_image" not in saved["objective"][0]
        with urlopen(base + saved["objective"][0]["bubble_url"], timeout=5) as preview_response:
            assert preview_response.read() == b"\x89PNG-preview"

        confirm = {"review_id": review_id, "decisions": [{"question": "31", "status": "通过", "text": "i%2 != 0"}],
                   "objective_decisions": [{"question": "1", "override_answer": True, "answer": "A", "status": ""}]}
        request = Request(base + "/api/review/confirm", data=json.dumps(confirm).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=10) as response:
            updated = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert updated["items"][0]["manual_status"] == "通过"
        assert updated["objective"][0]["recognized"] == "B"
        assert updated["objective"][0]["final_answer"] == "A"
        assert updated["objective"][0]["final_status"] == "通过"
        assert updated["objective_summary"]["reviewed"] == 1
        assert updated["score_summary"]["total_score"] == 3
        assert updated["score_summary"]["possible_score"] == 3
        reloaded = json.loads((tmp_path / "reviews" / review_id / "output" / "review.json").read_text(encoding="utf-8"))
        assert reloaded["objective"][0]["reviewed_answer"] == "A"
        invalid = {"review_id": review_id, "objective_decisions": [{"question": "1", "override_answer": True, "answer": "AB"}]}
        request = Request(base + "/api/review/confirm", data=json.dumps(invalid).encode(), headers={"Content-Type": "application/json"}, method="POST")
        from urllib.error import HTTPError
        import pytest
        with pytest.raises(HTTPError) as failure:
            urlopen(request, timeout=10)
        assert failure.value.code == 400
        persisted = json.loads((tmp_path / "reviews" / review_id / "output" / "review.json").read_text(encoding="utf-8"))
        assert persisted["objective"][0]["final_answer"] == "A"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_review_ui_is_connected():
    html = scan_ui.UI_ROOT.joinpath("index.html").read_text(encoding="utf-8")
    javascript = scan_ui.UI_ROOT.joinpath("app.js").read_text(encoding="utf-8")
    assert "自动判断与人工复核" in html
    assert "/api/review" in javascript
    assert "保存复核结果" in html
    assert "客观题复核" in html
    assert 'id="reviewObjectiveSummary"' in html
    assert "objective_decisions" in javascript
    assert "修正识别" in javascript
    assert "objective-scan-overlay" in javascript
    assert "扫描识别：" in javascript
    assert "识别与参考答案一致" in javascript
    assert "objectiveNeedsReview" in javascript
    assert "objectiveRecognitionLabel" in javascript
    assert "objectiveFinalLabel" in javascript
    assert "待人工确认" in javascript
    assert 'id="reviewTotalScore"' in html
    assert "calculateLocalScore" in javascript
    assert "renderScoreBoard" in javascript




def test_objective_mapping_and_conservative_text_judging():
    assert _objective_expected("正确") == "T"
    assert _objective_expected("错误") == "F"
    assert _single_bubble_answer([("A", 0.08), ("B", 0.42), ("C", 0.07), ("D", 0.06)]) == "B"
    assert _multiple_bubble_answer([("A", 0.52), ("B", 0.09), ("C", 0.61), ("D", 0.08)]) == "AC"
    assert judge_answer("i % 2 != 0", "i % 2 = 0", 0.99)[0] == "不通过"
    assert judge_answer("arr[j] > arr[j+1]", "arr[j] > arr[j + 1]", 0.94)[0] == "自动通过"
    wrong_status, wrong_reason = judge_answer("arr[j] > arr[j+1]", "arr[j] > arr[j-1]", 0.94)
    assert wrong_status == "不通过"
    assert "下标" in wrong_reason


def test_page_images_are_aligned_and_reordered(monkeypatch):
    import numpy as np

    page_two = np.full((4, 4), 2, dtype=np.uint8)
    page_one = np.full((4, 4), 1, dtype=np.uint8)
    reference_one = np.full((4, 4), 10, dtype=np.uint8)
    reference_two = np.full((4, 4), 20, dtype=np.uint8)
    monkeypatch.setattr(exam_review, "_warp_page", lambda image: image)
    monkeypatch.setattr(exam_review, "_reference_pages", lambda: [reference_one, reference_two])

    def fake_align(image, reference):
        page_number = int(image[0, 0])
        reference_number = int(reference[0, 0]) // 10
        score = 100 if page_number == reference_number else 1
        return image, score

    monkeypatch.setattr(exam_review, "_feature_align", fake_align)
    ordered, scores = exam_review._align_and_order_pages([page_two, page_one])
    assert [int(page[0, 0]) for page in ordered] == [1, 2]
    assert scores == [2, 200]


def test_answer_card_merges_long_answer_and_keeps_12_digit_id(monkeypatch, tmp_path):
    import numpy as np
    from types import SimpleNamespace

    page = np.full((1684, 1190), 255, dtype=np.uint8)
    monkeypatch.setattr(exam_review, "_load_page", lambda _path: [("p1", page), ("p2", page)])
    monkeypatch.setattr(exam_review, "_align_and_order_pages", lambda images: (images, [200, 20]))
    monkeypatch.setattr(exam_review, "_normalize_illumination", lambda image: image)
    monkeypatch.setattr(exam_review, "_handwriting_ratio", lambda crop, reference: 1.0)
    monkeypatch.setattr(exam_review, "_read_student_id", lambda image, ink: "202619240110")
    monkeypatch.setattr(exam_review, "_bubble_scores", lambda *args, **kwargs: [(choice, 0.0) for choice in args[3]])

    def fake_recognize(_crops, labels):
        values = {"64思路": "先分析复杂度", "64代码": "return answer;"}
        return [SimpleNamespace(text=values.get(label, ""), confidence=0.96, error=None) for label in labels]

    monkeypatch.setattr(exam_review, "_recognize_crops", fake_recognize)
    report = extract_answer_card([tmp_path / "card.jpg"])
    assert report["student_id"] == "202619240110"
    assert report["text_fields"]["64"]["text"] == "先分析复杂度\nreturn answer;"
    assert report["text_fields"]["64"]["confidence"] == 0.96
    assert list(exam_review.QUESTION_ORDER[-4:]) == ["61", "62", "63", "64"]
    assert not any("(" in question for question in exam_review.QUESTION_ORDER)


def test_objective_manual_review_corrects_and_summarizes(monkeypatch):
    monkeypatch.setattr(exam_review, "extract_answer_card", lambda paths, image_dir=None: {
        "objective": {"1": "B", "16": "AC", "21": "T"}, "objective_images": {"1": "q01.png"},
        "text_fields": {}, "crop_paths": {}, "ocr_errors": [], "alignment_scores": [], "student_id": "",
    })
    report = exam_review._build_review_from_maps(
        {"1": "1. 选择题"}, {"1": "A", "16": "CA", "21": "F"}, {}, ["card.png"]
    )
    original = {entry["question"]: entry for entry in report["objective"]}
    assert original["1"]["bubble_image"] == "q01.png"
    assert original["16"]["auto_status"] == "自动通过"
    assert report["objective_summary"]["auto_pass"] == 1
    updated = exam_review.apply_manual_review(report, [], [
        {"question": "1", "override_answer": True, "answer": "A", "status": ""},
        {"question": "16", "override_answer": False, "status": "不通过"},
        {"question": "21", "override_answer": True, "answer": "", "status": ""},
    ])
    decisions = {entry["question"]: entry for entry in updated["objective"]}
    assert decisions["1"]["recognized"] == "B"
    assert decisions["1"]["final_answer"] == "A"
    assert decisions["1"]["final_status"] == "通过"
    assert decisions["16"]["final_status"] == "不通过"
    assert decisions["21"]["final_answer"] == ""
    assert decisions["21"]["final_status"] == "不通过"
    assert updated["objective_summary"]["reviewed"] == 3
    assert updated["objective_summary"]["corrected"] == 2
    assert updated["objective_summary"]["final_fail"] == 2
    updated = exam_review.apply_manual_review(updated, [], [
        {"question": "1", "override_answer": False, "answer": "", "status": ""},
    ])
    assert updated["objective"][0]["final_answer"] == "B"
    assert updated["objective"][0]["final_status"] == "需人工复核"


def test_objective_review_validates_answer_and_saves_preview(tmp_path):
    import numpy as np
    import pytest

    image = np.full((1684, 1190), 255, dtype=np.uint8)
    paths = exam_review._save_objective_crops(image, tmp_path)
    assert len(paths) == 30
    assert (tmp_path / "objective" / "q01.png").is_file()
    assert (tmp_path / "objective" / "q30.png").is_file()
    assert exam_review._normalize_objective_correction("16", "DA") == "AD"
    assert exam_review._normalize_objective_correction("21", "正确") == "T"
    report = {"objective": [{"question": "1", "recognized": "B", "expected": "A", "auto_status": "需人工复核"}]}
    with pytest.raises(ValueError, match="修正答案格式错误"):
        exam_review.apply_manual_review(report, [], [{"question": "1", "override_answer": True, "answer": "AB"}])
    with pytest.raises(ValueError, match="客观题题号无效"):
        exam_review.apply_manual_review(report, [], [{"question": "99", "status": "通过"}])


def test_structured_scores_are_calculated_and_recalculated(monkeypatch):
    exam = [{"name": "计分", "questions": [
        {"id": "1", "type": "single", "title": "1. 单选", "score": 2, "answer": "A"},
        {"id": "2", "type": "single", "title": "2. 单选", "score": 3, "answer": "B"},
        {"id": "31-32", "type": "blank", "title": "31-32. 填空", "score": 4,
         "question_ids": ["31", "32"], "score_map": {"31": 1, "32": 3},
         "answer": {"31": "x", "32": "y"}},
    ]}]
    monkeypatch.setattr(exam_review, "extract_answer_card", lambda paths, image_dir=None: {
        "objective": {"1": "A", "2": "C"}, "objective_images": {},
        "text_fields": {"31": {"text": "x", "confidence": .98}, "32": {"text": "wrong", "confidence": .98}},
        "crop_paths": {}, "ocr_errors": [], "alignment_scores": [], "student_id": "",
    })
    review = exam_review.build_review_from_structured(exam, {}, ["card.png"])
    objective = {item["question"]: item for item in review["objective"]}
    text = {item["question"]: item for item in review["items"]}
    assert objective["1"]["score"] == 2
    assert objective["2"]["score"] == 3
    assert text["31"]["score"] == 1
    assert text["32"]["score"] == 3
    assert review["score_summary"] == {
        "total_score": 3, "possible_score": 9,
        "objective_score": 2, "objective_possible": 5,
        "text_score": 1, "text_possible": 4,
        "pending_score": 6, "failed_score": 0,
    }
    updated = exam_review.apply_manual_review(
        review,
        [{"question": "32", "status": "通过", "text": "y"}],
        [{"question": "2", "override_answer": True, "answer": "B", "status": ""}],
    )
    assert updated["score_summary"]["total_score"] == 9
    assert updated["score_summary"]["pending_score"] == 0
    assert updated["score_summary"]["objective_score"] == 5
    assert updated["score_summary"]["text_score"] == 4


def test_question_39_visual_crop_is_expanded(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    from types import SimpleNamespace

    page = np.full((1684, 1190), 255, dtype=np.uint8)
    monkeypatch.setattr(exam_review, "_load_page", lambda path: [("page", page)])
    monkeypatch.setattr(exam_review, "_align_and_order_pages", lambda images: (images, []))
    monkeypatch.setattr(exam_review, "_reference_pages", lambda: [])
    monkeypatch.setattr(exam_review, "_recognize_crops", lambda crops, labels: [
        SimpleNamespace(text="", confidence=0.0, error=None) for _ in labels
    ])
    report = exam_review.extract_answer_card([tmp_path / "card.pdf"], image_dir=tmp_path / "crops")
    q38 = cv2.imread(report["crop_paths"]["38"], cv2.IMREAD_GRAYSCALE)
    q39 = cv2.imread(report["crop_paths"]["39"], cv2.IMREAD_GRAYSCALE)
    assert q39.shape[1] > q38.shape[1]
    assert q39.shape[0] > q38.shape[0]

def test_refresh_rule_judgments_corrects_legacy_false_pass():
    review = {
        "items": [{
            "question": "36",
            "expected_answer": "arr[j] > arr[j+1]",
            "recognized_text": "arr[j] > arr[j-1]",
            "confidence": 0.94,
            "auto_status": "自动通过",
            "reason": "答案相似度1.00",
            "manual_status": "待复核",
            "ai_status": "AI待处理",
            "score": 1,
        }],
        "objective": [],
        "review_summary": {"total": 1, "auto_pass": 1, "manual": 0},
        "score_summary": {"total_score": 1, "possible_score": 1},
    }
    assert exam_review.refresh_rule_judgments(review) is True
    assert review["items"][0]["auto_status"] == "不通过"
    assert review["items"][0]["awarded_score"] == 0
    assert review["review_summary"]["auto_pass"] == 0
    assert review["score_summary"]["total_score"] == 0

def test_ocr_recognizer_is_reused_inside_batch_worker(monkeypatch):
    import threading
    from types import SimpleNamespace
    import numpy as np
    import src.ocr as ocr_module

    loads = []

    class FakeRecognizer:
        def recognize(self, images, labels):
            return [SimpleNamespace(text=label, confidence=1.0, error=None) for label in labels]

    def fake_create(params):
        loads.append(params.model_name)
        return FakeRecognizer()

    monkeypatch.setattr(ocr_module, "create_text_recognizer", fake_create)
    monkeypatch.setattr(exam_review, "_OCR_THREAD_LOCAL", threading.local())
    image = np.full((20, 80), 255, dtype=np.uint8)
    first = exam_review._recognize_crops([image], ["31"])
    second = exam_review._recognize_crops([image], ["32"])
    assert first[0].text == "31"
    assert second[0].text == "32"
    assert loads == ["PP-OCRv6_medium_rec"]



def test_correction_without_line_number_or_content_is_direct_fail():
    expected = "6. if (*(arr + i) > *max)"
    empty = judge_answer(expected, "", 0.0, question="47")
    assert empty[0] == "不通过"
    assert "行号" in empty[1]


def test_correction_questions_require_matching_line_number_and_content():
    expected = "6. if (*(arr + i) > *max)"
    assert judge_answer(expected, "(6) if (*(arr + i) > *max)", 0.99, question="47")[0] == "自动通过"

    missing_line = judge_answer(expected, "if (*(arr + i) > *max)", 0.99, question="47")
    assert missing_line[0] == "不通过"
    assert "行号" in missing_line[1]

    wrong_line = judge_answer(expected, "(8) if (*(arr + i) > *max)", 0.99, question="47")
    assert wrong_line[0] == "不通过"
    assert "应为第6行" in wrong_line[1]

    wrong_content = judge_answer(expected, "(6) if (*(arr + i) > max)", 0.99, question="47")
    assert wrong_content[0] == "不通过"
    assert "变量、下标、数值或运算符" in wrong_content[1]

    assert judge_answer("return *max;", "return *max;", 0.99, question="31")[0] == "自动通过"


def test_correction_reference_line_is_inferred_and_alternatives_are_accepted():
    source = """46-48. 指针找最大值：修正 3 处错误
1 int findMax(int *arr, int n)
2 {
3 int *max = arr[0];
4 for (int i = 1; i < n; i++)
5 {
6 if (*(arr + i) > max)
7 {
8 max = arr + i;
9 }
10 }
11 return max;
12 }
"""
    expected = "int *max = arr; 或 int *max = &arr[0];"
    normalized = exam_review._normalize_correction_expected("46", expected, source)
    assert normalized.startswith("3. ")
    assert judge_answer(normalized, "(3)int *max = arr;", 0.99, question="46")[0] == "自动通过"


def test_correction_reference_lines_are_inferred_for_all_unnumbered_and_numbered_code():
    numbered = """49-53. 斐波那契数列
1 int fib(int n)
2 {
3 if (n == 1) return 0;
4 if (n == 2) return 2;
5 int a = 1, b = 1;
6 for (int i = 3; i < n; i++)
7 {
8 int c = a - b;
9 a = b;
10 b = c;
11 }
12 return a;
13 }
"""
    assert exam_review._normalize_correction_expected("49", "if (n == 1) return 1;", numbered).startswith("3. ")
    assert exam_review._normalize_correction_expected("50", "if (n == 2) return 1;", numbered).startswith("4. ")
    assert exam_review._normalize_correction_expected("51", "for (int i = 3; i <= n; i++)", numbered).startswith("6. ")
    assert exam_review._normalize_correction_expected("52", "int c = a + b;", numbered).startswith("8. ")
    assert exam_review._normalize_correction_expected("53", "return b;", numbered).startswith("12. ")

    unnumbered = """59. 圆面积计算
#include <stdio.h>
#define PI 3.14159
int main(){
    int r;
    scanf("%d", &r);
    int area = PI * r * r;
    printf("%.2lf\n", area);
    return 0;
}
"""
    assert exam_review._normalize_correction_expected("59", "double area = PI * r * r;", unnumbered).startswith("6. ")


def test_refresh_rule_judgments_repairs_saved_correction_reference_line():
    source = """46-48. 指针找最大值
1 int findMax(int *arr, int n)
2 {
3 int *max = arr[0];
4 for (int i = 1; i < n; i++)
5 {
6 if (*(arr + i) > max)
7 {
8 max = arr + i;
9 }
10 }
11 return max;
12 }
"""
    review = {
        "items": [{
            "question": "46",
            "source_content": source,
            "expected_answer": "int *max = arr; 或 int *max = &arr[0];",
            "recognized_text": "(3)int *max = arr;",
            "confidence": 0.99,
            "auto_status": "需人工复核",
            "reason": "改错题参考答案需要同时提供行号和改错内容",
            "score": 1,
        }],
        "objective": [],
    }
    assert exam_review.refresh_rule_judgments(review) is True
    assert review["items"][0]["expected_answer"].startswith("3. ")
    assert review["items"][0]["auto_status"] == "自动通过"


def test_manual_pass_for_correction_allows_empty_text_and_validates_supplied_text():
    review = {
        "items": [{
            "question": "48", "expected_answer": "11. return *max;",
            "recognized_text": "return *max;", "ai_visual_text": "11. return [涂改处] *max;",
            "confidence": 0.99, "auto_status": "不通过", "manual_status": "待复核",
            "score": 1,
        }],
        "objective": [],
    }
    updated = exam_review.apply_manual_review(
        review, [{"question": "48", "status": "通过", "text": ""}]
    )
    assert updated["items"][0]["manual_status"] == "通过"
    assert updated["items"][0]["manual_text"] == ""
    assert updated["score_summary"]["total_score"] == 1

    with pytest.raises(ValueError, match="行号"):
        exam_review.apply_manual_review(
            {"items": [{**review["items"][0], "manual_status": "待复核", "manual_text": ""}], "objective": []},
            [{"question": "48", "status": "通过", "text": "return *max;"}],
        )

    validated = exam_review.apply_manual_review(
        {"items": [{**review["items"][0], "manual_status": "待复核", "manual_text": ""}], "objective": []},
        [{"question": "48", "status": "通过", "text": "11. return *max;"}],
    )
    assert validated["items"][0]["manual_status"] == "通过"
    assert validated["score_summary"]["total_score"] == 1


def test_structured_review_routes_to_marked_b_paper(monkeypatch, tmp_path):
    exam = {
        "papers": {
            "A": [{"name": "选择题", "questions": [{"id": "1", "type": "single", "title": "A卷第1题", "score": 2}]}],
            "B": [{"name": "选择题", "questions": [{"id": "1", "type": "single", "title": "B卷第1题", "score": 3}]}],
            "C": [{"name": "选择题", "questions": [{"id": "1", "type": "single", "title": "C卷第1题", "score": 4}]}],
        }
    }
    answers = {
        "papers": {
            "A": {"answer_map": {"1": "A"}},
            "B": {"answer_map": {"1": "B"}},
            "C": {"answer_map": {"1": "C"}},
        }
    }
    card = {
        "paper_type": "B",
        "paper_type_status": "自动识别",
        "text_fields": {str(number): {"text": "", "confidence": 0.0} for number in range(31, 65)},
        "objective": {"1": "B"},
        "objective_flags": {},
        "objective_images": {},
        "crop_paths": {},
        "ocr_errors": [],
        "alignment_scores": [],
        "crop_adjustments": {},
        "student_id": "",
    }
    monkeypatch.setattr(exam_review, "extract_answer_card", lambda *args, **kwargs: card)
    report = exam_review.build_review_from_structured(
        json.dumps(exam, ensure_ascii=False),
        json.dumps(answers, ensure_ascii=False),
        [tmp_path / "card.png"],
        image_dir=tmp_path / "crops",
    )
    objective = next(item for item in report["objective"] if item["question"] == "1")
    assert report["answer_paper_type"] == "B"
    assert report["answer_selection_status"] == "已按答题卡标记选择B卷答案"
    assert objective["expected"] == "B"
    assert objective["score"] == 3
    assert objective["final_status"] == "自动通过"


def test_structured_review_without_paper_type_keeps_variant_answers_pending(monkeypatch, tmp_path):
    exam = {
        "papers": {
            "A": [{"name": "选择题", "questions": [{"id": "1", "type": "single", "title": "A卷第1题", "score": 2}]}],
            "B": [{"name": "选择题", "questions": [{"id": "1", "type": "single", "title": "B卷第1题", "score": 3}]}],
        }
    }
    answers = {"papers": {"A": {"answer_map": {"1": "A"}}, "B": {"answer_map": {"1": "B"}}}}
    card = {
        "paper_type": "",
        "paper_type_status": "待复核",
        "text_fields": {str(number): {"text": "", "confidence": 0.0} for number in range(31, 65)},
        "objective": {"1": "A"},
        "objective_flags": {},
        "objective_images": {},
        "crop_paths": {},
        "ocr_errors": [],
        "alignment_scores": [],
        "crop_adjustments": {},
        "student_id": "",
    }
    monkeypatch.setattr(exam_review, "extract_answer_card", lambda *args, **kwargs: card)
    report = exam_review.build_review_from_structured(
        json.dumps(exam, ensure_ascii=False),
        json.dumps(answers, ensure_ascii=False),
        [tmp_path / "card.png"],
        image_dir=tmp_path / "crops",
    )
    objective = next(item for item in report["objective"] if item["question"] == "1")
    assert report["answer_paper_type"] == ""
    assert "未识别试卷类型" in report["answer_selection_status"]
    assert objective["expected"] == ""
    assert objective["final_status"] == "需人工复核"


def test_saved_review_score_refresh_uses_selected_paper_variant(monkeypatch, tmp_path):
    import_root = tmp_path / "imports"
    import_id = "variants"
    folder = import_root / import_id
    folder.mkdir(parents=True)
    imported = import_exam_and_answers(
        {"papers": {
            "A": [{"name": "选择", "questions": [{"id": "1", "type": "single", "title": "A题", "score": 2, "answer": "A"}]}],
            "B": [{"name": "选择", "questions": [{"id": "1", "type": "single", "title": "B题", "score": 7, "answer": "B"}]}],
        }}
    )
    (folder / "normalized_exam.json").write_text(json.dumps(imported, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", import_root)
    review = {
        "import_id": import_id,
        "answer_paper_type": "B",
        "objective": [{"question": "1", "auto_status": "自动通过", "final_status": "自动通过", "score": 0}],
        "items": [],
        "score_summary": {"possible_score": 0},
    }
    assert scan_ui._ensure_review_scores(review) is True
    assert review["objective"][0]["score"] == 7
    assert review["score_summary"]["possible_score"] == 7


def test_old_correction_ai_judgment_requests_image_recheck_once():
    review = {"items": [{"question": "55", "expected_answer": '6. scanf("%s", name);',
        "recognized_text": '(b) scanf("%s", name);', "confidence": 0.95,
        "ai_status": "AI不通过", "ai_reason": "缺少行号", "ai_visual_text": 'scanf("%s", name);',
        "score": 1, "manual_status": "待复核"}], "objective": []}
    assert exam_review.refresh_rule_judgments(review)
    item = review["items"][0]
    assert item["ai_status"] == "AI需复核"
    assert item["ai_previous_judgment"]["ai_status"] == "AI不通过"
    assert review["score_summary"]["pending_score"] == 1
    assert exam_review.refresh_rule_judgments(review) is False


@pytest.mark.parametrize("manual,confirmed", [("通过", False), ("不通过", False), ("待复核", True)])
def test_visual_policy_refresh_preserves_confirmed_or_manual_grades(manual, confirmed):
    review = {"grade_confirmed": confirmed, "items": [{
        "question": "55", "expected_answer": '6. scanf("%s", name);',
        "recognized_text": '(b) scanf("%s", name);', "confidence": 0.95,
        "ai_status": "AI不通过", "score": 1, "manual_status": manual,
    }], "objective": []}
    exam_review.refresh_rule_judgments(review)
    assert review["items"][0]["ai_status"] == "AI不通过"
    assert review["items"][0]["manual_status"] == manual


def test_run_ai_review_persists_image_evidence_and_policy(monkeypatch, tmp_path):
    import copy
    evidence = {"visual_text": '(6) scanf("%s", name);', "confidence": 0.93,
                "image_status": "clear", "line_number_status": "present"}
    saved = {"ok": True, "items": [{"question": "55", "handwriting_urls": []}], "objective": []}
    path = tmp_path / "review.json"
    monkeypatch.setattr(scan_ui, "_load_review", lambda ident: (ident, path, copy.deepcopy(saved)))
    monkeypatch.setattr(scan_ui, "_ensure_review_scores", lambda review: False)
    def apply(review, progress_callback=None):
        review["items"][0].update({"expected_answer": '6. scanf("%s", name);',
            "ai_status": "AI通过", "ai_visual_text": evidence["visual_text"],
            "ai_review_policy": exam_review.AI_REVIEW_POLICY, "ai_visual_evidence": evidence})
        review["ai_judgment"] = {"status": "已完成"}
        review["review_summary"] = {"ai_pass": 1}
        return review
    monkeypatch.setattr(scan_ui, "apply_ai_review", apply)
    def write(path, review):
        saved.clear()
        saved.update(copy.deepcopy(review))
    monkeypatch.setattr(scan_ui, "_write_review", write)
    result = scan_ui.run_ai_review({"review_id": "image-primary"})
    for report in (saved, result):
        item = report["items"][0]
        assert item["ai_review_policy"] == exam_review.AI_REVIEW_POLICY
        assert item["ai_visual_evidence"] == evidence
        assert item["ai_visual_text"].startswith("(6)")
        assert item["expected_answer"].startswith("6.")
