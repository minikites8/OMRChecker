"""Roster import, identity precedence, attendance, and both HTTP deployments."""
import base64
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import scan_ui
from backend import app as api
from candidate_manager import CandidateManager
from exam_roster import (apply_roster_identity, attendance, load_roster, parse_roster,
                         roster_index, save_roster)
from workspace_context import WorkspacePath, workspace_scope

CONTENT = "学号,姓名\n000001,张三\n000002,李四\n000003,王五\n"


@pytest.mark.parametrize("content", [CONTENT, CONTENT.encode("utf-8-sig"), CONTENT.encode("gb18030"), CONTENT.replace(",", "\t"), "student_id,student_name\n000001,张三\n000002,李四\n000003,王五\n"])
def test_csv_tsv_encodings_and_leading_zeroes(content):
    assert parse_roster(content) == [{"student_id": "000001", "student_name": "张三"}, {"student_id": "000002", "student_name": "李四"}, {"student_id": "000003", "student_name": "王五"}]


@pytest.mark.parametrize("content", ["", "学号,姓名\n", "姓名\n张三", "学号,学号,姓名\n000001,000001,张三", "学号,姓名\n000001,甲\n000001,乙", "学号,姓名\n123,甲", "学号,姓名\n00000?,甲", "学号,姓名\n1.0E+11,甲", "学号,姓名\n000001,", "学号,姓名\n000001,甲,乙", "学号,姓名\n000001,\"甲", "学号,姓名\n000001,"+"甲"*41])
def test_invalid_roster_is_rejected(content):
    with pytest.raises(ValueError):
        parse_roster(content)


def test_blank_lines_and_fullwidth_digits():
    assert parse_roster("\n学号,姓名\n\n０００００１, 张 三 \n")[0] == {"student_id": "000001", "student_name": "张 三"}


def test_roster_size_and_rows(monkeypatch):
    import exam_roster
    monkeypatch.setattr(exam_roster, "MAX_ROSTER_ROWS", 1)
    with pytest.raises(ValueError):
        parse_roster(CONTENT)
    monkeypatch.setattr(exam_roster, "MAX_ROSTER_BYTES", 10)
    with pytest.raises(ValueError):
        parse_roster(CONTENT.encode())


@pytest.fixture
def env(tmp_path, monkeypatch):
    imports, reviews = tmp_path / "imports", tmp_path / "reviews"
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", imports)
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", reviews)
    monkeypatch.setattr(scan_ui, "PLATFORM_PERSISTENCE", SimpleNamespace(sync_file=Mock()))
    monkeypatch.setattr(scan_ui, "_ensure_review_display_crops", lambda *_: False)
    monkeypatch.setattr(scan_ui, "AUTH_SERVICE", SimpleNamespace(enabled=False, user_from_headers=lambda _: None))
    monkeypatch.setattr(api, "current_user", lambda _: {"id": "teacher-1"})
    manager = CandidateManager(lambda: scan_ui.REVIEW_ROOT, scan_ui._load_review, scan_ui._write_review,
                               scan_ui.AI_REVIEW_LOCK, lambda *_: None,
                               roster_provider=lambda identifier: roster_index(scan_ui.IMPORT_ROOT, identifier))
    monkeypatch.setattr(scan_ui, "CANDIDATE_MANAGER", manager)
    for identifier in ("exam-a", "exam-b"):
        directory = imports / identifier
        directory.mkdir(parents=True)
        (directory / "normalized_exam.json").write_text(json.dumps({"name": identifier,"summary":{},"exam":{"sections":[]}}), encoding="utf-8")
    def review(identifier, sid="000001", exam="exam-a", name="误识别"):
        data = {"ok": True,"review_id":identifier,"import_id":exam,"student_id":sid,
                "student_name":name,"student_name_source":"ai","name_ocr":"原始识别",
                "objective":[{"question":"1","score":2,"auto_status":"自动通过"}],"items":[],
                "score_summary":{"total_score":2,"possible_score":2},"grade_confirmed":True}
        path=reviews/identifier/"output/review.json";path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
        return path
    yield SimpleNamespace(imports=imports,reviews=reviews,manager=manager,review=review)
    manager.executor.shutdown(wait=True)


