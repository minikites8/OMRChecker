import json
from pathlib import Path

import pytest

from exam_import import import_exam_and_answers


@pytest.mark.parametrize("title", ["E. 圆面积计算（59）", "E. 圆面积计算(59)", "E. 圆面积计算（ 59 ）", "E. 圆面积计算 ( 59 ) "])
def test_trailing_parenthesized_number_resolves_to_native_id(title):
    exam = [{"name": "程序改错", "questions": [{"id": 203, "type": "blank", "title": title, "score": 1}]}]
    result = import_exam_and_answers(exam, {"answer_map": {"59": "(7) double area = PI * r * r;"}})
    assert result["answer_map"] == {"203": "(7) double area = PI * r * r;"}
    assert result["summary"]["answer_missing"] == []
    assert result["exam"]["sections"][0]["questions"][0]["id"] == 203
    assert result["exam"]["sections"][0]["questions"][0]["question_ids"] == ["203"]


def test_native_answer_id_keeps_precedence():
    exam = [{"name": "程序改错", "questions": [{"id": 203, "type": "blank", "title": "E. 圆面积计算（59）", "score": 1}]}]
    result = import_exam_and_answers(exam, {"answer_map": {"59": "local", "203": "native"}})
    assert result["answer_map"] == {"203": "native"}


def test_leading_question_number_keeps_precedence():
    exam = [{"name": "程序题", "questions": [{"id": 203, "type": "blank", "title": "59. 求函数值（2）", "score": 1}]}]
    result = import_exam_and_answers(exam, {"answer_map": {"59": "expected", "2": "argument"}})
    assert result["answer_map"] == {"203": "expected"}


def test_parenthesized_code_inside_title_does_not_supply_question_number():
    exam = [{"name": "程序题", "questions": [{"id": 203, "type": "blank", "title": "E. 调用 f(59) 后的结果", "score": 1}]}]
    result = import_exam_and_answers(exam, {"answer_map": {"59": "argument"}})
    assert result["answer_map"] == {}
    assert result["summary"]["answer_missing"] == ["203"]


def test_full_provided_exam_imports_all_63_supplied_answers():
    fixture = Path(__file__).parent / "fixtures/answer_import_single_number"
    exam = json.loads((fixture / "exam.json").read_text(encoding="utf-8"))
    answers = json.loads((fixture / "answers.json").read_text(encoding="utf-8"))
    result = import_exam_and_answers(exam, answers)
    expected = {str(163 + number) if number <= 30 else str(number): answer for number, answer in ((int(key), value) for key, value in answers["answer_map"].items())}
    expected["203"] = expected.pop("59")
    expected["204"] = expected.pop("60")
    assert result["answer_map"] == expected
    assert result["summary"]["question_count"] == 64
    assert result["summary"]["total_score"] == 100
    assert result["summary"]["answer_count"] == 63
    assert result["summary"]["answer_missing"] == ["206"]
    questions = {question["id"]: question for section in result["exam"]["sections"] for question in section["questions"]}
    assert questions[203]["answer"] == answers["answer_map"]["59"]
    assert questions[204]["answer"] == answers["answer_map"]["60"]
    assert questions[206]["title"].startswith("64.")


def test_import_handler_saves_all_supplied_answers(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock

    import scan_ui

    fixtures = Path(__file__).parent / "fixtures/answer_import_single_number"
    sync = Mock()
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "PLATFORM_PERSISTENCE", SimpleNamespace(sync_tree=sync))
    result = scan_ui.run_exam_import({
        "name": "软件B卷",
        "exam_text": (fixtures / "exam.json").read_text(encoding="utf-8"),
        "answer_text": (fixtures / "answers.json").read_text(encoding="utf-8"),
    })
    assert result["answer_count"] == 63
    assert result["answer_missing"] == ["206"]
    saved = tmp_path / "imports" / result["import_id"]
    stored = json.loads((saved / "normalized_exam.json").read_text(encoding="utf-8"))
    flat = json.loads((saved / "answer_map.json").read_text(encoding="utf-8"))
    sections = json.loads((saved / "exam_with_answers.json").read_text(encoding="utf-8"))
    assert stored["summary"]["answer_count"] == 63
    assert stored["summary"]["answer_missing"] == ["206"]
    assert flat["answer_map"] == stored["answer_map"] == result["answer_map"]
    questions = {question["id"]: question for section in sections for question in section["questions"]}
    assert questions[203]["answer"] == result["answer_map"]["203"]
    assert questions[204]["answer"] == result["answer_map"]["204"]
    sync.assert_called_once()
