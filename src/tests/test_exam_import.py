import json
from exam_import import answer_map, import_exam_and_answers, normalize_exam_data, parse_json_text, source_map


def test_imports_relaxed_exam_shape_with_unescaped_code_quotes():
    source = r'''[{"name":"Section","total_score":3,"questions":[{"type":"blank","title":"31-33. code","score":3,"description":"printf("%d", value);"}]}]'''
    normalized = normalize_exam_data(parse_json_text(source))
    assert normalized["summary"]["section_count"] == 1
    assert normalized["summary"]["question_count"] == 3
    assert normalized["summary"]["grouped_question_count"] == 1
    assert normalized["summary"]["total_score"] == 3
    assert normalized["sections"][0]["questions"][0]["description"] == 'printf("%d", value);'
    assert normalized["sections"][0]["questions"][0]["question_ids"] == ["31", "32", "33"]


def test_import_merges_answer_map_and_normalizes_true_false():
    exam = [{"name": "TF", "questions": [{"type": "single", "title": "1. statement", "score": 1, "description": "T. true\nF. false"}]}]
    answers = {"answer_map": {"1": "true"}}
    imported = import_exam_and_answers(json.dumps(exam), json.dumps(answers))
    assert imported["answer_map"] == {"1": "T"}
    assert imported["summary"]["answer_count"] == 1
    assert imported["summary"]["answer_missing"] == []


def test_grouped_answers_expand_to_individual_fields():
    exam = [{"name": "Grouped", "questions": [
        {"type": "blank", "title": "31-33. Fill", "score": 3, "description": "*** (31) ***"},
        {"type": "essay", "title": "A. Fix", "score": 2, "description": "Code"},
    ]}]
    answers = {"31": "a", "32": "b", "33": "c", "34": "x", "35": "y"}
    imported = import_exam_and_answers(exam, answers)
    assert imported["answer_map"] == answers
    assert imported["exam"]["sections"][0]["questions"][0]["answer"] == {"31": "a", "32": "b", "33": "c"}
    assert imported["exam"]["sections"][0]["questions"][1]["question_ids"] == ["34", "35"]


def test_import_preserves_optional_subquestion_score_map():
    exam = [{"name": "Grouped", "questions": [{
        "id": "31-32", "type": "blank", "title": "31-32. Fill", "score": 4,
        "question_ids": ["31", "32"], "score_map": {"31": 1, "32": 3},
    }]}]
    imported = import_exam_and_answers(exam)
    question = imported["exam"]["sections"][0]["questions"][0]
    assert question["score_map"] == {"31": 1, "32": 3}



def test_en_dash_title_expands_question_ids_and_source_map():
    exam = [{"name": "程序填空", "questions": [{
        "id": "31", "type": "blank", "title": "31–33. 输出水仙花数", "score": 3,
        "description": "***31***\n***32***\n***33***",
    }]}]
    normalized = normalize_exam_data(exam)
    question = normalized["sections"][0]["questions"][0]
    assert question["question_ids"] == ["31", "32", "33"]
    assert set(source_map(normalized)) == {"31", "32", "33"}
    assert normalized["summary"]["question_count"] == 3
    assert normalized["summary"]["types"] == {"blank": 3}


def test_range_formats_and_explicit_question_ids_priority():
    exam = [{"name": "范围", "questions": [
        {"type": "blank", "title": "46-48. 改错", "score": 3},
        {"type": "blank", "title": "49~51. 改错", "score": 3},
        {"type": "blank", "title": "52至54. 改错", "score": 3},
        {"id": "61", "type": "blank", "title": "61–63. 材料填空", "score": 3,
         "question_ids": ["61(1)", "61(2)", "61(3)"]},
    ]}]
    normalized = normalize_exam_data(exam)
    questions = normalized["sections"][0]["questions"]
    assert questions[0]["question_ids"] == ["46", "47", "48"]
    assert questions[1]["question_ids"] == ["49", "50", "51"]
    assert questions[2]["question_ids"] == ["52", "53", "54"]
    assert questions[3]["question_ids"] == ["61(1)", "61(2)", "61(3)"]


def test_grouped_list_dict_and_numbered_text_answers_expand():
    exam = [{"name": "答案", "questions": [
        {"type": "blank", "title": "31–33. 填空", "score": 3,
         "answer": ["x / 100", "x / 10 % 10", "x % 10"]},
        {"type": "blank", "title": "34–36. 填空", "score": 3,
         "answer": {"1": "n - 1", "2": "j < n - i - 1", "3": "swapped = 1"}},
        {"type": "blank", "title": "61–63. 材料填空", "score": 9,
         "answer": "61. return cur;\n62. cur += step; return *this;\n63. return cur < other.cur;"},
    ]}]
    normalized = normalize_exam_data(exam)
    assert answer_map(normalized) == {
        "31": "x / 100", "32": "x / 10 % 10", "33": "x % 10",
        "34": "n - 1", "35": "j < n - i - 1", "36": "swapped = 1",
        "61": "return cur;", "62": "cur += step; return *this;", "63": "return cur < other.cur;",
    }


def test_import_keeps_independent_a_b_c_exam_and_answer_variants():
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
    imported = import_exam_and_answers(json.dumps(exam, ensure_ascii=False), json.dumps(answers, ensure_ascii=False))
    assert imported["paper_types"] == ["A", "B", "C"]
    assert set(imported["paper_variants"]) == {"A", "B", "C"}
    assert imported["paper_variants"]["A"]["answer_map"] == {"1": "A"}
    assert imported["paper_variants"]["B"]["answer_map"] == {"1": "B"}
    assert imported["paper_variants"]["C"]["answer_map"] == {"1": "C"}
    assert imported["paper_variants"]["A"]["source_map"]["1"].startswith("A卷第1题")
    assert imported["paper_variants"]["B"]["source_map"]["1"].startswith("B卷第1题")
    assert imported["paper_variants"]["C"]["source_map"]["1"].startswith("C卷第1题")
    assert imported["paper_variants"]["B"]["exam"]["sections"][0]["questions"][0]["score"] == 3