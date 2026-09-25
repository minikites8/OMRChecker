import json
import sys
import threading
import pytest
from pathlib import Path
from urllib.request import Request, urlopen

import main
import scan_ui


def test_safe_filename_preserves_supported_extension():
    assert scan_ui.safe_filename(r"..\试卷?.JPG") == "试卷_.jpg"


def test_copy_template_assets_includes_reference(tmp_path):
    scan_ui.copy_template_assets(tmp_path)
    assert (tmp_path / "template.json").is_file()
    assert (tmp_path / "config.json").is_file()
    assert (tmp_path / "reference_blank.png").is_file()


def test_health_endpoint_returns_json():
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:{}/api/health".format(server.server_address[1])
        with urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["ok"] is True
        assert "ai_judgment" in payload
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_sheet_endpoint_creates_downloadable_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, "SHEETS_ROOT", tmp_path)
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:{}/api/sheets".format(server.server_address[1])
        request = Request(
            url,
            data=json.dumps(
                {
                    "title": "Web UI Sheet",
                    "mcq_count": 3,
                    "tf_count": 2,
                    "text_count": 1,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["ok"] is True
        assert payload["pdf_url"].endswith("omr_custom_sheet.pdf")
        assert payload["package_url"].endswith("omr_custom_sheet_package.zip")
        assert len(list(tmp_path.glob("*/omr_custom_sheet.pdf"))) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_save_uploads_decodes_base64(tmp_path):
    files = [{"name": "answer.png", "data": "data:image/png;base64,iVBORw0KGgo="}]
    saved = scan_ui.save_uploads(files, tmp_path)
    assert saved == [tmp_path / "answer.png"]
    assert saved[0].read_bytes().startswith(b"\x89PNG")


def test_main_accepts_ui_flag(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["main.py", "--ui"])
    assert main.parse_args()["ui"] is True


def test_ui_prompts_are_chinese_and_support_twelve_digits():
    html = (scan_ui.UI_ROOT / "index.html").read_text(encoding="utf-8")
    javascript = (scan_ui.UI_ROOT / "app.js").read_text(encoding="utf-8")
    assert "阅卷工作空间" in html
    assert "查看处理日志" not in html
    assert "STEP 1" not in html
    assert "<option selected>12</option>" in html
    assert "文件名" in javascript
    assert "气泡学号" in javascript
    assert "导入试卷和答案信息" in html
    assert "/api/exam/import" in javascript
    assert "AI判断手写内容" in html
    assert "/api/review/ai-judge" in javascript
    assert "扫描识别：" in javascript
    assert "需要人工核对" in javascript
    assert "objectiveNeedsReview" in javascript
    assert 'id="reviewScoreBoard"' in html
    assert 'id="reviewTotalScore"' in html
    assert "renderScoreBoard" in javascript
    assert "review-score-badge" in javascript
    assert "得分 '+formatScore(score)" in javascript
    assert "buildImportedScoreMap" in javascript
    assert "loadReviewScoreMap" in javascript
    assert 'id="reviewConcurrency"' in html
    assert 'id="reviewTemplateSelect"' in html
    assert 'id="scanTemplateSelect"' in html
    assert 'data-view="templates"' in html
    assert '/api/templates' in (scan_ui.UI_ROOT / "templates.js").read_text(encoding="utf-8")
    assert 'id="reviewBatchPanel"' in html
    assert "/api/review/batch" in javascript
    assert "groupReviewFiles" in javascript
    assert "restoreActiveReview" in javascript
    assert "omrActiveReviewId" in javascript
    assert "aiPanelPinned" in javascript
    assert "resetAiProgress(clearPinned=true)" in javascript
    assert "updateAiProgress({status:'处理中'" in javascript
    assert "/reviews/latest.json" in javascript
    assert 'id="reviewAiProgress"' in html
    assert 'id="reviewAiTrack"' in html
    assert "已完成 '+done+'/'+total+' 组" in javascript
    assert "aria-valuenow" in javascript



def test_ai_judge_endpoint_updates_saved_review(tmp_path, monkeypatch):
    review_id = "review-ai"
    review_path = tmp_path / review_id / "output" / "review.json"
    review_path.parent.mkdir(parents=True)
    image_path = review_path.parent / "handwriting" / "31.png"
    image_path.parent.mkdir()
    image_path.write_bytes(b"test-image")
    review_path.write_text(json.dumps({
        "ok": True,
        "items": [{"question": "31", "handwriting_urls": ["/reviews/review-ai/output/handwriting/31.png"]}],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path)
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: True)
    started, release = threading.Event(), threading.Event()

    def fake_apply(review, progress_callback=None):
        assert review["items"][0]["handwriting_images"] == [str(image_path.resolve())]
        progress_callback({"phase": "准备中", "group_count": 1, "completed_groups": 0,
                           "current_group": "", "processed": 0, "error_groups": 0})
        progress_callback({"phase": "审核中", "group_count": 1, "completed_groups": 0,
                           "current_group": "31", "processed": 0, "error_groups": 0})
        started.set()
        assert release.wait(5)
        progress_callback({"phase": "本组完成", "group_count": 1, "completed_groups": 1,
                           "current_group": "31", "processed": 1, "error_groups": 0})
        review["items"][0]["ai_status"] = "AI通过"
        review["items"][0]["final_status"] = "通过"
        review["items"][0]["awarded_score"] = 2
        review["ai_judgment"] = {"status": "已完成", "processed": 1, "group_count": 1,
                                 "completed_groups": 1, "current_group": ""}
        review["review_summary"] = {"total": 1, "ai_pass": 1}
        review["score_summary"] = {"total_score": 2, "possible_score": 2, "pending_score": 0}
        return review

    monkeypatch.setattr(scan_ui, "apply_ai_review", fake_apply)
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:{}".format(server.server_address[1])
        url = base + "/api/review/ai-judge"
        request = Request(url, data=json.dumps({"review_id": review_id}).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["ai_judgment"]["status"] == "处理中"
        assert started.wait(2)
        with urlopen(base + "/api/review/status?review_id=" + review_id, timeout=5) as status_response:
            progress = json.loads(status_response.read().decode("utf-8"))
        assert progress["ai_judgment"]["group_count"] == 1
        assert progress["ai_judgment"]["completed_groups"] == 0
        assert progress["ai_judgment"]["current_group"] == "31"
        assert "31" in progress["ai_judgment"]["message"]
        release.set()
        for _ in range(40):
            with urlopen(base + "/api/review/status?review_id=" + review_id, timeout=5) as status_response:
                saved = json.loads(status_response.read().decode("utf-8"))
            if saved["ai_judgment"]["status"] == "已完成":
                break
            threading.Event().wait(0.05)
        assert saved["ai_judgment"]["processed"] == 1
        assert saved["ai_judgment"]["completed_groups"] == 1
        assert saved["items"][0]["ai_status"] == "AI通过"
        assert saved["items"][0]["final_status"] == "通过"
        assert saved["items"][0]["awarded_score"] == 2
        assert saved["score_summary"]["total_score"] == 2
        assert "handwriting_images" not in saved["items"][0]
        assert saved["items"][0]["handwriting_urls"]
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_exam_import_endpoint_accepts_pasted_text(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    exam = [{"name": "选择题", "description": "每题2分", "total_score": 2, "questions": [{"type": "single", "title": "1. 选项", "score": 2, "description": "A. 甲\nB. 乙"}]}]
    answer = {"answer_map": {"1": "A"}}
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = "http://127.0.0.1:{}/api/exam/import".format(server.server_address[1])
        request = Request(url, data=json.dumps({"exam_text": json.dumps(exam, ensure_ascii=False), "answer_text": json.dumps(answer, ensure_ascii=False)}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert payload["ok"] is True
        assert payload["summary"]["question_count"] == 1
        assert payload["answer_map"] == {"1": "A"}
        assert (tmp_path / "imports" / payload["import_id"] / "normalized_exam.json").is_file()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_review_job_uses_imported_exam(tmp_path, monkeypatch):
    import base64
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    imported = scan_ui.run_exam_import({
        "exam_text": json.dumps([{"name": "选择", "questions": [{"type": "single", "title": "1. Item", "score": 2, "description": "A/B", "answer": "B"}]}]),
    })
    captured = {}
    def fake_build(exam, answers, cards, image_dir=None, answer_docx=None):
        captured["exam"] = exam
        captured["answers"] = answers
        captured["cards"] = cards
        return {"ok": True, "items": [], "objective": [], "source": {}, "review_summary": {}}
    monkeypatch.setattr(scan_ui, "build_review_from_structured", fake_build)
    photo = {"name": "card.png", "data": base64.b64encode(b"image").decode("ascii")}
    result = scan_ui.run_review_job({"import_id": imported["import_id"], "card_files": [photo]})
    assert result["ok"] is True
    assert captured["exam"]["sections"][0]["questions"][0]["title"] == "1. Item"
    assert captured["answers"]["answer_map"] == {"1": "B"}
    assert captured["cards"][0].is_file()


def test_legacy_review_status_backfills_scores_from_import(tmp_path, monkeypatch):
    import_root = tmp_path / "imports"
    review_root = tmp_path / "reviews"
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", import_root)
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", review_root)
    imported = scan_ui.run_exam_import({"exam_text": json.dumps([{
        "name": "选择题", "questions": [{"id": "1", "type": "single", "title": "1. 题目", "score": 2, "answer": "A"}]
    }], ensure_ascii=False)})
    review_id = "legacy-review"
    review_path = review_root / review_id / "output" / "review.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(json.dumps({
        "ok": True, "review_id": review_id, "import_id": imported["import_id"],
        "objective": [{"question": "1", "recognized": "A", "expected": "A", "auto_status": "自动通过", "final_status": "自动通过"}],
        "items": [], "review_summary": {}, "objective_summary": {},
    }, ensure_ascii=False), encoding="utf-8")
    result = scan_ui.read_review_status(review_id)
    assert result["objective"][0]["score"] == 2
    assert result["score_summary"]["total_score"] == 2
    assert result["score_summary"]["possible_score"] == 2
    saved = json.loads(review_path.read_text(encoding="utf-8"))
    assert saved["score_summary"]["total_score"] == 2

def test_batch_review_runs_multiple_cards_concurrently(tmp_path, monkeypatch):
    import base64
    import time

    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: False)
    imported = scan_ui.run_exam_import({
        "exam_text": json.dumps([{
            "name": "选择",
            "questions": [{"id": "1", "type": "single", "title": "1. Item", "score": 2, "answer": "A"}],
        }]),
    })
    state = {"active": 0, "maximum": 0}
    lock = threading.Lock()

    def fake_build(exam, answers, cards, image_dir=None, answer_docx=None):
        with lock:
            state["active"] += 1
            state["maximum"] = max(state["maximum"], state["active"])
        time.sleep(0.12)
        with lock:
            state["active"] -= 1
        return {
            "ok": True, "student_id": Path(cards[0]).stem, "paper_type": "A",
            "items": [], "objective": [], "source": {},
            "review_summary": {"total": 0}, "objective_summary": {"total": 0},
            "score_summary": {"total_score": 0, "possible_score": 0},
        }

    monkeypatch.setattr(scan_ui, "build_review_from_structured", fake_build)
    groups = []
    for index in range(4):
        groups.append({
            "label": "考生{}".format(index + 1),
            "files": [{
                "name": "card{}.pdf".format(index + 1),
                "data": base64.b64encode(b"pdf").decode("ascii"),
            }],
        })
    batch = scan_ui.start_batch_review({
        "import_id": imported["import_id"], "concurrency": 2, "card_groups": groups,
    })
    for _ in range(100):
        batch = scan_ui.read_batch_status(batch["batch_id"])
        if batch["status"] in {"已完成", "部分完成", "异常"}:
            break
        time.sleep(0.03)
    assert batch["status"] == "已完成"
    assert batch["completed"] == 4
    assert batch["failed"] == 0
    assert batch["concurrency"] == 2
    assert state["maximum"] >= 2
    assert all(entry["review_id"] for entry in batch["reviews"])


def test_batch_file_grouping_treats_each_pdf_as_one_card():
    files = [
        {"name": "one.pdf", "data": "x"},
        {"name": "two.pdf", "data": "x"},
        {"name": "three-1.jpg", "data": "x"},
        {"name": "three-2.jpg", "data": "x"},
    ]
    groups = scan_ui._normalize_card_groups({"card_files": files})
    assert [len(group["files"]) for group in groups] == [1, 1, 2]
    assert [group["label"] for group in groups[:2]] == ["one", "two"]

def test_batch_review_http_endpoint_reports_progress(tmp_path, monkeypatch):
    import base64
    import time

    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: False)
    imported = scan_ui.run_exam_import({
        "exam_text": json.dumps([{
            "name": "选择",
            "questions": [{"id": "1", "type": "single", "title": "1. Item", "score": 1, "answer": "A"}],
        }]),
    })

    def fake_build(exam, answers, cards, image_dir=None, answer_docx=None):
        time.sleep(0.03)
        return {
            "ok": True, "student_id": Path(cards[0]).stem, "paper_type": "B",
            "items": [], "objective": [], "source": {},
            "review_summary": {"total": 0}, "objective_summary": {"total": 0},
            "score_summary": {"total_score": 1, "possible_score": 1},
        }

    monkeypatch.setattr(scan_ui, "build_review_from_structured", fake_build)
    encoded = base64.b64encode(b"pdf").decode("ascii")
    payload = {
        "import_id": imported["import_id"], "concurrency": 2,
        "card_groups": [
            {"label": "甲", "files": [{"name": "a.pdf", "data": encoded}]},
            {"label": "乙", "files": [{"name": "b.pdf", "data": encoded}]},
        ],
    }
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:{}".format(server.server_address[1])
        request = Request(
            base + "/api/review/batch", data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=5) as response:
            batch = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        for _ in range(100):
            with urlopen(base + "/api/review/batch/status?batch_id=" + batch["batch_id"], timeout=5) as response:
                status = json.loads(response.read().decode("utf-8"))
            if status["status"] in {"已完成", "部分完成", "异常"}:
                break
            time.sleep(0.02)
        assert status["status"] == "已完成"
        assert status["completed"] == 2
        assert [entry["paper_type"] for entry in status["reviews"]] == ["B", "B"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_incomplete_review_report_is_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    path = scan_ui.REVIEW_ROOT / "review-1" / "output" / "review.json"
    path.parent.mkdir(parents=True)
    path.write_text("{\n  \"items\":", encoding="utf-8")
    with pytest.raises(scan_ui.ReviewDataUnavailable, match="复核结果正在生成"):
        scan_ui.read_review_status("review-1")
