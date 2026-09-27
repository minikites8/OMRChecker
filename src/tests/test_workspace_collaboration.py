"""Two-account and concurrent shared-workspace review regression tests."""
import copy
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import backend.app as api
import scan_ui
from review_collaboration import SharedReviewLock, question_revision


@pytest.fixture
def shared(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "_ensure_review_scores", lambda report: False)
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_file", lambda *a, **k: None)
    monkeypatch.setattr(scan_ui, "grade_confirmation_blockers", lambda report: [])
    monkeypatch.setattr(api, "current_user", lambda request: {
        "id": request.headers.get("X-Test-User", "teacher-a"), "role": "teacher",
        "display_name": "老师" + request.headers.get("X-Test-User", "teacher-a")})
    report = {"ok": True, "review_id": "shared-review", "owner_user_id": "teacher-a",
              "student_id": "202619240110", "student_name": "张同学", "paper_type": "A",
              "items": [{"question": q, "score": 2, "auto_status": "待复核",
                         "expected_answer": "answer", "manual_status": "", "manual_text": ""}
                        for q in ("41", "42")],
              "objective": [{"question": "01", "score": 1, "recognized": "A", "expected": "A",
                             "auto_status": "自动通过", "final_status": "自动通过"}],
              "score_summary": {"possible_score": 5, "total_score": 1}}
    path = scan_ui.REVIEW_ROOT / "shared-review/output/review.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    return TestClient(api.app, raise_server_exceptions=False), path


def read(client):
    response = client.get("/api/review/status", params={"review_id": "shared-review"})
    assert response.status_code == 200
    return response.json()


def decision(report, question="41", kind="subjective", status="通过"):
    field = "decisions" if kind == "subjective" else "objective_decisions"
    return {"review_id": report["review_id"], "expected_revision": report["collaboration"]["revision"],
            field: [{"question": question, "status": status,
                     "expected_revision": report["collaboration"]["questions"][kind][question]}]}


def save(client, payload, actor="teacher-a"):
    return client.post("/api/review/confirm", json=payload, headers={"X-Test-User": actor})


def test_two_teachers_merge_different_questions_and_record_real_actors(shared):
    client, path = shared
    initial = read(client)
    assert save(client, decision(initial)).status_code == 200
    payload = decision(initial, "42")
    payload.update(_actor_user_id="spoof", _actor_display_name="spoof", _require_revision=False)
    second = save(client, payload, "teacher-b")
    assert second.status_code == 200
    report = read(client)
    assert [item["manual_status"] for item in report["items"]] == ["通过", "通过"]
    assert report["score_summary"]["total_score"] == 5
    assert [item["reviewed_by"]["id"] for item in report["items"]] == ["teacher-a", "teacher-b"]
    assert [event["actor"]["id"] for event in report["review_activity"]] == ["teacher-a", "teacher-b"]
    assert json.loads(path.read_text(encoding="utf-8"))["collaboration"]["workspace_id"] == "shared"


@pytest.mark.parametrize("kind,question", [("subjective", "41"), ("objective", "01")])
def test_stale_same_question_returns_409_and_preserves_disk(shared, kind, question):
    client, path = shared
    initial = read(client)
    assert save(client, decision(initial, question, kind)).status_code == 200
    before = path.read_bytes()
    response = save(client, decision(initial, question, kind, "不通过"), "teacher-b")
    assert response.status_code == 409
    assert response.json()["code"] == "review_conflict"
    assert response.json()["questions"] == [{"kind": kind, "question": question}]
    assert path.read_bytes() == before


def test_missing_revision_returns_428_and_rejects_spoofed_bypass(shared):
    client, path = shared
    before = path.read_bytes()
    response = save(client, {"review_id": "shared-review", "_require_revision": False,
                             "decisions": [{"question": "41", "status": "通过"}]})
    assert response.status_code == 428
    assert path.read_bytes() == before


def test_grade_confirmation_uses_whole_review_revision(shared):
    client, path = shared
    initial = read(client)
    saved = save(client, decision(initial), "teacher-b").json()
    stale = client.post("/api/review/confirm-grade", json={"review_id": "shared-review",
                        "expected_revision": initial["collaboration"]["revision"]})
    assert stale.status_code == 409
    confirmed = client.post("/api/review/confirm-grade", json={"review_id": "shared-review",
                            "expected_revision": saved["collaboration"]["revision"]},
                            headers={"X-Test-User": "teacher-c"})
    assert confirmed.status_code == 200
    assert confirmed.json()["grade_confirmed_by"]["id"] == "teacher-c"
    assert confirmed.json()["review_activity"][-1]["action"] == "grade.confirmed"
    updated = save(client, decision(confirmed.json(), "42"), "teacher-a").json()
    assert updated["grade_confirmed"] is False
    assert updated["grade_confirmed_by"] is None


def test_confirmation_save_rejects_changes_on_another_question_atomically(shared):
    client, path = shared
    initial = read(client)
    save(client, decision(initial))
    before = path.read_bytes()
    payload = decision(initial, "42")
    payload["for_confirmation"] = True
    response = save(client, payload, "teacher-b")
    assert response.status_code == 409
    assert path.read_bytes() == before


