"""复核 API 错误响应契约。"""
from pathlib import Path

from fastapi.testclient import TestClient

import backend.app as backend_app


def _client(monkeypatch, operation):
    monkeypatch.setattr(backend_app, "current_user", lambda _request: {"id": "test-user", "sub": "test-user"})
    monkeypatch.setattr(backend_app.legacy, "run_review_job", operation)
    return TestClient(backend_app.app, raise_server_exceptions=False)


def test_review_endpoint_returns_json_for_validation_error(monkeypatch):
    client = _client(monkeypatch, lambda _payload: (_ for _ in ()).throw(ValueError("请先导入结构化试卷与答案")))
    response = client.post("/api/review", json={})
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"ok": False, "error": "请先导入结构化试卷与答案"}


def test_review_endpoint_returns_json_for_scan_failure(monkeypatch):
    error = backend_app.legacy.ScanFailure("扫描程序退出状态为 1", "日志最后一行")
    client = _client(monkeypatch, lambda _payload: (_ for _ in ()).throw(error))
    response = client.post("/api/review", json={"import_id": "demo", "card_files": []})
    assert response.status_code == 502
    assert response.json() == {"ok": False, "error": "扫描程序退出状态为 1", "log_tail": "日志最后一行"}


def test_review_endpoint_hides_unexpected_exception_and_logs(monkeypatch, caplog):
    client = _client(monkeypatch, lambda _payload: (_ for _ in ()).throw(RuntimeError("database secret")))
    response = client.post("/api/review", json={"import_id": "demo", "card_files": []})
    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "批改服务暂时失败，请稍后重试"}
    assert "database secret" in caplog.text
    assert "Internal Server Error" not in response.text


def test_review_endpoint_json_encodes_report_values(monkeypatch):
    client = _client(monkeypatch, lambda _payload: {"ok": True, "report_path": Path("review.json")})
    response = client.post("/api/review", json={"import_id": "demo", "card_files": []})
    assert response.status_code == 200
    assert response.json() == {"ok": True, "report_path": "review.json"}


def test_frontend_reads_json_or_plain_error_response():
    source = (Path(__file__).resolve().parents[2] / "ui" / "app.js").read_text(encoding="utf-8")
    assert "async function readReviewResponse(response)" in source
    assert "服务器返回了无效响应" in source
    assert "readReviewResponse(response)" in source
