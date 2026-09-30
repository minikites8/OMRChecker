import copy
import json
from pathlib import Path

import pytest
import ai_judge
import exam_review
import exam_regrade
import scan_ui


def item(question, group=None):
    value = {"question": question, "source_content": "题干", "expected_answer": "42",
             "recognized_text": "42", "confidence": .99, "score": 2,
             "auto_status": "待复核", "manual_status": "", "manual_text": "",
             "ai_status": "AI需复核", "ai_visual_text": "旧识别"}
    if group:
        value.update(ai_group=group, major_question="1")
    return value


def exam():
    return {"sections": [{"id": "section-a", "questions": [
        {"id": "native-parent", "title": "程序填空", "score": 4,
         "question_ids": ["native-1", "native-2"], "local_question_ids": ["31", "32"]},
        {"id": "other-parent", "title": "下一题", "score": 2,
         "question_ids": ["33"], "local_question_ids": ["33"]},
    ]}]}


def judged(items):
    return {"status": "已完成", "enabled": True, "processed": len(items), "group_count": 1,
            "results": {row["question"]: {"status": "AI通过", "confidence": .99,
                         "visual_text": "42", "reason": "结合完整题目判分", "awarded_score": 2, "corrected_answer": "42"}
                        for row in items}}


def test_structured_group_map_covers_native_ids_and_card_aliases():
    groups = exam_review._structured_ai_group_map(exam())
    assert groups["31"] == groups["32"] == groups["native-1"] == groups["native-2"]
    assert groups["31"] != groups["33"]
    repeated = {"sections": [
        {"questions": [{"id": "1", "question_ids": ["31", "32"]}]},
        {"questions": [{"id": "1", "question_ids": ["33", "34"]}]},
    ]}
    groups = exam_review._structured_ai_group_map(repeated)
    assert groups["31"][0] != groups["33"][0]


def test_saved_group_backfill_updates_only_group_metadata():
    review = {"items": [item("31", "31"), item("32", "32"), item("33", "33")]}
    review["items"][1].update(manual_status="通过", manual_text="老师修改")
    original = copy.deepcopy(review)
    assert exam_review.attach_structured_ai_groups(review, exam()) is True
    assert review["items"][0]["ai_group"] == review["items"][1]["ai_group"]
    assert review["items"][0]["ai_group"] != review["items"][2]["ai_group"]
    for before, after in zip(original["items"], review["items"]):
        assert {k:v for k,v in after.items() if k not in {"ai_group", "major_question"}} == {k:v for k,v in before.items() if k not in {"ai_group", "major_question"}}
    assert exam_review.attach_structured_ai_groups(review, exam()) is False


def test_single_blank_retry_submits_and_updates_the_whole_parent(monkeypatch):
    review = {"items": [item("31", "1:1"), item("32", "1:1"), item("33", "1:2")], "objective": []}
    review["items"][1].update(manual_status="不通过", manual_text="人工复核")
    other = copy.deepcopy(review["items"][2]);calls=[]
    monkeypatch.setattr(exam_review, "judge_handwritten_items", lambda rows: calls.append([r["question"] for r in rows]) or judged(rows))
    result = exam_review.apply_ai_review_question(review, "32")
    assert calls == [["31", "32"]]
    assert result["ai_question_judgment"]["question"] == "32"
    assert result["ai_question_judgment"]["questions"] == ["31", "32"]
    assert result["items"][0]["ai_visual_text"] == "42"
    assert result["items"][1]["ai_visual_text"] == "42"
    assert result["items"][1]["manual_status"] == "不通过"
    assert result["items"][2]["ai_visual_text"] == other["ai_visual_text"]


def test_parent_retry_forwards_progress_and_keeps_every_subpart(monkeypatch):
    review={"items":[item("61(1)"),item("61(2)"),item("62")],"objective":[]};events=[]
    def fake(rows, progress_callback):
        assert [r["question"] for r in rows] == ["61(1)","61(2)"]
        progress_callback({"group_count":1,"completed_groups":1});return judged(rows)
    monkeypatch.setattr(exam_review,"judge_handwritten_items",fake)
    result=exam_review.apply_ai_review_question(review,"61(2)",progress_callback=events.append)
    assert result["ai_question_judgment"]["questions"] == ["61(1)","61(2)"]
    assert events == [{"group_count":1,"completed_groups":1}]