def test_preimport_before_any_answer_cards(env):
    result=scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    assert result["summary"] == {"expected":3,"present":0,"absent":3,"unmatched":0,"duplicate_ids":0}
    assert load_roster(env.imports,"exam-a")["students"][0]["student_id"]=="000001"


def test_attendance_is_per_exam_and_unique_id_with_unknown_and_duplicates(env):
    env.review("a");env.review("duplicate");env.review("partial","00000?",name="李四")
    env.review("outsider","999999");env.review("other-exam","000002","exam-b")
    result=scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    assert result["summary"] == {"expected":3,"present":1,"absent":2,"unmatched":2,"duplicate_ids":1}
    assert {row["student_id"] for row in result["students"] if not row["review_ids"]}=={"000002","000003"}
    assert result["duplicates"][0]["student_id"]=="000001"
    assert {row["reason"] for row in result["unmatched"]}=={"学号待核对","名单外学号"}


def test_identity_precedence_in_records_reviews_batches_and_export_preserves_disk(env):
    path=env.review("a");before=path.read_bytes()
    scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    record=env.manager.list()["candidates"][0]
    assert record["student_name"]=="张三" and record["roster_matched"]
    assert record["student_name_source"]=="roster" and record["name_ocr"]=="原始识别"
    assert scan_ui.export_confirmed_grades(["a"])[0]["student_name"]=="张三"
    assert scan_ui._compact_review(json.loads(before))["student_name"]=="张三"
    assert path.read_bytes()==before
    assert scan_ui.read_review_status("a")["student_name"]=="张三"
    assert json.loads(path.read_bytes())["student_name"]=="误识别"


def test_roster_replacement_and_manual_id_correction_recompute_attendance(env):
    path=env.review("a","00000?")
    scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    result=env.manager.save({"review_id":"a","student_id":"000002","student_name":"随手填写","paper_type":"A"})
    assert result["candidate"]["student_name"]=="李四"
    assert json.loads(path.read_bytes())["student_name"]=="误识别"
    assert scan_ui.read_exam_roster("exam-a")["summary"]["present"]==1
    scan_ui.import_exam_roster({"import_id":"exam-a","content":"学号,姓名\n000002,新姓名"})
    assert scan_ui.export_confirmed_grades(["a"])[0]["student_name"]=="新姓名"
    scan_ui.import_exam_roster({"import_id":"exam-a","content":"学号,姓名\n000003,王五"})
    assert env.manager.list()["candidates"][0]["student_name"]=="误识别"


def test_reimport_validation_and_persistence_failure_leave_previous_roster(env):
    scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    path=env.imports/"exam-a/candidate_roster.json";before=path.read_bytes()
    with pytest.raises(ValueError):
        scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT+"000001,重复\n"})
    assert path.read_bytes()==before
    def fail(*_):raise OSError("sync failed")
    with pytest.raises(OSError):
        save_roster(env.imports,"exam-a","学号,姓名\n000001,新姓名",sync=fail)
    assert path.read_bytes()==before
    assert list(path.parent.glob(".roster-*"))==[]


def test_same_id_and_same_name_never_cross_exam_or_replace_other_ids(env):
    env.review("a","000001","exam-a",name="王五");env.review("b","000001","exam-b",name="张三")
    scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    scan_ui.import_exam_roster({"import_id":"exam-b","content":"学号,姓名\n000001,另一考生"})
    records={row["review_id"]:row for row in env.manager.list()["candidates"]}
    assert records["a"]["student_name"]=="张三" and records["b"]["student_name"]=="另一考生"
    assert scan_ui.read_exam_roster("exam-a")["summary"]["absent"]==2


def test_imported_names_bypass_automatic_name_recognition(env):
    env.review("a");scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    assert env.manager.start({})["name_job"]["total"]==0


