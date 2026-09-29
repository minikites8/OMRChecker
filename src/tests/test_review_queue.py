"""Batch queue persistence and concurrent submission tests."""
import base64
import json
import threading
from concurrent.futures import ThreadPoolExecutor

from src.tests.test_saas_workspaces import ALICE, BOB, CHARLIE, http_service, store
from workspace_context import workspace_scope

import pytest
from fastapi.testclient import TestClient

import backend.app as backend_app
import scan_ui


@pytest.fixture
def review_queue_case(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: False)
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_tree", lambda *a, **k: None)
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_file", lambda *a, **k: None)
    monkeypatch.setattr(backend_app, "current_user", lambda _request: {"id": "queue-teacher", "sub": "queue-teacher"})
    path = scan_ui.IMPORT_ROOT / "exam-queue" / "normalized_exam.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"name": "队列试卷", "exam": {"sections": []}, "answer_map": {}}), encoding="utf-8")
    payload = {"import_id": "exam-queue", "local_ocr_enabled": True,
               "card_files": [{"name": "card.pdf", "data": base64.b64encode(b"test PDF").decode("ascii")}]}
    return TestClient(backend_app.app, raise_server_exceptions=False), payload


def join_batch(batch_id):
    worker = scan_ui.BATCH_REVIEW_WORKERS.get(batch_id)
    if worker:
        worker.join(timeout=8)
        assert not worker.is_alive()


def report():
    return {"ok": True, "items": [], "objective": [], "source": {}, "review_summary": {},
            "objective_summary": {}, "score_summary": {"possible_score": 1, "total_score": 0}}


def test_queue_endpoint_lists_active_and_completed_batches(review_queue_case, monkeypatch):
    client, payload = review_queue_case
    monkeypatch.setattr(scan_ui, "build_review_from_structured", lambda *args, **kwargs: report())
    first = client.post("/api/review", json=payload)
    second = client.post("/api/review", json={**payload, "card_files": [{**payload["card_files"][0], "name": "second.pdf"}]})
    assert first.status_code == second.status_code == 202
    first_id, second_id = first.json()["batch_id"], second.json()["batch_id"]
    queue = client.get("/api/review/batch/queue")
    assert queue.status_code == 200
    assert {item["batch_id"] for item in queue.json()["tasks"]} >= {first_id, second_id}
    join_batch(first_id); join_batch(second_id)
    finished = client.get("/api/review/batch/queue").json()["tasks"]
    by_id = {item["batch_id"]: item for item in finished}
    assert by_id[first_id]["status"] == "已完成"
    assert by_id[second_id]["status"] == "已完成"


def test_second_batch_is_accepted_while_first_batch_waits(review_queue_case, monkeypatch):
    client, payload = review_queue_case
    entered = threading.Event(); release = threading.Event()
    def build(*args, **kwargs):
        entered.set(); assert release.wait(8); return report()
    monkeypatch.setattr(scan_ui, "build_review_from_structured", build)
    ids = []
    try:
        first = client.post("/api/review", json=payload)
        assert first.status_code == 202
        ids.append(first.json()["batch_id"])
        assert entered.wait(3)
        second = client.post("/api/review", json={**payload, "card_files": [{**payload["card_files"][0], "name": "queued.pdf"}]})
        assert second.status_code == 202
        ids.append(second.json()["batch_id"])
        queue = client.get("/api/review/batch/queue").json()["tasks"]
        assert set(ids) <= {item["batch_id"] for item in queue}
    finally:
        release.set()
        for batch_id in ids:
            join_batch(batch_id)
    assert all(item["status"] == "已完成" for item in client.get("/api/review/batch/queue").json()["tasks"][:2])


def test_queue_rejects_unauthenticated_requests(review_queue_case, monkeypatch):
    client, payload = review_queue_case
    monkeypatch.setattr(backend_app, "current_user", lambda _request: (_ for _ in ()).throw(backend_app.HTTPException(401, "需要登录")))
    response = client.get("/api/review/batch/queue")
    assert response.status_code == 401


