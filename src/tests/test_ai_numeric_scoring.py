"""Numeric AI scoring across parsing, review, persistence and export."""
import json
import pytest
import ai_judge
import exam_review
from candidate_manager import grade_confirmation_blockers, question_score_records


def question(**changes):
    item = {"question": "61(1)", "score": 6, "expected_answer": "完整答案",
            "recognized_text": "已完成部分步骤", "confidence": 0.95,
            "auto_status": "需人工复核", "manual_status": "待复核"}
    return {**item, **changes}


def response(score=4, **changes):
    return {"question": "61(1)", "status": "partial", "awarded_score": score,
            "confidence": 0.95, "visual_text": "已完成部分步骤",
            "reason": "思路和前两步正确得4分，最后一步缺失扣2分", "corrected_answer": "", **changes}


def remote(monkeypatch, row):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-numeric-key")
    monkeypatch.setattr(ai_judge, "_request", lambda config, items: {
        "choices": [{"message": {"content": json.dumps({"results": [row]})}}]})


def test_prompt_sends_each_question_maximum():
    payload = ai_judge._prompt_items([question(), question(question="61(2)", score=3)])
    assert [row["max_score"] for row in payload] == [6, 3]
    assert "awarded_score" in ai_judge.SYSTEM_PROMPT
    assert "pass|partial|fail|review" in ai_judge.SYSTEM_PROMPT
    assert "得分点" in ai_judge.SYSTEM_PROMPT


@pytest.mark.parametrize("score", [0, 1, 2, 3, 4, 5, 6, 4.5])
def test_all_scores_flow_from_ai_to_totals_and_export(monkeypatch, score):
    remote(monkeypatch, response(score, status="pass"))
    review = exam_review.apply_ai_review({"items": [question()], "objective": []})
    item = review["items"][0]
    assert item["ai_score"] == score
    assert item["awarded_score"] == score
    assert item["final_status"] == ("通过" if score == 6 else "不通过" if score == 0 else "部分得分")
    assert item["score_basis"] == "AI审核"
    assert review["score_summary"]["total_score"] == score
    assert review["score_summary"]["failed_score"] == 6 - score
    assert review["score_summary"]["pending_score"] == 0
    assert grade_confirmation_blockers(review) == []
    record = question_score_records(review)[0]
    assert record["awarded_score"] == score
    assert record["status"] == item["final_status"]


@pytest.mark.parametrize("score", [-1, 6.01, float("nan"), float("inf"), True, False, None, "", " ", "bad", {}, []])
def test_invalid_scores_require_review(monkeypatch, score):
    remote(monkeypatch, response(score))
    review = exam_review.apply_ai_review({"items": [question()]})
    item = review["items"][0]
    assert item["ai_status"] == "AI需复核"
    assert item["ai_score"] is None
    assert item["awarded_score"] == 0
    assert review["score_summary"]["pending_score"] == 6
    assert grade_confirmation_blockers(review)


@pytest.mark.parametrize("changes", [{"status": "review"}, {"confidence": 0.4}])
def test_review_and_low_confidence_keep_score_pending(monkeypatch, changes):
    remote(monkeypatch, response(**changes))
    review = exam_review.apply_ai_review({"items": [question()]})
    assert review["items"][0]["ai_score"] is None
    assert review["score_summary"]["pending_score"] == 6


def test_score_only_and_numeric_string_response(monkeypatch):
    row = response("3.5")
    row.pop("status")
    remote(monkeypatch, row)
    review = exam_review.apply_ai_review({"items": [question()]})
    assert review["items"][0]["ai_score"] == 3.5
    assert review["items"][0]["final_status"] == "部分得分"


def test_partial_requires_a_numeric_score(monkeypatch):
    row = response(); row.pop("awarded_score")
    remote(monkeypatch, row)
    review = exam_review.apply_ai_review({"items": [question()]})
    assert review["items"][0]["ai_status"] == "AI需复核"
    assert review["score_summary"]["pending_score"] == 6


@pytest.mark.parametrize("status,awarded", [("pass", 6), ("fail", 0)])
def test_legacy_binary_response_remains_readable(monkeypatch, status, awarded):
    row = response(status=status); row.pop("awarded_score")
    remote(monkeypatch, row)
    review = exam_review.apply_ai_review({"items": [question()]})
    assert review["items"][0]["awarded_score"] == awarded


