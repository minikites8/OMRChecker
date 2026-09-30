import json
import itertools

import pytest

import ai_judge
import exam_review


def make_review(answers, expected=None, groups=None):
    expected = expected or ["3. x = 1;", "8. return x;"]
    items = []
    for index, (reference, visual) in enumerate(zip(expected, answers)):
        items.append({"question": str(46 + index), "score": 2,
                      "expected_answer": reference, "recognized_text": visual,
                      "source_content": "", "confidence": .99,
                      "auto_status": "需人工复核", "manual_status": "待复核",
                      "ai_group": groups[index] if groups else "correction:1",
                      "major_question": "1"})
    return {"items": items, "objective": []}


def result_for(items, progress_callback=None):
    return {"status": "已完成", "results": {
        item["question"]: {"status": "AI通过", "confidence": .99,
                           "visual_text": item["recognized_text"], "reason": "原图改错正确",
                           "corrected_answer": item["recognized_text"], "awarded_score": item["score"],
                           "visual_evidence": {"visual_text": item["recognized_text"], "confidence": .99,
                                               "image_status": "clear", "line_number_status": "present"}}
        for item in items}}


@pytest.mark.parametrize("answers", list(itertools.permutations(["3. x = 1;", "8. return x;", "12. y = 2;"])))
def test_same_question_corrections_accept_all_answer_orders(monkeypatch, answers):
    expected = ["3. x = 1;", "8. return x;", "12. y = 2;"]
    data = make_review(answers, expected)
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    assert [item["ai_status"] for item in data["items"]] == ["AI通过"] * 3
    assert data["score_summary"]["total_score"] == 6
    assert [item["expected_answer"] for item in data["items"]] == expected
    assert [item["ai_visual_evidence"]["matched_reference_question"] for item in data["items"]] == [str(46 + expected.index(answer)) for answer in answers]


def test_repeated_correction_receives_credit_once(monkeypatch):
    data = make_review(["8. return x;", "8. return x;"])
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    assert data["score_summary"]["total_score"] == 2
    assert sorted(item["ai_score"] for item in data["items"]) == [0, 2]


def test_answers_stay_within_their_parent_question(monkeypatch):
    data = make_review(["8. return x;", "3. x = 1;"], groups=["correction:1", "correction:2"])
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    assert data["score_summary"]["total_score"] == 0


def test_single_blank_retry_regrades_and_matches_the_whole_parent(monkeypatch):
    data = make_review(["8. return x;", "3. x = 1;"])
    data["items"][0].update(manual_status="不通过", manual_text="老师复核")
    calls = []
    def judge(items):
        calls.append([item["question"] for item in items])
        return result_for(items)
    monkeypatch.setattr(exam_review, "judge_handwritten_items", judge)
    exam_review.apply_ai_review_question(data, "47")
    assert calls == [["46", "47"]]
    assert [item["ai_status"] for item in data["items"]] == ["AI通过", "AI通过"]
    assert data["score_summary"]["total_score"] == 2
    assert data["items"][0]["manual_text"] == "老师复核"


def test_prompt_declares_unordered_one_to_one_correction_matching():
    assert "任意顺序作答" in ai_judge.SYSTEM_PROMPT
    assert "每个参考改错点最多计分一次" in ai_judge.SYSTEM_PROMPT


@pytest.mark.parametrize("answers,credited_question,reference", [
    (["", "3. x = 1;"], "47", "46"),
    (["8. return x;", ""], "46", "47"),
])
def test_correct_answer_in_the_other_blank_gets_credit(monkeypatch, answers, credited_question, reference):
    data = make_review(answers)
    def judge(items):
        result = result_for(items)
        for row in result["results"].values():
            if not row["visual_text"]:
                row["visual_evidence"].update(image_status="blank", line_number_status="missing")
        return result
    monkeypatch.setattr(exam_review, "judge_handwritten_items", judge)
    exam_review.apply_ai_review(data)
    credited = next(item for item in data["items"] if item["question"] == credited_question)
    assert credited["ai_score"] == 2
    assert credited["ai_visual_evidence"]["matched_reference_question"] == reference
    assert data["score_summary"]["total_score"] == 2
    assert data["score_summary"]["pending_score"] == 0