def persisted(monkeypatch,tmp_path):
    monkeypatch.setattr(scan_ui,"REVIEW_ROOT",tmp_path/"reviews")
    monkeypatch.setattr(scan_ui,"IMPORT_ROOT",tmp_path/"imports")
    path=scan_ui.REVIEW_ROOT/"group-review"/"output"/"review.json";path.parent.mkdir(parents=True)
    review={"review_id":"group-review","import_id":"exam-a","items":[item("31","31"),item("32","32"),item("33","33")],"objective":[],"score_summary":{"possible_score":6},"grade_confirmed":True}
    for row in review["items"]:row["handwriting_urls"]=["/reviews/group-review/output/handwriting/"+row["question"]+".png"]
    path.write_text(json.dumps(review),encoding="utf-8")
    imported=scan_ui.IMPORT_ROOT/"exam-a"/"normalized_exam.json";imported.parent.mkdir(parents=True)
    imported.write_text(json.dumps({"exam":exam()}),encoding="utf-8")
    return path


def test_retry_restores_all_group_images_and_merges_all_group_results(monkeypatch,tmp_path):
    path=persisted(monkeypatch,tmp_path)
    def fake(review,question):
        assert review["items"][0]["ai_group"] == review["items"][1]["ai_group"]
        for row in review["items"][:2]:
            assert row["handwriting_images"] == [str((path.parent/"handwriting"/(row["question"]+".png")).resolve())]
            row.update(ai_status="AI通过",ai_visual_text="整题识别"+row["question"])
        review["ai_question_judgment"]={"question":question,"questions":["31","32"],"status":"已完成"};return review
    monkeypatch.setattr(scan_ui,"apply_ai_review_question",fake)
    result=scan_ui.run_ai_question_review({"review_id":"group-review","question":"32"})
    assert [r["ai_visual_text"] for r in result["items"]] == ["整题识别31","整题识别32","旧识别"]
    assert result["grade_confirmed"] is False
    saved=json.loads(path.read_text(encoding="utf-8"));assert saved["items"][0]["ai_group"] == saved["items"][1]["ai_group"]


def test_sibling_clicks_share_one_worker_and_mark_whole_group_busy(monkeypatch,tmp_path):
    persisted(monkeypatch,tmp_path);calls=[]
    class Future:
        def done(self):return False
    class Executor:
        def submit(self,*args):calls.append(args);return Future()
    monkeypatch.setattr(scan_ui,"AI_REVIEW_EXECUTOR",Executor())
    monkeypatch.setattr(scan_ui,"AI_QUESTION_WORKERS",{})
    monkeypatch.setattr(scan_ui,"ai_is_configured",lambda:True)
    for q in ["32","31"]:
        result=scan_ui.start_ai_question_review({"review_id":"group-review","question":q})
        assert [r["ai_status"] for r in result["items"]] == ["AI处理中","AI处理中","AI需复核"]
        assert result["ai_question_judgment"]["questions"] == ["31","32"]
    assert len(calls)==1
    assert len(scan_ui.AI_QUESTION_WORKERS)==1


def test_whole_paper_uses_restored_groups_for_existing_scored_reviews(monkeypatch,tmp_path):
    persisted(monkeypatch,tmp_path);captured=[]
    def fake(review,progress_callback):
        captured.append([r["ai_group"] for r in review["items"]]);review["ai_judgment"]={"status":"已完成"};return review
    monkeypatch.setattr(scan_ui,"apply_ai_review",fake)
    scan_ui.run_ai_review({"review_id":"group-review"})
    assert captured[0][0]==captured[0][1]
    assert captured[0][0]!=captured[0][2]


def test_live_sibling_edits_keep_latest_values_and_leave_processing(monkeypatch,tmp_path):
    path=persisted(monkeypatch,tmp_path)
    review=json.loads(path.read_text(encoding="utf-8"))
    for row in review["items"][:2]:row["ai_status"]="AI处理中"
    path.write_text(json.dumps(review),encoding="utf-8")
    def fake(review,question):
        latest=json.loads(path.read_text(encoding="utf-8"));latest["items"][1].update(manual_status="通过",manual_text="老师的新修改")
        path.write_text(json.dumps(latest),encoding="utf-8")
        for row in review["items"][:2]:row.update(ai_status="AI通过",ai_visual_text="旧请求结果")
        review["ai_question_judgment"]={"question":question,"questions":["31","32"],"status":"已完成"};return review
    monkeypatch.setattr(scan_ui,"apply_ai_review_question",fake)
    result=scan_ui.run_ai_question_review({"review_id":"group-review","question":"31"})
    assert result["items"][0]["ai_visual_text"]=="旧请求结果"
    assert result["items"][1]["manual_text"]=="老师的新修改"
    assert result["items"][1]["ai_visual_text"]=="旧识别"
    assert result["items"][1]["ai_status"]=="AI需复核"
    assert result["ai_question_judgment"]["skipped_questions"]==["32"]


