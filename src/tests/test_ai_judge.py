from __future__ import annotations

import json

import ai_judge
from exam_review import apply_ai_review


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


def test_ai_judge_reports_configuration_state(monkeypatch):
    monkeypatch.delenv("HANDWRITING_AI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = ai_judge.judge_handwritten_items([{"question": "31", "recognized_text": "i%2", "expected_answer": "i % 2"}])
    assert result["status"] == "未配置"
    assert result["enabled"] is False
    assert result["processed"] == 0


def test_ai_judge_maps_remote_semantic_result(monkeypatch, tmp_path):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")
    monkeypatch.setenv("HANDWRITING_AI_MODEL", "test-model")
    captured = {}
    image_path = tmp_path / "31.png"
    image_path.write_bytes(b"fake-image-bytes")

    def fake_urlopen(request, timeout):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse({
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "results": [{
                            "question": "31",
                            "status": "pass",
                            "confidence": 0.91,
                            "reason": "表达式语义一致",
                            "visual_text": "i%2≠0",
                            "corrected_answer": "i % 2 != 0",
                        }]
                    }, ensure_ascii=False)
                }
            }]
        })

    monkeypatch.setattr(ai_judge, "urlopen", fake_urlopen)
    result = ai_judge.judge_handwritten_items([{
        "question": "31",
        "source_content": "判断奇数",
        "expected_answer": "i % 2 != 0",
        "recognized_text": "i%2≠0",
        "handwriting_images": [str(image_path)],
        "confidence": 0.84,
    }])
    assert result["status"] == "已完成"
    assert result["results"]["31"]["status"] == "AI通过"
    assert result["results"]["31"]["corrected_answer"] == "i % 2 != 0"
    assert result["results"]["31"]["visual_text"] == "i%2≠0"
    assert captured["body"]["model"] == "test-model"
    assert captured["body"]["messages"][1]["content"][2]["type"] == "image_url"
    assert captured["body"]["messages"][1]["content"][2]["image_url"]["url"].startswith("data:image/png;base64,")


def test_apply_ai_review_adds_per_item_judgment(monkeypatch):
    monkeypatch.setattr(ai_judge, "judge_handwritten_items", lambda items: {
        "status": "已完成",
        "enabled": True,
        "model": "test-model",
        "processed": 1,
        "message": "AI已完成手写答案语义判断",
        "results": {"31": {"status": "AI通过", "confidence": 0.9, "reason": "语义一致", "corrected_answer": "i % 2 != 0"}},
    })
    monkeypatch.setattr("exam_review.judge_handwritten_items", ai_judge.judge_handwritten_items)
    review = {"items": [{"question": "31", "score": 2, "auto_status": "需人工复核", "manual_status": "待复核", "recognized_text": "i%2", "expected_answer": "i % 2 != 0"}], "objective": []}
    updated = apply_ai_review(review)
    assert updated["items"][0]["ai_status"] == "AI通过"
    assert updated["review_summary"]["ai_pass"] == 1
    assert updated["ai_judgment"]["status"] == "已完成"
    assert updated["items"][0]["final_status"] == "通过"
    assert updated["items"][0]["score_basis"] == "AI审核"
    assert updated["items"][0]["awarded_score"] == 2
    assert updated["score_summary"]["total_score"] == 2
    assert updated["score_summary"]["pending_score"] == 0


def test_ai_read_timeout_returns_review_result(monkeypatch):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")
    def timed_out(request, timeout):
        raise TimeoutError("The read operation timed out")
    monkeypatch.setattr(ai_judge, "urlopen", timed_out)
    result = ai_judge.judge_handwritten_items([{"question": "31", "expected_answer": "A", "recognized_text": "A"}])
    assert result["status"] == "异常"
    assert result["processed"] == 0
    assert result["results"]["31"]["status"] == "AI需复核"
    assert "HANDWRITING_AI_TIMEOUT" in result["message"]