@pytest.mark.parametrize("visual,image_status,line_status,confidence,expected_status", [
    ("8. return x;", "clear", "ambiguous", .99, "AI需复核"),
    ("8. return x;", "uncertain", "present", .99, "AI需复核"),
    ("8. return x;", "clear", "present", .4, "AI需复核"),
    ("return x;", "clear", "missing", .99, "AI不通过"),
    ("return x;", "clear", "printed_only", .99, "AI不通过"),
    ("9. return x;", "clear", "present", .99, "AI不通过"),
    ("8. return y;", "clear", "present", .99, "AI需复核"),
])
def test_unordered_matching_retains_visual_line_and_content_checks(monkeypatch, visual, image_status, line_status, confidence, expected_status):
    data = make_review([visual, "3. x = 1;"])
    def judge(items):
        result = result_for(items)
        result["results"]["46"]["visual_evidence"].update(
            image_status=image_status, line_number_status=line_status, confidence=confidence)
        return result
    monkeypatch.setattr(exam_review, "judge_handwritten_items", judge)
    exam_review.apply_ai_review(data)
    assert data["items"][0]["ai_status"] == expected_status
    assert data["items"][0]["ai_score"] in (None, 0)
    assert data["items"][1]["ai_score"] == 2


def test_duplicate_reference_text_is_one_correction_point(monkeypatch):
    data = make_review(["3. x = 1;", "3. x = 1;"], expected=["3. x = 1;", "3. x=1;"])
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    assert data["score_summary"]["total_score"] == 2


def test_matching_uses_augmenting_paths_for_overlapping_alternatives(monkeypatch):
    data = make_review(["3. x = 1;", "3. x = 2;"], expected=["3. x = 1; 或 x = 2;", "3. x = 1;"])
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    assert data["score_summary"]["total_score"] == 4
    assert [item["ai_visual_evidence"]["matched_reference_question"] for item in data["items"]] == ["47", "46"]


def test_retry_clears_old_match_when_answer_changes(monkeypatch):
    data = make_review(["8. return x;", "3. x = 1;"])
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    data["items"][0]["recognized_text"] = "8. return y;"
    exam_review.apply_ai_review_question(data, "46")
    assert data["items"][0]["ai_score"] is None
    assert "matched_reference_question" not in data["items"][0]["ai_visual_evidence"]
    exam_review.refresh_rule_judgments(data)
    assert data["score_summary"]["total_score"] == 2


def test_pool_is_group_scoped_and_blind_read_contains_only_questions():
    data = make_review(["8. return x;", "3. x = 1;"])
    content = ai_judge._image_content(data["items"])
    metadata = json.loads(content[-1]["text"])
    assert metadata["correction_matching"] == "unordered_one_to_one_within_question"
    assert metadata["correction_answer_pool"] == [
        {"reference_question": "46", "expected_answer": "3. x = 1;"},
        {"reference_question": "47", "expected_answer": "8. return x;"}]
    blind = json.loads(ai_judge._image_content(data["items"], transcription=True)[-1]["text"])
    assert blind == {"items": [{"question": "46"}, {"question": "47"}]}
    for item in data["items"]:
        item["question"] = str(int(item["question"]) - 15)
    assert "correction_answer_pool" not in json.loads(ai_judge._image_content(data["items"])[-1]["text"])


def test_clear_visual_match_corrects_a_position_based_ai_failure(monkeypatch):
    data = make_review(["8. return x;", "3. x = 1;"])
    def judge(items):
        result = result_for(items)
        for row in result["results"].values():
            row.update(status="AI不通过", awarded_score=0, reason="按空位比较未匹配")
        return result
    monkeypatch.setattr(exam_review, "judge_handwritten_items", judge)
    exam_review.apply_ai_review(data)
    assert data["score_summary"]["total_score"] == 4


def test_repeated_answer_retains_best_single_slot_credit(monkeypatch):
    data = make_review(["3. x = 1;", "3. x = 1;"])
    data["items"][1]["score"] = 3
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    assert [item["ai_score"] for item in data["items"]] == [0, 3]