def test_file_upload_and_cloud_storage_key(env):
    result=scan_ui.import_exam_roster({"import_id":"exam-a","file":{"name":"名单.csv","data":base64.b64encode(CONTENT.encode("gb18030")).decode()},"_actor_user_id":"teacher-1"})
    assert result["filename"]=="名单.csv"
    args=scan_ui.PLATFORM_PERSISTENCE.sync_file.call_args.args
    assert args[1:]==("exam_import","teacher-1",Path("exam-a/candidate_roster.json"))


@pytest.mark.parametrize("identifier",["","../outside","..","exam-missing"])
def test_invalid_or_missing_exam_is_rejected(env,identifier):
    with pytest.raises(ValueError):scan_ui.import_exam_roster({"import_id":identifier,"content":CONTENT})


def test_deleted_exam_keeps_historical_name_but_closes_roster_editing(env):
    scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    (env.imports/"exam-a/normalized_exam.json").write_text('{"deleted_at":"2026-09-30"}',encoding="utf-8")
    with pytest.raises(ValueError):scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    assert roster_index(env.imports,"exam-a")["000001"]=="张三"


def test_workspace_root_isolation(tmp_path):
    root=WorkspacePath(tmp_path,"imports")
    for workspace,name in [("a","甲"),("b","乙")]:
        with workspace_scope({"id":workspace}):
            directory=root/"exam-a";directory.mkdir(parents=True)
            (directory/"normalized_exam.json").write_text('{}',encoding="utf-8")
            save_roster(root,"exam-a","学号,姓名\n000001,"+name)
    for workspace,name in [("a","甲"),("b","乙")]:
        with workspace_scope({"id":workspace}):
            assert roster_index(root,"exam-a")=={"000001":name}


def test_unreadable_reviews_are_reported_for_attendance_verification(env):
    path=env.review("corrupt");path.write_text('invalid',encoding="utf-8")
    result=scan_ui.import_exam_roster({"import_id":"exam-a","content":CONTENT})
    assert result["skipped"]==1


@pytest.mark.parametrize("backend",["local","fastapi"])
def test_roster_routes_import_get_invalid_and_export(env,backend):
    env.review("a")
    if backend=="fastapi":
        client=TestClient(api.app,raise_server_exceptions=False)
        def call(method,path,payload=None):
            response=client.request(method,path,json=payload) if payload is not None else client.request(method,path)
            return response.status_code,response.json()
        cleanup=client.close
    else:
        server=scan_ui.create_server("127.0.0.1",0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def call(method,path,payload=None):
            data=json.dumps(payload).encode() if payload is not None else None
            request=Request("http://127.0.0.1:"+str(server.server_address[1])+path,data,headers={"Content-Type":"application/json"},method=method)
            try:response=urlopen(request)
            except HTTPError as error:response=error
            with response:return response.status,json.load(response)
        def cleanup():server.shutdown();server.server_close();thread.join(timeout=3)
    try:
        status,data=call("POST","/api/exam/roster",{"import_id":"exam-a","content":CONTENT})
        assert status==200 and data["summary"]["present"]==1
        status,data=call("GET","/api/exam/roster?import_id=exam-a")
        assert status==200 and data["summary"]["absent"]==2
        status,data=call("GET","/api/candidates/export.json?review_id=a")
        assert status==200 and data[0]["student_name"]=="张三"
        status,data=call("POST","/api/exam/roster",{"import_id":"exam-a","content":CONTENT+"000001,重复"})
        assert status==400 and "重复" in data["error"]
    finally:cleanup()

def test_imported_exact_name_with_punctuation_survives_identity_save(env):
    env.review("a")
    scan_ui.import_exam_roster({"import_id":"exam-a","content":"学号,姓名\n000001,张 三（甲）"})
    result=env.manager.save({"review_id":"a","student_id":"000001","student_name":"张 三（甲）","paper_type":"A"})
    assert result["candidate"]["student_name"]=="张 三（甲）"
