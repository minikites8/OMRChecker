import json
import threading
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

import backend.app as backend_app
import exam_review
import scan_ui


def _item(question, status="AI需复核", score=2):
    return {
        "question": str(question), "source_content": "题干", "expected_answer": "42",
        "recognized_text": "42", "confidence": 0.9, "auto_status": "待复核",
        "manual_status": "", "manual_text": "", "score": score,
        "ai_status": status, "ai_confidence": 0.2, "ai_visual_text": "旧识别",
        "ai_reason": "旧原因", "ai_corrected_answer": "旧答案",
        "ai_review_policy": "old-policy", "ai_visual_evidence": {"old": True},
    }


def test_apply_ai_review_question_only_updates_target_and_recalculates_score(monkeypatch):
    review = {"items": [_item("31", score=2), _item("32", status="AI不通过", score=3)], "objective": []}
    original = {key: review["items"][1].get(key) for key in (
        "ai_status", "ai_confidence", "ai_visual_text", "ai_reason", "ai_corrected_answer",
        "ai_review_policy", "ai_visual_evidence",
    )}

    def fake_judge(items):
        assert [item["question"] for item in items] == ["31"]
        return {"status": "已完成", "enabled": True, "processed": 1, "message": "ok", "results": {
            "31": {"status": "AI通过", "confidence": 0.99, "visual_text": "42",
                    "reason": "新识别", "corrected_answer": "42", "policy": "new-policy"}
        }}

    monkeypatch.setattr(exam_review, "judge_handwritten_items", fake_judge)
    result = exam_review.apply_ai_review_question(review, "31")
    assert result["items"][0]["ai_status"] == "AI通过"
    assert result["items"][0]["ai_visual_text"] == "42"
    assert {key: result["items"][1].get(key) for key in original} == original
    assert result["score_summary"]["total_score"] == 2
    assert result["score_summary"]["possible_score"] == 5
    assert result["score_summary"]["text_score"] == 2
    assert result["ai_question_judgment"]["question"] == "31"


def _write_review(root, review_id="single-review"):
    path = root / review_id / "output" / "review.json"
    path.parent.mkdir(parents=True)
    review = {"review_id": review_id, "items": [_item("31"), _item("32", status="AI通过", score=3)],
              "objective": [], "score_summary": {"possible_score": 5}, "grade_confirmed": True}
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return review_id


def test_run_ai_question_review_merges_only_target_and_resets_confirmation(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    review_id = _write_review(scan_ui.REVIEW_ROOT, "merge-review")

    def fake_apply(review, question):
        assert question == "31"
        target = next(item for item in review["items"] if item["question"] == "31")
        target.update({"ai_status": "AI通过", "ai_confidence": 0.98, "ai_visual_text": "新识别",
                       "ai_reason": "重识别", "ai_corrected_answer": "42",
                       "ai_review_policy": "new-policy", "ai_visual_evidence": {"image_status": "clear"},
                       "expected_answer": "42", "final_status": "通过", "score_basis": "AI审核",
                       "awarded_score": 2})
        review["ai_question_judgment"] = {"question": "31", "status": "已完成", "message": "ok"}
        return review

    monkeypatch.setattr(scan_ui, "apply_ai_review_question", fake_apply)
    result = scan_ui.run_ai_question_review({"review_id": review_id, "question": "31"})
    saved = scan_ui._load_review(review_id)[2]
    assert result["items"][0]["ai_visual_text"] == "新识别"
    assert saved["items"][1]["ai_visual_text"] == "旧识别"
    assert saved["grade_confirmed"] is False
    assert saved["grade_confirmation_status"] in {"待确认", "待处理"}
    assert saved["ai_question_judgment"]["question"] == "31"


def test_local_single_question_endpoint_uses_question_payload(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    review_id = _write_review(scan_ui.REVIEW_ROOT)
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: False)
    captured = {}

    def fake_run(payload):
        captured.update(payload)
        return {"ok": True, "review_id": review_id, "items": [], "objective": [],
                "ai_question_judgment": {"question": payload["question"], "status": "已完成"}}

    monkeypatch.setattr(scan_ui, "run_ai_question_review", fake_run)
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        request = Request(
            "http://127.0.0.1:{}/api/review/ai-judge-question".format(server.server_address[1]),
            data=json.dumps({"review_id": review_id, "question": "32"}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert response.status == 200
        assert captured == {"review_id": review_id, "question": "32"}
        assert payload["ai_question_judgment"]["question"] == "32"
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def test_fastapi_single_question_endpoint_forwards_question(monkeypatch):
    from fastapi.testclient import TestClient
    captured = {}

    def fake_start(payload):
        captured.update(payload)
        return {"ok": True, "review_id": "fastapi-review", "ai_question_judgment": {
            "question": payload["question"], "status": "已完成"}}

    monkeypatch.setattr(backend_app.legacy, "start_ai_question_review", fake_start)
    response = TestClient(backend_app.app).post(
        "/api/review/ai-judge-question", json={"review_id": "fastapi-review", "question": "64(1)"}
    )
    assert response.status_code == 200
    assert captured["question"] == "64(1)"
    assert response.json()["ai_question_judgment"]["question"] == "64(1)"
