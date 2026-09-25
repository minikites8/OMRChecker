"""Selection/export contracts shared by the local and separated HTTP backends."""
import copy
import json
import threading
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest
from fastapi.testclient import TestClient

import backend.app as api
import scan_ui


@pytest.fixture
def export_records(monkeypatch):
    records = [
        {"review_id": "review-01", "student_name": "考生甲", "student_id": "000000000001",
         "grade_confirmed": False, "grade_confirmation_status": "待确认", "grade_blockers": [],
         "score_summary": {"total_score": 83, "possible_score": 100},
         "question_scores": [{"question": "1", "awarded_score": 2}]},
        {"review_id": "review-02", "student_name": "考生乙", "grade_confirmed": True,
         "grade_confirmation_status": "已确认", "grade_confirmed_at": "2026-09-25T12:00:00",
         "score_summary": {"total_score": 42, "possible_score": 100}},
        {"review_id": "review-03", "student_name": "考生丙", "grade_confirmed": False,
         "grade_confirmation_status": "待处理", "grade_blockers": ["第1题待复核"],
         "score_summary": {"total_score": 0, "possible_score": 100}},
        {"review_id": "review-04", "score_summary": {}},
    ]
    monkeypatch.setattr(scan_ui, "CANDIDATE_MANAGER", SimpleNamespace(records=lambda: (copy.deepcopy(records), 0)))
    return records


@pytest.fixture(params=["local", "fastapi"])
def export_http(request, monkeypatch, export_records):
    if request.param == "fastapi":
        monkeypatch.setattr(api, "current_user", lambda _: {"id": "test-user"})
        client = TestClient(api.app, raise_server_exceptions=False)
        def get(query):
            response = client.get("/api/candidates/export.json" + query)
            return response.status_code, response.json(), response.headers
        yield get
        client.close()
    else:
        monkeypatch.setattr(scan_ui, "AUTH_SERVICE", SimpleNamespace(enabled=False, user_from_headers=lambda _cookie: None))
        server = scan_ui.create_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def get(query):
            try:
                response = urlopen("http://127.0.0.1:" + str(server.server_address[1]) + "/api/candidates/export.json" + query)
            except HTTPError as error:
                response = error
            with response:
                return response.status, json.load(response), response.headers
        try:
            yield get
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


@pytest.mark.parametrize("query,ids", [
    ("?review_id=review-01", {"review-01"}),
    ("?review_id=review-01&review_id=review-03", {"review-01", "review-03"}),
    ("?review_ids=review-01,review-02", {"review-01", "review-02"}),
    ("?review_ids=review-03", {"review-03"}),
    ("?review_id=review-01&review_ids=review-03", {"review-01", "review-03"}),
    ("?review_id=%20review-01%20&review_id=review-01", {"review-01"}),
    ("", {"review-02"}),
])
def test_export_http_matches_exact_selection(export_http, query, ids):
    status, result, headers = export_http(query)
    assert status == 200
    assert result["ok"] is True
    assert result["count"] == len(ids)
    assert {row["review_id"] for row in result["grades"]} == ids
    assert headers["Cache-Control"] == "no-store"
    assert headers["Content-Disposition"].startswith("attachment;")


@pytest.mark.parametrize("query", [
    "?review_id=", "?review_ids=%20", "?review_id=review-missing",
    "?review_id=review-04", "?review_id=review-01&review_id=review-04",
    "?review_id=review-01&review_id=review-missing",
])
def test_stale_or_empty_selection_returns_actionable_json(export_http, query):
    status, result, _ = export_http(query)
    assert status == 400
    assert result["ok"] is False
    assert result["error"]


def test_export_preserves_confirmation_status_zero_score_and_question_details(export_records):
    before = copy.deepcopy(export_records)
    exported = scan_ui.export_confirmed_grades(["review-01", "review-03"])
    first, zero = exported["grades"]
    assert first["score"] == 83 and first["student_id"] == "000000000001"
    assert first["grade_confirmed"] is False and first["grade_confirmation_status"] == "待确认"
    assert first["question_scores"] == [{"question": "1", "awarded_score": 2}]
    assert zero["score"] == zero["percentage"] == 0
    assert zero["grade_blockers"] == ["第1题待复核"] and zero["confirmed_at"] == ""
    assert export_records == before


@pytest.mark.parametrize("total,possible,eligible", [
    (83, 100, True), (0, 100, True), ("0", "100", True), (None, 100, False),
    ("", 100, False), (float("inf"), 100, False), (float("nan"), 100, False),
    (True, 100, False), ([], 100, False), (83, 0, False), (83, -1, False), (83, True, False),
])
def test_export_score_availability(total, possible, eligible):
    assert scan_ui._has_exportable_score({"score_summary": {"total_score": total, "possible_score": possible}}) is eligible