@pytest.mark.parametrize("same_question", [True, False])
def test_concurrent_submissions_are_serialized(shared, same_question):
    client, _ = shared
    initial = read(client)
    barrier = threading.Barrier(2)
    def submit(actor, question):
        barrier.wait(timeout=5)
        return save(client, decision(initial, question), actor).status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(submit, "teacher-a", "41"),
                   executor.submit(submit, "teacher-b", "41" if same_question else "42")]
        statuses = sorted(f.result(timeout=10) for f in futures)
    assert statuses == ([200, 409] if same_question else [200, 200])


@pytest.mark.parametrize("whole_review", [True, False])
def test_ai_completion_preserves_concurrent_human_decision(shared, monkeypatch, whole_review):
    client, _ = shared
    initial = read(client)
    entered, release = threading.Event(), threading.Event()
    def ai(report, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        updated = copy.deepcopy(report)
        updated["items"][0].update(ai_status="AI不通过", awarded_score=0, final_status="不通过")
        updated["score_summary"] = {"total_score": 0, "possible_score": 5}
        updated["review_summary"] = {}
        updated["ai_judgment"] = {"status": "已完成"}
        updated["ai_question_judgment"] = {"status": "已完成", "question": "41"}
        return updated
    monkeypatch.setattr(scan_ui, "apply_ai_review" if whole_review else "apply_ai_review_question", ai)
    with ThreadPoolExecutor(max_workers=1) as executor:
        operation = scan_ui.run_ai_review if whole_review else scan_ui.run_ai_question_review
        future = executor.submit(operation, {"review_id": "shared-review", "question": "41"})
        assert entered.wait(3)
        response = save(client, decision(initial), "teacher-b")
        release.set()
        assert response.status_code == 200
        future.result(timeout=10)
    final = read(client)
    assert final["items"][0]["manual_status"] == "通过"
    assert final["items"][0]["final_status"] == "通过"
    assert final["items"][0]["awarded_score"] == 2
    assert final["score_summary"]["total_score"] == 3
    assert final["items"][0]["reviewed_by"]["id"] == "teacher-b"


def test_invalid_multi_question_request_has_no_partial_write(shared):
    client, path = shared
    initial = read(client)
    payload = decision(initial)
    payload["decisions"].append({"question": "missing", "status": "通过"})
    before = path.read_bytes()
    assert save(client, payload).status_code == 400
    assert path.read_bytes() == before


def test_version_ignores_runtime_paths_and_tracks_human_events():
    item = {"question": "1", "manual_status": "通过"}
    assert question_revision(item) == question_revision({**item, "handwriting_images": ["local-path"]})
    assert question_revision(item) != question_revision({**item, "review_event_id": "another-save"})


def test_shared_lock_is_reentrant_and_serializes_separate_processes(tmp_path):
    path = tmp_path / "lock"
    with SharedReviewLock(lambda: path) as lock:
        with lock:
            assert path.exists()
    counter = tmp_path / "counter.txt"
    counter.write_text("0", encoding="utf-8")
    code = """from pathlib import Path
import sys, time
from review_collaboration import SharedReviewLock
counter=Path(sys.argv[1]); lock=SharedReviewLock(lambda: counter.with_suffix('.lock'))
for _ in range(30):
    with lock:
        value=int(counter.read_text(encoding='utf-8'))
        time.sleep(0.002)
        counter.write_text(str(value+1),encoding='utf-8')
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
    processes = [subprocess.Popen([sys.executable, "-c", code, str(counter)], env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(3)]
    for process in processes:
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr.decode("utf-8", errors="replace")
    assert counter.read_text(encoding="utf-8") == "90"


@pytest.mark.parametrize("route", ["/api/review/confirm", "/api/review/confirm-grade"])
def test_authenticated_api_requires_login(monkeypatch, route):
    from types import SimpleNamespace
    monkeypatch.setattr(api, "auth", SimpleNamespace(enabled=True, user_from_headers=lambda cookie: None))
    client = TestClient(api.app, raise_server_exceptions=False)
    assert client.post(route, json={"review_id": "private"}).status_code == 401


def test_legacy_authenticated_service_uses_same_conflict_protocol(shared, monkeypatch):
    from types import SimpleNamespace
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
    client, _ = shared
    initial = read(client)
    monkeypatch.setattr(scan_ui, "AUTH_SERVICE", SimpleNamespace(
        enabled=True, user_from_headers=lambda cookie: {"id": cookie, "display_name": cookie, "role": "teacher"}))
    server = scan_ui.create_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:" + str(server.server_address[1])
    def post(payload, actor):
        request = Request(base + "/api/review/confirm", data=json.dumps(payload).encode("utf-8"),
                          headers={"Content-Type": "application/json", "Cookie": actor})
        with urlopen(request, timeout=5) as response:
            return json.load(response)
    try:
        first = post(decision(initial), "teacher-a")
        assert first["items"][0]["reviewed_by"]["id"] == "teacher-a"
        with pytest.raises(HTTPError) as conflict:
            post(decision(initial), "teacher-b")
        assert conflict.value.code == 409
        assert json.load(conflict.value)["code"] == "review_conflict"
        with pytest.raises(HTTPError) as missing:
            post({"review_id": "shared-review", "decisions": [{"question": "42", "status": "通过"}]}, "teacher-b")
        assert missing.value.code == 428
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
