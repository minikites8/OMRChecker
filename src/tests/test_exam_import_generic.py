"""Format-independent question numbering and auditable answer resolution."""
import json
from pathlib import Path

import pytest

from exam_import import import_exam_and_answers


def exam_for(title, **fields):
    return [{"name": "程序题", "questions": [{
        "id": 903, "type": "blank", "title": title, "score": 1, **fields,
    }]}]


@pytest.mark.parametrize("title", [
    "59. 圆面积", "59、圆面积", "５９．圆面积", "(59) 圆面积", "（５９）圆面积",
    "【59】圆面积", "[59]圆面积", "第59题 圆面积", "第五十九题 圆面积",
    "题号：59 圆面积", "题目编号: 59 圆面积", "Q59. Area", "Question 59: Area",
    "E. 圆面积（59）", "E. 圆面积 (59)", "圆面积【５９】", "圆面积 [59]", "圆面积 第59题",
])
def test_common_title_formats(title):
    result = import_exam_and_answers(exam_for(title), {"answer_map": {"59": "area"}})
    assert result["answer_map"] == {"903": "area"}
    assert result["summary"]["answer_missing"] == []


@pytest.mark.parametrize("field", [
    "number", "question_number", "question_no", "questionNumber", "questionNo", "qno", "local_number",
])
def test_explicit_number_fields_override_title(field):
    result = import_exam_and_answers(exam_for("8. Content", **{field: "第59题"}), {"answer_map": {"59": "field"}})
    assert result["answer_map"] == {"903": "field"}


@pytest.mark.parametrize("title,ids", [
    ("第31至33题 程序填空", ["31", "32", "33"]),
    ("A. 程序填空【３１～３３】", ["31", "32", "33"]),
    ("31—33. 程序填空", ["31", "32", "33"]),
    ("31到33 程序填空", ["31", "32", "33"]),
    ("程序填空（31、33、35）", ["31", "33", "35"]),
    ("Q31,33,35: Fill", ["31", "33", "35"]),
    ("程序填空（31-33、35）", ["31", "32", "33", "35"]),
    ("第三十一至三十三题 程序填空", ["31", "32", "33"]),
])
def test_ranges_and_nonconsecutive_groups(title, ids):
    answers = {key: "value-" + key for key in ids}
    result = import_exam_and_answers(exam_for(title), {"answer_map": answers})
    assert result["answer_map"] == answers
    assert result["summary"]["question_count"] == len(ids)
    assert result["exam"]["sections"][0]["questions"][0]["id"] == 903


@pytest.mark.parametrize("key", ["５９", "第59题", "Q59", "Question 59", "(59)", "【五十九】"])
def test_answer_key_formats(key):
    result = import_exam_and_answers(exam_for("59. 圆面积"), {"answer_map": {key: "area"}})
    assert result["answer_map"] == {"903": "area"}


def test_title_numbers_do_not_override_leading_question_number():
    result = import_exam_and_answers(exam_for("59. 计算 1-3 的差（2分）"), {"answer_map": {"59": "answer"}})
    assert result["answer_map"] == {"903": "answer"}
    assert result["summary"]["question_count"] == 1


@pytest.mark.parametrize("title", ["计算 f(59)", "数组 arr[59]", "圆面积（2分）", "2026-09-29 考试", "浮点数 3.14", "E. 调用 f(59) 后的结果"])
def test_content_numbers_stay_unmatched(title):
    result = import_exam_and_answers(exam_for(title), {"answer_map": {"59": "x", "2": "y", "3": "z"}})
    assert result["answer_map"] == {}
    assert result["summary"]["answer_missing"] == ["903"]


def test_duplicate_local_numbers_require_explicit_native_ids():
    exam = [{"name": "练习", "questions": [
        {"id": 903, "title": "1. First", "type": "blank"},
        {"id": 904, "title": "1. Second", "type": "blank"},
    ]}]
    result = import_exam_and_answers(exam, {"answer_map": {"1": "ambiguous"}})
    assert result["answer_map"] == {}
    assert set(result["summary"]["answer_missing"]) == {"903", "904"}
    assert result["summary"]["answer_warnings"]
    direct = import_exam_and_answers(exam, {"answer_map": {"903": "first", "904": "second"}})
    assert direct["answer_map"] == {"903": "first", "904": "second"}


def test_conflicting_alias_answers_stay_pending():
    result = import_exam_and_answers(exam_for("59. 圆面积"), {"answer_map": {"59": "A", "第59题": "B"}})
    assert result["answer_map"] == {}
    assert result["summary"]["answer_warnings"]