@pytest.mark.parametrize("workers", [1, 2])
def test_batches_wait_for_real_slots_and_use_available_capacity(review_queue_case, monkeypatch, workers):
    client, payload = review_queue_case
    pool = ThreadPoolExecutor(max_workers=workers)
    monkeypatch.setattr(scan_ui, "REVIEW_EXECUTOR", pool)
    entered, release, next_started = threading.Event(), threading.Event(), threading.Event()
    ids = []

    def create(import_id, normalized_path, imported, files, batch_id, index, label, *args):
        if label == "first-0":
            entered.set()
            assert release.wait(10)
        if label == "next":
            next_started.set()
        return report()

    monkeypatch.setattr(scan_ui, "_create_review_report", create)
    first_payload = {**payload, "concurrency": 1, "card_groups": [
        {"label": f"first-{i}", "files": payload["card_files"]} for i in range(4)]}
    next_payload = {**payload, "concurrency": 1, "card_groups": [
        {"label": "next", "files": payload["card_files"]}]}
    try:
        first = client.post("/api/review/batch", json=first_payload)
        assert first.status_code == 200
        ids.append(first.json()["batch_id"])
        assert entered.wait(3)
        second = client.post("/api/review/batch", json=next_payload)
        assert second.status_code == 200
        ids.append(second.json()["batch_id"])
        if workers == 2:
            assert next_started.wait(3), "the second pool slot must serve the next batch"
        else:
            assert not next_started.is_set()
        queue = {t["batch_id"]: t for t in client.get("/api/review/batch/queue").json()["tasks"]}
        assert queue[ids[0]]["processing"] == 1
        assert queue[ids[0]]["waiting"] == 3
        if workers == 1:
            assert queue[ids[1]]["status"] == "等待中"
            assert queue[ids[1]]["waiting"] == 1
            assert queue[ids[1]]["processing"] == 0
    finally:
        release.set()
        for batch_id in ids:
            join_batch(batch_id)
        pool.shutdown(wait=True)
    assert next_started.is_set()
    assert all(scan_ui.read_batch_status(i)["status"] == "已完成" for i in ids)


def test_history_limit_preserves_all_active_batches(review_queue_case):
    for i in range(6):
        active = i < 4
        batch_id = f"history-{i}"
        path = scan_ui.REVIEW_ROOT / "batches" / batch_id / "batch.json"
        scan_ui._write_batch(path, {"ok": True, "batch_id": batch_id, "created_at": i + 1,
                                  "status": "等待中" if active else "已完成", "reviews": []})
    tasks = scan_ui.read_batch_queue(limit=1)["tasks"]
    assert {task["batch_id"] for task in tasks} == {"history-0", "history-1", "history-2", "history-3", "history-5"}


def test_queue_respects_workspace_membership_and_paths(http_service):
    client, store, _root = http_service
    first, second = store.create(ALICE, "甲班队列"), store.create(BOB, "乙班队列")
    for workspace, batch_id in [(first, "alice-batch"), (second, "bob-batch")]:
        with workspace_scope(workspace):
            path = scan_ui.REVIEW_ROOT / "batches" / batch_id / "batch.json"
            scan_ui._write_batch(path, {"ok": True, "batch_id": batch_id, "status": "等待中", "reviews": []})
    for workspace, cookie, expected in [(first, "alice", "alice-batch"), (second, "bob", "bob-batch")]:
        result = client.get(f"/w/{workspace['id']}/api/review/batch/queue", headers={"cookie": cookie})
        assert result.status_code == 200
        assert [t["batch_id"] for t in result.json()["tasks"]] == [expected]
    for cookie in ["bob", "charlie"]:
        assert client.get(f"/w/{first['id']}/api/review/batch/queue", headers={"cookie": cookie}).status_code == 404
    assert client.get(f"/w/{first['id']}/api/review/batch/queue").status_code == 401



def test_batch_status_keeps_ai_started_during_the_status_read(review_queue_case, monkeypatch):
    path = scan_ui.REVIEW_ROOT / "batches" / "ai-race" / "batch.json"
    first = {"status": "已完成", "review_id": "first", "ai_judgment": {"status": "已完成"}}
    second = {"status": "等待中", "review_id": ""}
    batch = {"ok": True, "batch_id": "ai-race", "status": "处理中", "reviews": [first, second]}
    scan_ui._write_batch(path, batch)

    def read(review_id):
        latest = json.loads(path.read_text(encoding="utf-8"))
        latest.update(status="已完成", completed=2, total=2)
        latest["reviews"][1] = {"status": "已完成", "review_id": "second", "ai_judgment": {"status": "处理中"}}
        scan_ui._write_batch(path, latest)
        return {**report(), **first}

    monkeypatch.setattr(scan_ui, "read_review_status", read)
    result = scan_ui.read_batch_status("ai-race")
    assert result["status"] == "已完成"
    assert result["ai_processing"] == 1
    assert result["phase"] == "AI处理中"