@pytest.mark.parametrize("status,awarded", [("通过", 6), ("不通过", 0)])
def test_manual_override_and_reset_preserve_ai_score(monkeypatch, status, awarded):
    remote(monkeypatch, response())
    review = exam_review.apply_ai_review({"items": [question()]})
    exam_review.apply_manual_review(review, [{"question": "61(1)", "status": status}])
    assert review["items"][0]["awarded_score"] == awarded
    assert review["items"][0]["ai_score"] == 4
    exam_review.apply_manual_review(review, [{"question": "61(1)", "status": "待复核"}])
    assert review["items"][0]["awarded_score"] == 4
    assert review["score_summary"]["pending_score"] == 0


def test_rerun_clears_previous_numeric_result(monkeypatch):
    remote(monkeypatch, response())
    review = exam_review.apply_ai_review({"items": [question()]})
    remote(monkeypatch, response(None, status="review"))
    exam_review.apply_ai_review(review)
    assert review["items"][0]["ai_score"] is None
    assert review["items"][0]["awarded_score"] == 0
    assert review["score_summary"]["pending_score"] == 6
    exam_review._apply_ai_review_result(review["items"][0], {"status": "已跳过", "results": {}})
    assert review["items"][0]["ai_score"] is None


@pytest.mark.parametrize("status,visual,line,expected", [
    ("uncertain", "部分步骤", "ambiguous", None),
    ("unavailable", "部分步骤", "ambiguous", None),
    ("blank", "", "missing", 0),
    ("blank", "部分步骤", "missing", None),
])
def test_visual_evidence_controls_numeric_awards(status, visual, line, expected):
    item = question()
    row = response(status="AI部分得分", visual_text=visual,
                   visual_evidence={"image_status": status, "visual_text": visual,
                                    "line_number_status": line, "confidence": 0.95})
    exam_review._apply_ai_review_result(item, {"results": {"61(1)": row}})
    assert item["ai_score"] == expected


def test_correction_requirements_apply_before_numeric_credit():
    item = question(question="55", expected_answer='(6) scanf("%s", name);')
    visual = 'scanf("%s", name);'
    row = response(status="AI部分得分", visual_text=visual,
                   visual_evidence={"image_status": "clear", "visual_text": visual,
                                    "line_number_status": "missing", "confidence": 0.95})
    exam_review._apply_ai_review_result(item, {"results": {"55": row}})
    assert item["ai_status"] == "AI不通过"
    assert item["ai_score"] == 0


@pytest.mark.parametrize("single", [False, True])
def test_background_merge_persists_numeric_scores(tmp_path, monkeypatch, single):
    import scan_ui
    remote(monkeypatch, response())
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path)
    review_id = "numeric-score"
    path = tmp_path / review_id / "output" / "review.json"
    path.parent.mkdir(parents=True)
    review = {"ok": True, "items": [question()], "objective": [], "review_id": review_id}
    review["score_summary"] = exam_review._score_summary(review)
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    payload = {"review_id": review_id, "question": "61(1)"}
    (scan_ui.run_ai_question_review if single else scan_ui.run_ai_review)(payload)
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["items"][0]["ai_score"] == 4
    assert saved["items"][0]["awarded_score"] == 4
    assert saved["score_summary"]["total_score"] == 4
    assert saved["score_summary"]["pending_score"] == 0
    assert grade_confirmation_blockers(saved) == []
    record = question_score_records(saved)[0]
    assert scan_ui._export_question_result(record)["score"] == 4


def test_mixed_scores_balance_total_pending_and_lost():
    review = {"objective": [{"score": 2, "final_status": "通过"}], "items": [
        question(ai_status="AI部分得分", ai_score=4),
        question(question="61(2)", ai_status="AI需复核"),
        question(question="62", ai_status="AI不通过", ai_score=0)]}
    score = exam_review._score_summary(review)
    assert score["total_score"] == 6
    assert score["possible_score"] == 20
    assert score["pending_score"] == 6
    assert score["failed_score"] == 8
    assert score["total_score"] + score["pending_score"] + score["failed_score"] == score["possible_score"]
