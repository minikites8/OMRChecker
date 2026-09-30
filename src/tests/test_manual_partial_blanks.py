import copy
import pytest
from exam_review import apply_manual_review, refresh_rule_judgments, _score_summary


def review(question="31", maximum=2):
    return {"items":[{"question":question,"score":maximum,"manual_status":"待复核","auto_status":"需人工复核","recognized_text":"42","expected_answer":"42","source_content":"填空题","confidence":.9,"ai_status":"AI通过","ai_score":maximum}],"objective":[]}


@pytest.mark.parametrize("question,maximum,points",[
    ("31",2,0),("31",2,.75),("31",2,2),
    ("35",2.5,0),("35",2.5,1.25),("35",2.5,2.5),
    ("61(1)",.5,0),("61(1)",.5,.25),("61(1)",.5,.5),
    ("63",3,1.5),
])
def test_non_unit_blank_score_is_saved_and_controls_totals(question,maximum,points):
    data=review(question,maximum)
    apply_manual_review(data,[{"question":question,"score":points,"status":"待复核","text":""}])
    item=data["items"][0]
    expected="通过" if points==maximum else "不通过" if points==0 else "部分得分"
    assert item["manual_score"]==points
    assert item["final_status"]==expected
    assert item["awarded_score"]==points
    assert item["score_basis"]=="人工复核"
    assert data["score_summary"]["total_score"]==points
    assert data["score_summary"]["pending_score"]==0
    item.update(ai_status="AI通过",ai_score=maximum)
    refresh_rule_judgments(data)
    assert data["score_summary"]["total_score"]==points


@pytest.mark.parametrize("points",[-.1,2.1,float("nan"),float("inf"),True,False,"","bad",None])
def test_invalid_partial_points_reject_without_mutating_review(points):
    data=review();before=copy.deepcopy(data)
    with pytest.raises(ValueError,match="得分"):
        apply_manual_review(data,[{"question":"31","score":points,"status":"部分得分"}])
    assert data==before


def test_unit_score_blank_retains_correct_incorrect_workflow():
    data=review(maximum=1)
    with pytest.raises(ValueError):apply_manual_review(data,[{"question":"31","score":.5,"status":"部分得分"}])
    for status,points in [("通过",1),("不通过",0)]:
        apply_manual_review(data,[{"question":"31","status":status}]);assert data["score_summary"]["total_score"]==points


def test_partial_full_zero_and_clear_follow_existing_override_rules():
    data=review();apply_manual_review(data,[{"question":"31","score":.75}])
    for status,points in [("通过",2),("不通过",0)]:
        apply_manual_review(data,[{"question":"31","status":status}]);assert data["score_summary"]["total_score"]==points
    apply_manual_review(data,[{"question":"31","score":.75}])
    apply_manual_review(data,[{"question":"31","status":"待复核","score":None}])
    assert "manual_score" not in data["items"][0]
    assert data["score_summary"]["total_score"]==2


def test_partial_score_and_objective_points_add_without_changing_objective_controls():
    data=review();data["objective"]=[{"question":"1","score":2,"auto_status":"自动通过","final_status":"自动通过"}]
    apply_manual_review(data,[{"question":"31","score":1.25}])
    assert data["score_summary"]["total_score"]==3.25
    assert data["score_summary"]["possible_score"]==4


def test_missing_new_score_keeps_existing_partial_points():
    data=review();apply_manual_review(data,[{"question":"31","score":1.25}])
    apply_manual_review(data,[{"question":"31","status":"部分得分","text":"人工修正"}])
    assert data["items"][0]["manual_score"]==1.25
    assert data["score_summary"]["total_score"]==1.25


@pytest.mark.parametrize("question,maximum,kind,allowed", [
    ("31", 2, "subjective", True),
    ("35", .5, "subjective", True),
    ("63", 3, "subjective", True),
    ("64", 1, "subjective", True),
    ("31", 1, "subjective", False),
    ("31", 0, "subjective", False),
    ("1", 2, "objective", False),
])
def test_collaboration_accepts_partial_status_for_eligible_questions(question, maximum, kind, allowed):
    from review_collaboration import check_decisions, describe_review
    data = review(question, maximum)
    field = "decisions"
    if kind == "objective":
        data["objective"] = data.pop("items")
        field = "objective_decisions"
    revision = describe_review(data)["questions"][kind][question]
    payload = {field: [{"question": question, "status": "部分得分",
                       "score": maximum / 2, "expected_revision": revision}]}
    if allowed:
        check_decisions(payload, data)
    else:
        with pytest.raises(ValueError, match="复核结论无效"):
            check_decisions(payload, data)