def test_structured_main_questions_make_one_request_per_question(monkeypatch):
    import exam_review

    exam = [{"name": "填空和简答", "questions": [
        {"id": "1", "type": "blank", "title": "1. 第一大题", "score": 2,
         "question_ids": ["31", "32"], "answer": {"31": "a", "32": "b"}},
        {"id": "2", "type": "blank", "title": "2. 第二大题", "score": 1,
         "question_ids": ["33"], "answer": "c"},
        {"id": "3", "type": "blank", "title": "3. 材料填空", "score": 6,
         "question_ids": ["61", "62"], "answer": {"61": "x", "62": "y"}},
    ]}]
    fields = {key: {"text": key, "confidence": 0.95} for key in ("31", "32", "33", "61", "62")}
    monkeypatch.setattr(exam_review, "extract_answer_card", lambda paths, image_dir=None: {
        "text_fields": fields, "objective": {}, "ocr_errors": [],
        "alignment_scores": [], "crop_paths": {}, "student_id": "",
    })
    review = exam_review.build_review_from_structured(exam, {}, ["card.png"])
    item_map = {item["question"]: item for item in review["items"]}
    assert [item_map[key]["major_question"] for key in fields] == ["1", "1", "2", "3", "3"]
    assert item_map["31"]["ai_group"] == item_map["32"]["ai_group"]
    assert item_map["31"]["ai_group"] != item_map["33"]["ai_group"]

    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")
    groups = []

    def fake_request(config, items):
        groups.append([item["question"] for item in items])
        content = ai_judge._image_content(items)[0]["text"]
        assert json.loads(content)["major_question"] == items[0]["major_question"]
        return {"choices": [{"message": {"content": json.dumps({"results": [
            {"question": item["question"], "status": "pass", "confidence": 0.95}
            for item in items
        ]})}}]}

    monkeypatch.setattr(ai_judge, "_request", fake_request)
    judged = ai_judge.judge_handwritten_items(review["items"])
    assert groups == [["31", "32"], ["33"], ["61", "62"]]
    assert judged["group_count"] == 3
    assert judged["processed"] == 5
    assert judged["status"] == "已完成"


def test_major_question_failure_does_not_stop_later_groups(monkeypatch):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")
    calls = []

    def fake_request(config, items):
        calls.append([item["question"] for item in items])
        if items[0]["major_question"] == "1":
            raise TimeoutError("The read operation timed out")
        return {"choices": [{"message": {"content": json.dumps({"results": [
            {"question": "33", "status": "pass", "confidence": 0.8},
            {"question": "unexpected", "status": "pass", "confidence": 1},
        ]})}}]}

    monkeypatch.setattr(ai_judge, "_request", fake_request)
    items = [
        {"question": number, "major_question": group, "ai_group": group,
         "recognized_text": "手写内容"}
        for number, group in [("31", "1"), ("32", "1"), ("33", "2")]
    ]
    result = ai_judge.judge_handwritten_items(items)
    assert calls == [["31", "32"], ["33"]]
    assert result["status"] == "部分完成"
    assert result["processed"] == 1
    assert result["group_count"] == 2
    assert result["results"]["31"]["status"] == "AI需复核"
    assert result["results"]["32"]["status"] == "AI需复核"
    assert result["results"]["33"]["status"] == "AI通过"
    assert "unexpected" not in result["results"]


def test_progress_callback_tracks_each_major_question(monkeypatch):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")
    events = []

    def fake_request(config, items):
        return {"choices": [{"message": {"content": json.dumps({"results": [
            {"question": item["question"], "status": "pass", "confidence": 0.9}
            for item in items
        ]})}}]}

    monkeypatch.setattr(ai_judge, "_request", fake_request)
    items = [
        {"question": number, "ai_group": group, "major_question": label, "recognized_text": "答案"}
        for number, group, label in [("31", "1:1", "1"), ("32", "1:1", "1"), ("33", "1:2", "2")]
    ]
    result = ai_judge.judge_handwritten_items(items, progress_callback=events.append)
    assert events[0]["phase"] == "准备中"
    assert [(event["phase"], event["completed_groups"]) for event in events[1:]] == [
        ("审核中", 0), ("审核中", 0), ("本组完成", 1), ("本组完成", 2),
    ]
    assert {event["current_group"] for event in events[1:]} == {"1", "2"}
    assert [event["processed"] for event in events[-2:]] == [2, 3]
    assert result["completed_groups"] == result["group_count"] == 2