def test_actual_ai_payload_and_blind_evidence_support_cross_slot_answers(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "fixture-key")
    monkeypatch.setenv("HANDWRITING_AI_CONCURRENCY", "1")
    data = make_review(["", "3. x = 1;"])
    for item in data["items"]:
        image = tmp_path / (item["question"] + ".png")
        image.write_bytes(b"fixture-image-bytes")
        item["handwriting_images"] = [str(image)]
    payloads = []
    class Response:
        def __init__(self, rows): self.rows = rows
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return json.dumps({"choices": [{"message": {"content": json.dumps({"results": self.rows})}}]}).encode("utf-8")
    def respond(request, timeout):
        payload = json.loads(request.data)
        metadata = json.loads(payload["messages"][1]["content"][-1]["text"])
        payloads.append(metadata)
        blind = payload["messages"][0]["content"] == ai_judge.VISUAL_TRANSCRIPTION_PROMPT
        if blind:
            assert metadata == {"items": [{"question": "46"}, {"question": "47"}]}
        else:
            assert [row["reference_question"] for row in metadata["correction_answer_pool"]] == ["46", "47"]
            assert metadata["items"][1]["visual_evidence"]["visual_text"] == "3. x = 1;"
        return Response([
            {"question": "46", "visual_text": "", "confidence": .99, "image_status": "blank", "line_number_status": "missing", "status": "fail", "awarded_score": 0},
            {"question": "47", "visual_text": "3. x = 1;", "confidence": .99, "image_status": "clear", "line_number_status": "present", "status": "pass", "awarded_score": 2}])
    monkeypatch.setattr(ai_judge, "urlopen", respond)
    exam_review.apply_ai_review(data)
    assert len(payloads) == 2
    assert [item["ai_score"] for item in data["items"]] == [0, 2]
    assert data["items"][1]["ai_visual_evidence"]["matched_reference_question"] == "46"


@pytest.mark.parametrize("mode", ["all", "question"])
def test_saved_ai_results_keep_cross_slot_credit_and_match_evidence(monkeypatch, tmp_path, mode):
    import scan_ui
    data = make_review(["8. return x;", "3. x = 1;"])
    data.update(review_id="unordered", ok=True, student_name="测试考生", student_id="000123", paper_type="A")
    root = tmp_path / "reviews"
    path = root / "unordered" / "output" / "review.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", root)
    monkeypatch.setattr(scan_ui, "_ensure_review_scores", lambda review: False)
    monkeypatch.setattr(scan_ui, "_ensure_review_ai_groups", lambda review: False)
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    payload = {"review_id": "unordered", "question": "47"}
    runner = scan_ui.run_ai_review if mode == "all" else scan_ui.run_ai_question_review
    saved = runner(payload)
    persisted = json.loads(path.read_text(encoding="utf-8"))
    for report in (saved, persisted):
        assert report["score_summary"]["total_score"] == 4
        assert [item["ai_visual_evidence"]["matched_reference_question"] for item in report["items"]] == ["47", "46"]
        assert [item["expected_answer"] for item in report["items"]] == ["3. x = 1;", "8. return x;"]
    import backend.app as api
    from fastapi.testclient import TestClient
    monkeypatch.setattr(scan_ui.CANDIDATE_MANAGER, "root", lambda: root)
    monkeypatch.setattr(api, "current_user", lambda request: {"id": "teacher", "role": "teacher", "display_name": "老师"})
    client = TestClient(api.app, raise_server_exceptions=False)
    loaded = client.get("/api/review/status", params={"review_id": "unordered"})
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["score_summary"]["total_score"] == 4
    response = client.post("/api/review/confirm-grade", json={"review_id": "unordered", "expected_revision": loaded.json()["collaboration"]["revision"]})
    assert response.status_code == 200, response.text
    exported = client.get("/api/candidates/export.json", params={"review_id": "unordered"})
    assert exported.status_code == 200, exported.text
    assert [question["score"] for question in exported.json()[0]["questions"]] == [2, 2]


def test_repeated_written_answer_counts_once_with_overlapping_reference_options(monkeypatch):
    data = make_review(["3. x = 1;", "第3行 x=1;"], expected=["3. x = 1; 或 x = 2;", "3. x = 1;"])
    monkeypatch.setattr(exam_review, "judge_handwritten_items", result_for)
    exam_review.apply_ai_review(data)
    assert data["score_summary"]["total_score"] == 2
    assert sorted(item["ai_score"] for item in data["items"]) == [0, 2]
