"""Regression coverage for image-confirmed blank answers."""
import json

import pytest

import ai_judge
import exam_review


def grade(monkeypatch, *, question="61(1)", image_status="blank", visual_text="", confidence=0.98,
          score=0, status="fail", recognized_text="", expected_answer="参考答案", evidence=None):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-blank-key")
    row = {"question": question, "status": status, "awarded_score": score,
           "confidence": confidence, "visual_text": visual_text, "reason": "作答留空，得0分",
           "corrected_answer": ""}
    if image_status is not None:
        row["image_status"] = image_status
    monkeypatch.setattr(ai_judge, "_request", lambda config, items: {
        "choices": [{"message": {"content": json.dumps({"results": [row]}, ensure_ascii=False)}}]})
    if evidence is not None:
        monkeypatch.setattr(ai_judge, "_read_visual_evidence", lambda config, items: {question: evidence})
    item = {"question": question, "score": 6, "expected_answer": expected_answer,
            "recognized_text": recognized_text, "confidence": 0.0,
            "handwriting_images": ["mock-answer-image.png"], "auto_status": "需人工复核",
            "manual_status": "待复核"}
    return exam_review.apply_ai_review({"items": [item], "objective": []})


def assert_zero(review):
    item = review["items"][0]
    assert item["ai_status"] == "AI不通过"
    assert item["ai_score"] == 0
    assert item["awarded_score"] == 0
    assert item["final_status"] == "不通过"
    assert review["score_summary"]["pending_score"] == 0
    assert review["score_summary"]["failed_score"] == 6


@pytest.mark.parametrize("question", ["31", "61(1)", "64"])
@pytest.mark.parametrize("recognized", ["", "OCR幻觉内容"])
def test_confirmed_blank_scores_zero(monkeypatch, question, recognized):
    review = grade(monkeypatch, question=question, recognized_text=recognized, expected_answer="")
    assert_zero(review)
    assert review["items"][0]["ai_visual_evidence"]["image_status"] == "blank"


@pytest.mark.parametrize("score,status", [(6, "pass"), (3, "partial"), (None, "review")])
def test_confirmed_blank_overrides_grader_score(monkeypatch, score, status):
    assert_zero(grade(monkeypatch, score=score, status=status))


@pytest.mark.parametrize("image_status,visual,confidence", [
    ("uncertain", "", 0.98), ("unavailable", "", 0.98),
    ("uncertain", "仍需辨认的笔迹", 0.98), ("unavailable", "旧转录", 0.98),
    ("clear", "", 0.98), (None, "", 0.98), ("unexpected", "", 0.98),
    ("blank", "实际笔迹", 0.98), ("blank", "", 0.4),
])
def test_inconclusive_or_conflicting_image_stays_pending(monkeypatch, image_status, visual, confidence):
    review = grade(monkeypatch, image_status=image_status, visual_text=visual, confidence=confidence)
    assert review["items"][0]["ai_status"] == "AI需复核"
    assert review["items"][0]["ai_score"] is None
    assert review["score_summary"]["pending_score"] == 6


@pytest.mark.parametrize("score,status", [(6, "pass"), (3, "partial"), (0, "fail")])
def test_written_answer_keeps_numeric_scoring(monkeypatch, score, status):
    review = grade(monkeypatch, image_status="clear", visual_text="实际手写步骤", score=score, status=status)
    assert review["items"][0]["ai_score"] == score
    assert review["score_summary"]["pending_score"] == 0


def test_correction_blank_uses_independent_evidence(monkeypatch):
    evidence = {"image_status": "blank", "visual_text": "", "confidence": 0.98,
                "line_number_status": "missing", "reason": "作答区域留空"}
    assert_zero(grade(monkeypatch, question="55", evidence=evidence))


def test_correction_ambiguous_evidence_keeps_priority(monkeypatch):
    evidence = {"image_status": "uncertain", "visual_text": "疑似笔迹", "confidence": 0.98,
                "line_number_status": "ambiguous", "reason": "字形待确认"}
    review = grade(monkeypatch, question="55", evidence=evidence)
    assert review["items"][0]["ai_status"] == "AI需复核"
    assert review["items"][0]["ai_visual_evidence"] == evidence


def test_prompts_define_blank_first_and_structured_evidence():
    assert "优先规则：作答留空直接给0分" in ai_judge.SYSTEM_PROMPT
    assert '"image_status":"clear|blank|uncertain|unavailable"' in ai_judge.SYSTEM_PROMPT
    assert "作答留空，得0分" in ai_judge.SYSTEM_PROMPT
    assert "空白判断优先于参考答案完整性检查" in ai_judge.SYSTEM_PROMPT
    assert 'visual_text=""' in ai_judge.VISUAL_TRANSCRIPTION_PROMPT
    assert "印刷题号" in ai_judge.VISUAL_TRANSCRIPTION_PROMPT