def test_ai_response_is_printed_to_console(monkeypatch, capsys):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")
    payload = {"choices": [{"message": {"content": json.dumps({"results": []})}}]}

    def fake_urlopen(request, timeout):
        return FakeResponse(payload)

    monkeypatch.setattr(ai_judge, "urlopen", fake_urlopen)
    ai_judge._request(ai_judge.ai_config(), [{"question": "31", "recognized_text": "答案"}])
    output = capsys.readouterr().out
    assert "[AI响应][大题 31] HTTP?" in output
    assert '"choices"' in output
    assert "results" in output


def test_ai_http_error_response_is_printed_to_console(monkeypatch, capsys):
    from io import BytesIO
    from urllib.error import HTTPError
    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")

    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 429, "Too Many Requests", None,
                        BytesIO(b'{"error":"rate limit"}'))

    monkeypatch.setattr(ai_judge, "urlopen", fake_urlopen)
    try:
        ai_judge._request(ai_judge.ai_config(), [{"question": "31", "recognized_text": "A"}])
    except RuntimeError as error:
        assert "HTTP429" in str(error)
    output = capsys.readouterr().out
    assert "[AI响应][大题 31] HTTP429" in output
    assert '"rate limit"' in output



def test_ai_prompt_marks_correction_questions_as_strict():
    payload = ai_judge._prompt_items([{
        "question": "48", "source_content": "改正第11行", "expected_answer": "11. return *max;",
        "recognized_text": "return *max;", "confidence": 0.9,
    }])
    assert payload[0]["question_kind"] == "correction"
    assert "行号" in payload[0]["strict_requirements"]
    assert "第46至60题" in ai_judge.SYSTEM_PROMPT
    assert "禁止根据题干或参考答案替考生补全行号" in ai_judge.SYSTEM_PROMPT


def test_ai_pass_is_overridden_when_correction_line_number_is_missing(monkeypatch):
    monkeypatch.setattr(ai_judge, "judge_handwritten_items", lambda items: {
        "status": "已完成", "enabled": True, "model": "test-model", "processed": 1,
        "message": "AI已完成手写答案语义判断",
        "results": {"48": {
            "status": "AI通过", "confidence": 0.99, "reason": "代码一致",
            "visual_text": "return *max;", "corrected_answer": "11. return *max;",
        }},
    })
    monkeypatch.setattr("exam_review.judge_handwritten_items", ai_judge.judge_handwritten_items)
    review = {
        "items": [{
            "question": "48", "score": 1, "auto_status": "不通过", "manual_status": "待复核",
            "recognized_text": "return *max;", "expected_answer": "11. return *max;", "confidence": 0.95,
        }],
        "objective": [],
    }
    updated = apply_ai_review(review)
    assert updated["items"][0]["ai_status"] == "AI不通过"
    assert "行号" in updated["items"][0]["ai_reason"]
    assert updated["items"][0]["final_status"] == "不通过"
    assert updated["items"][0]["awarded_score"] == 0


def test_algorithm_question_64_uses_autonomous_ai_grading(monkeypatch):
    payload = ai_judge._prompt_items([{
        "question": "64", "source_content": "请设计一个查找最短路径的算法并分析复杂度",
        "expected_answer": "", "recognized_text": "使用 BFS 遍历并记录前驱", "confidence": 0.9,
    }])
    assert payload[0]["question_kind"] == "algorithm"
    assert payload[0]["expected_answer"] == ""
    assert "参考答案可能为空" in payload[0]["strict_requirements"]
    assert "第64题属于开放算法题" in ai_judge.SYSTEM_PROMPT
    assert "第64题按开放算法题规则继续自主判分" in ai_judge.SYSTEM_PROMPT
    assert "不能仅因缺少固定答案返回 review" in ai_judge.SYSTEM_PROMPT

    monkeypatch.setattr(ai_judge, "judge_handwritten_items", lambda items: {
        "status": "已完成", "enabled": True, "model": "test-model", "processed": 1,
        "message": "AI已按大题完成手写答案语义判断",
        "results": {"64": {
            "status": "AI通过", "confidence": 0.93,
            "reason": "BFS 思路正确，能够完成题目要求",
            "visual_text": "使用 BFS 遍历并记录前驱",
            "corrected_answer": "",
        }},
    })
    monkeypatch.setattr("exam_review.judge_handwritten_items", ai_judge.judge_handwritten_items)
    review = {
        "items": [{
            "question": "64", "score": 10, "auto_status": "需人工复核",
            "manual_status": "待复核", "recognized_text": "使用 BFS 遍历并记录前驱",
            "expected_answer": "", "confidence": 0.9,
        }],
        "objective": [],
    }
    updated = apply_ai_review(review)
    assert updated["items"][0]["ai_status"] == "AI通过"
    assert updated["items"][0]["final_status"] == "通过"
    assert updated["items"][0]["score_basis"] == "AI审核"
    assert updated["items"][0]["awarded_score"] == 10



