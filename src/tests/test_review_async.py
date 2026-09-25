"""单份批改后台受理、轮询及失败状态的回归测试。"""
import base64
import json
import threading

import pytest
from fastapi.testclient import TestClient

import backend.app as backend_app
import scan_ui


@pytest.fixture
def review_case(monkeypatch, tmp_path):
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: False)
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_tree", lambda *a, **k: None)
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_file", lambda *a, **k: None)
    monkeypatch.setattr(backend_app, "current_user", lambda _request: {"id": "test-user", "sub": "test-user"})
    path = scan_ui.IMPORT_ROOT / "exam-1" / "normalized_exam.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"exam": {"sections": []}, "answer_map": {}}), encoding="utf-8")
    payload = {"import_id": "exam-1", "local_ocr_enabled": True,
               "card_files": [{"name": "card.pdf", "data": base64.b64encode(b"test PDF").decode("ascii")}] }
    return TestClient(backend_app.app, raise_server_exceptions=False), payload


def join_batch(batch_id):
    worker = scan_ui.BATCH_REVIEW_WORKERS.get(batch_id)
    if worker:
        worker.join(timeout=5)
        assert not worker.is_alive()


def fake_report():
    return {"ok": True, "items": [], "objective": [], "source": {},
            "review_summary": {}, "objective_summary": {},
            "score_summary": {"possible_score": 1, "total_score": 0}}


def test_single_review_returns_202_before_recognition_and_status_reaches_result(review_case, monkeypatch):
    client, payload = review_case
    entered, release = threading.Event(), threading.Event()

    def build(*args, **kwargs):
        entered.set()
        assert release.wait(5), "test worker release missing"
        return fake_report()

    monkeypatch.setattr(scan_ui, "build_review_from_structured", build)
    response = client.post("/api/review", json=payload)
    assert response.status_code == 202
    accepted = response.json()
    batch_id = accepted["batch_id"]
    assert accepted["total"] == 1
    assert accepted["concurrency"] == 1
    assert entered.wait(2)
    progress = client.get("/api/review/batch/status", params={"batch_id": batch_id}).json()
    assert progress["status"] == "处理中"
    assert progress["completed"] == progress["failed"] == 0
    release.set()
    join_batch(batch_id)
    finished = client.get("/api/review/batch/status", params={"batch_id": batch_id}).json()
    assert finished["status"] == "已完成"
    assert finished["completed"] == 1
    review_id = finished["reviews"][0]["review_id"]
    review = client.get("/api/review/status", params={"review_id": review_id}).json()
    assert review["owner_user_id"] == "test-user"
    assert review["import_id"] == payload["import_id"]
    assert review["local_ocr_enabled"] is True
    assert review["card_files"] == ["card.pdf"]


def test_background_failure_is_terminal_and_trace_is_logged(review_case, monkeypatch, caplog):
    client, payload = review_case

    def fail(*args, **kwargs):
        raise RuntimeError("test-private-diagnostic")

    monkeypatch.setattr(scan_ui, "build_review_from_structured", fail)
    response = client.post("/api/review", json=payload)
    assert response.status_code == 202
    batch_id = response.json()["batch_id"]
    join_batch(batch_id)
    result = client.get("/api/review/batch/status", params={"batch_id": batch_id})
    body = result.json()
    assert body["status"] == "部分完成"
    assert body["failed"] == 1
    assert body["reviews"][0]["status"] == "失败"
    assert "test-private-diagnostic" not in result.text
    assert "test-private-diagnostic" in caplog.text
    assert batch_id in caplog.text


@pytest.mark.parametrize("files", [[], "bad", [None], [{"name": "card.exe", "data": "AA=="}], [{"name": "card.pdf", "data": "wrong!!"}]])
def test_invalid_upload_is_rejected_before_queue(review_case, monkeypatch, files):
    client, payload = review_case
    monkeypatch.setattr(scan_ui, "start_batch_review", lambda *_: pytest.fail("invalid upload entered queue"))
    response = client.post("/api/review", json={**payload, "card_files": files})
    assert response.status_code == 400
    assert response.json()["ok"] is False


def test_single_review_keeps_all_pages_and_request_metadata(review_case, monkeypatch):
    _client, payload = review_case
    files = [{**payload["card_files"][0], "name": f"page-{i}.png"} for i in range(3)]
    payload = {**payload, "card_files": files, "template_id": "custom", "_actor_user_id": "owner",
               "concurrency": 4, "card_groups": [{"files": []}]}
    received = {}

    def start(value):
        received.update(value)
        return {"ok": True, "batch_id": "queued"}

    monkeypatch.setattr(scan_ui, "start_batch_review", start)
    assert scan_ui.start_review_job(payload)["batch_id"] == "queued"
    assert len(received["card_groups"]) == 1
    assert received["card_groups"][0]["files"] == files
    assert received["concurrency"] == 1
    assert received["template_id"] == "custom"
    assert received["_actor_user_id"] == "owner"
    assert received["local_ocr_enabled"] is True
    assert payload["concurrency"] == 4
    assert payload["card_groups"] == [{"files": []}]