def test_identical_alias_answers_coalesce():
    result = import_exam_and_answers(exam_for("59. 圆面积"), {"answer_map": {"59": "A", "第59题": "A"}})
    assert result["answer_map"] == {"903": "A"}
    assert result["summary"]["answer_unmatched_keys"] == []


def test_native_ids_keep_precedence_and_reserve_their_answer_keys():
    exam = [{"name": "练习", "questions": [
        {"id": 1, "title": "59. First", "type": "blank"},
        {"id": 904, "title": "1. Second", "type": "blank"},
    ]}]
    result = import_exam_and_answers(exam, {"answer_map": {"1": "native"}})
    assert result["answer_map"] == {"1": "native"}
    assert result["summary"]["answer_missing"] == ["904"]


def test_explicit_subquestion_ids_match_normalized_answer_keys():
    result = import_exam_and_answers(exam_for("程序", question_ids=["61(1)", "61(2)"]), {"answer_map": {"６１（１）": "a", "61(2)": "b"}})
    assert result["answer_map"] == {"61(1)": "a", "61(2)": "b"}


def test_unmatched_answers_and_missing_display_numbers_are_reported():
    result = import_exam_and_answers(exam_for("第59题 圆面积"), {"answer_map": {"70": "extra"}})
    assert result["summary"]["answer_unmatched_keys"] == ["70"]
    assert result["summary"]["answer_missing_labels"] == ["59（ID 903）"]
    assert result["summary"]["answer_warnings"]


def test_no_native_id_uses_recognized_local_number():
    exam = [{"name": "练习", "questions": [{"title": "第59题 圆面积", "type": "blank"}]}]
    result = import_exam_and_answers(exam, {"answer_map": {"59": "area"}})
    assert result["answer_map"] == {"59": "area"}


def test_partial_external_answers_preserve_embedded_group_answers():
    exam = exam_for("31-33. Fill", answer={"31": "a", "32": "b", "33": "c"})
    result = import_exam_and_answers(exam, {"answer_map": {"32": "updated"}})
    assert result["answer_map"] == {"31": "a", "32": "updated", "33": "c"}


def test_actual_paper_retains_all_answers_and_human_readable_missing_number():
    fixture = Path(__file__).parent / "fixtures/answer_import_single_number"
    result = import_exam_and_answers((fixture / "exam.json").read_text(encoding="utf-8"), (fixture / "answers.json").read_text(encoding="utf-8"))
    assert result["summary"]["answer_count"] == 63
    assert result["summary"]["answer_missing"] == ["206"]
    assert result["summary"]["answer_missing_labels"] == ["64（ID 206）"]
    assert result["summary"]["answer_unmatched_keys"] == []


@pytest.mark.parametrize("number", [2, 7, 18, 57, 100, 356, 2048])
@pytest.mark.parametrize("template", ["第{number}题 内容", "Q{number}: Content", "内容（{number}）", "{number}) Content", "内容 题号：{number}"])
def test_recognition_is_independent_of_specific_exam_numbers(number, template):
    result = import_exam_and_answers(exam_for(template.format(number=number)), {"answer_map": {str(number): "answer"}})
    assert result["answer_map"] == {"903": "answer"}
    assert result["summary"]["answer_warnings"] == []


def test_matching_native_answer_aliases_are_consumed():
    result = import_exam_and_answers(exam_for("Content"), {"answer_map": {"903": "A", "第903题": "A"}})
    assert result["answer_map"] == {"903": "A"}
    assert result["summary"]["answer_unmatched_keys"] == []


def test_explicit_local_number_lists_expand_in_declared_order():
    result = import_exam_and_answers(exam_for("Group", question_numbers=[3, 7, 9]), {"answer_map": {"3": "a", "7": "b", "9": "c"}})
    assert result["answer_map"] == {"3": "a", "7": "b", "9": "c"}
    assert result["exam"]["sections"][0]["questions"][0]["question_ids"] == ["3", "7", "9"]


def test_subquestion_ranges_expand():
    result = import_exam_and_answers(exam_for("61(1)-61(3). Fill"), {"answer_map": {"61(1)": "a", "61(2)": "b", "61(3)": "c"}})
    assert result["answer_map"] == {"61(1)": "a", "61(2)": "b", "61(3)": "c"}


@pytest.mark.parametrize("value", ["9-2", "1-10000"])
def test_explicit_invalid_ranges_are_bounded(value):
    with pytest.raises(ValueError, match="范围无效"):
        import_exam_and_answers(exam_for("Group", question_numbers=value), {})