def test_ai_group_requests_run_concurrently(monkeypatch):
    import threading

    monkeypatch.setenv("HANDWRITING_AI_API_KEY", "test-key")
    monkeypatch.setenv("HANDWRITING_AI_CONCURRENCY", "2")
    lock = threading.Lock()
    second_started = threading.Event()
    started = 0

    def fake_request(config, items):
        nonlocal started
        with lock:
            started += 1
            if started >= 2:
                second_started.set()
        assert second_started.wait(2.0)
        return {"choices": [{"message": {"content": json.dumps({"results": [
            {"question": item["question"], "status": "pass", "confidence": 0.9}
            for item in items
        ]})}}]}

    monkeypatch.setattr(ai_judge, "_request", fake_request)
    result = ai_judge.judge_handwritten_items([
        {"question": "31", "ai_group": "1", "major_question": "1", "recognized_text": "答案"},
        {"question": "32", "ai_group": "2", "major_question": "2", "recognized_text": "答案"},
    ])
    assert result["status"] == "已完成"
    assert result["concurrency"] == 2
    assert result["processed"] == 2


def test_correction_prompt_ignores_printed_subquestion_labels():
    payload = ai_judge._prompt_items([{
        "question": "55",
        "expected_answer": "6. scanf(\"%s\", name);",
        "recognized_text": "(b) scanf(\"%s\", name);",
    }])
    assert "印刷的(a)/(b)/(c)小题标签" in payload[0]["strict_requirements"]
    assert "不把它们当作行号" in payload[0]["strict_requirements"]
    assert "只转录考生实际手写内容" in payload[0]["visual_text_rule"]
    assert "印刷的(a)/(b)/(c)永远不作为改错行号" in ai_judge.SYSTEM_PROMPT


def test_clean_visual_text_removes_printed_correction_label():
    assert ai_judge._clean_visual_text("55", "（b） scanf(\"%s\", name);") == 'scanf("%s", name);'
    assert ai_judge._clean_visual_text("55", "(6) scanf(\"%s\", name);") == '(6) scanf("%s", name);'


def test_printed_correction_label_is_reported_as_missing_line_number(monkeypatch):
    monkeypatch.setattr(ai_judge, "judge_handwritten_items", lambda items: {
        "status": "已完成", "enabled": True, "model": "test-model", "processed": 1,
        "message": "AI已完成手写答案语义判断",
        "results": {"55": {
            "status": "AI通过", "confidence": 0.99,
            "reason": "改错内容一致",
            "visual_text": "(b) scanf(\"%s\", name);",
            "corrected_answer": "6. scanf(\"%s\", name);",
        }},
    })
    monkeypatch.setattr("exam_review.judge_handwritten_items", ai_judge.judge_handwritten_items)
    review = {
        "items": [{
            "question": "55", "score": 1, "auto_status": "需人工复核", "manual_status": "待复核",
            "recognized_text": "(b) scanf(\"%s\", name);",
            "expected_answer": "6. scanf(\"%s\", name);", "confidence": 0.95,
        }],
        "objective": [],
    }
    updated = apply_ai_review(review)
    item = updated["items"][0]
    assert item["ai_visual_text"] == 'scanf("%s", name);'
    assert item["ai_status"] == "AI不通过"
    assert "行号" in item["ai_reason"]