def test_worker_failure_updates_all_siblings_and_releases_shared_worker(monkeypatch,tmp_path):
    path=persisted(monkeypatch,tmp_path);review=json.loads(path.read_text(encoding="utf-8"))
    exam_review.attach_structured_ai_groups(review,exam());path.write_text(json.dumps(review),encoding="utf-8")
    key=scan_ui._ai_question_key("group-review","31")
    monkeypatch.setattr(scan_ui,"AI_QUESTION_WORKERS",{key:object()})
    def fail(payload):raise RuntimeError("fixture AI failure")
    monkeypatch.setattr(scan_ui,"run_ai_question_review",fail)
    scan_ui._ai_question_worker("group-review","32",key)
    saved=json.loads(path.read_text(encoding="utf-8"))
    assert [r["ai_status"] for r in saved["items"]]==["AI异常","AI异常","AI需复核"]
    assert saved["ai_question_judgment"]["questions"]==["31","32"]
    assert scan_ui.AI_QUESTION_WORKERS=={}


def test_bulk_regrade_rebuilds_parent_groups_and_preserves_manual_edits():
    review={"items":[item("31","31"),item("32","32"),item("33","33")],"objective":[]}
    review["items"][1]["manual_text"]="人工填写"
    imported={"exam":exam(),"answer_map":{"31":"42","32":"42","33":"42"},"source_map":{"31":"题干","32":"题干","33":"另一题"}}
    refreshed=exam_regrade._prepare_review(review,imported,"run-group-test")
    assert refreshed["items"][0]["ai_group"]==refreshed["items"][1]["ai_group"]
    assert refreshed["items"][0]["ai_group"]!=refreshed["items"][2]["ai_group"]
    assert refreshed["items"][1]["manual_text"]=="人工填写"


def test_http_ai_payload_contains_all_parent_blanks_and_individual_images(monkeypatch,tmp_path):
    monkeypatch.setenv("HANDWRITING_AI_API_KEY","fixture-key")
    monkeypatch.setenv("HANDWRITING_AI_CONCURRENCY","1")
    review={"items":[item("31","1:1"),item("32","1:1"),item("33","1:2")],"objective":[]}
    for row in review["items"]:
        image=tmp_path/(row["question"]+".png");image.write_bytes(b"fixture-image-bytes")
        row["handwriting_images"]=[str(image)];row["source_content"]="完整的原题干与两个小空"
    review["items"][1]["recognized_text"]=""
    payloads=[]
    class Response:
        status=200
        def __init__(self,data):self.data=data
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def read(self):return json.dumps(self.data).encode("utf-8")
    def respond(request,timeout):
        payload=json.loads(request.data);content=payload["messages"][1]["content"];metadata=json.loads(content[-1]["text"]);rows=metadata["items"]
        payloads.append([row["question"] for row in rows])
        assert len([part for part in content if part["type"]=="image_url"])==len(rows)
        results=[{"question":row["question"],"status":"fail" if row["question"]=="32" else "pass","awarded_score":0 if row["question"]=="32" else 2,"confidence":.99,"image_status":"blank" if row["question"]=="32" else "clear","visual_text":"" if row["question"]=="32" else "42","reason":"原图判分","corrected_answer":""} for row in rows]
        return Response({"choices":[{"message":{"content":json.dumps({"results":results})}}]})
    monkeypatch.setattr(ai_judge,"urlopen",respond)
    result=ai_judge.judge_handwritten_items(review["items"])
    assert sorted(payloads)==[["31","32"],["33"]]
    assert result["group_count"]==2
    assert result["results"]["31"]["awarded_score"]==2
    assert result["results"]["32"]["awarded_score"]==0


def test_backfill_uses_the_saved_paper_variant(monkeypatch,tmp_path):
    path=persisted(monkeypatch,tmp_path);review=json.loads(path.read_text(encoding="utf-8"));review["answer_paper_type"]="B"
    b_exam={"sections":[{"questions":[{"id":"B-main","question_ids":["32","33"]}]}]}
    imported=scan_ui.IMPORT_ROOT/"exam-a"/"normalized_exam.json"
    imported.write_text(json.dumps({"exam":exam(),"paper_variants":{"B":{"exam":b_exam}}}),encoding="utf-8")
    assert scan_ui._ensure_review_ai_groups(review)
    assert review["items"][1]["ai_group"]==review["items"][2]["ai_group"]
    assert review["items"][0]["ai_group"]!=review["items"][1]["ai_group"]
