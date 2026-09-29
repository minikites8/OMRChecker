"""Saved-exam regrading, 98/100 regression, and reference/native-ID alignment."""
import copy
import json
from concurrent.futures import Future
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

import exam_review
import scan_ui
from backend import app as api
from exam_import import import_exam_and_answers
from exam_regrade import _prepare_review, regrade_exam
from review_collaboration import describe_review

FIXTURE = Path(__file__).parent / "fixtures/answer_import_single_number"


@pytest.fixture
def saved(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    monkeypatch.setattr(scan_ui, "PLATFORM_PERSISTENCE", SimpleNamespace(sync_file=Mock()))
    monkeypatch.setattr(scan_ui, "ai_is_configured", lambda: False)
    monkeypatch.setattr(scan_ui, "AI_REVIEW_WORKERS", {})
    monkeypatch.setattr(scan_ui, "AI_QUESTION_WORKERS", {})
    imported = import_exam_and_answers((FIXTURE / "exam.json").read_text(encoding="utf-8"),
                                       (FIXTURE / "answers.json").read_text(encoding="utf-8"))
    imported["name"] = "软件B卷"
    normalized = scan_ui.IMPORT_ROOT / "paper" / "normalized_exam.json"
    normalized.parent.mkdir(parents=True)
    normalized.write_text(json.dumps(imported, ensure_ascii=False), encoding="utf-8")
    scores = exam_review._structured_score_map(imported["exam"])
    review = {"ok": True, "review_id": "saved-1", "import_id": "paper", "student_name": "考生",
              "student_id": "123", "owner_user_id": "teacher", "answer_paper_type": "B",
              "grade_confirmed": True, "grade_confirmed_at": "earlier", "card_files": ["scan.pdf"],
              "objective": [{"question": str(i), "recognized": "A", "expected": "B",
                             "score": scores.get(str(i), 0), "auto_status": "需人工复核"}
                            for i in range(1, 31)],
              "items": [{"question": str(i), "score": 0 if i in (59, 60) else scores.get(str(i), 0),
                         "recognized_text": "", "confidence": 0, "expected_answer": "stale",
                         "source_content": "old", "auto_status": "需人工复核",
                         "ai_status": "AI通过", "ai_score": 1,
                         "handwriting_urls": ["/reviews/saved-1/output/handwriting/" + str(i) + ".png"]}
                        for i in range(31, 65)], "score_summary": {"possible_score": 98}}
    review["items"][0].update(manual_status="通过", manual_text="approved", reviewed_by={"id": "teacher"})
    review["items"][-1].update(manual_status="部分得分", manual_score=2)
    review["objective"][0].update(manual_status="通过", override_answer=True, reviewed_answer="A")
    path = scan_ui.REVIEW_ROOT / "saved-1" / "output" / "review.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    return imported, review, path


def test_full_paper_local_score_map_is_100_and_keeps_native_ids(saved):
    imported, _, _ = saved
    scores = exam_review._structured_score_map(imported["exam"])
    assert sum(scores.get(str(i), 0) for i in range(1, 65)) == 100
    assert scores["59"] == scores["60"] == 1
    questions = {q["id"]: q for s in imported["exam"]["sections"] for q in s["questions"]}
    assert questions[203]["question_ids"] == ["203"]
    assert questions[204]["question_ids"] == ["204"]
    assert imported["summary"]["answer_count"] == 63
    assert imported["summary"]["answer_missing"] == ["206"]


def test_regrade_corrects_saved_98_and_preserves_manual_identity_images(saved):
    imported, old, path = saved
    result = regrade_exam({"import_id": "paper", "_actor_user_id": "teacher"})
    current = json.loads(path.read_text(encoding="utf-8"))
    assert result["completed"] == result["total"] == 1
    assert result["failed"] == 0
    assert current["score_summary"]["possible_score"] == 100
    assert current["score_summary"]["objective_possible"] == 55
    assert current["score_summary"]["text_possible"] == 45
    assert current["grade_confirmed"] is False
    for field in ("student_id", "student_name", "card_files", "owner_user_id"):
        assert current[field] == old[field]
    assert current["items"][0]["manual_text"] == "approved"
    assert current["items"][0]["manual_status"] == "通过"
    assert current["items"][0]["reviewed_by"] == {"id": "teacher"}
    assert current["items"][-1]["manual_score"] == 2
    assert current["items"][-1]["awarded_score"] == 2
    assert current["objective"][0]["manual_status"] == "通过"
    for before, after in zip(old["items"], current["items"]):
        assert before["handwriting_urls"] == after["handwriting_urls"]
        assert "ai_score" not in after
    q59 = next(q for q in current["items"] if q["question"] == "59")
    assert q59["expected_answer"] == "(7) double area = PI * r * r;"
    assert q59["id"] == 203
    assert current["review_activity"][-1]["action"] == "review.regraded"
    assert describe_review(old)["revision"] != describe_review(current)["revision"]
    backup = json.loads(next((path.parent / "regrade_history").glob("*.json")).read_text(encoding="utf-8"))
    assert backup["score_summary"]["possible_score"] == 98
    assert backup["grade_confirmed"] is True
    assert imported == json.loads((scan_ui.IMPORT_ROOT / "paper/normalized_exam.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("mode", ["full", "question"])
def test_regrade_skips_busy_review(saved, mode):
    _, old, path = saved
    task = Future()
    if mode == "full":
        scan_ui.AI_REVIEW_WORKERS["saved-1"] = task
    else:
        scan_ui.AI_QUESTION_WORKERS["saved-1:59"] = task
    result = regrade_exam({"import_id": "paper"})
    assert result["completed"] == 0
    assert result["skipped_busy"] == ["saved-1"]
    assert json.loads(path.read_text(encoding="utf-8")) == old
    assert not (path.parent / "regrade_history").exists()


def test_regrade_refreshes_existing_ai_through_shared_queue(saved, monkeypatch):
    _, _, path = saved
    def enqueue(payload):
        current = json.loads(path.read_text(encoding="utf-8"))
        assert current["score_summary"]["possible_score"] == 100
        assert current["items"][28]["expected_answer"]
        current["ai_judgment"] = {"status": "处理中"}
        return current
    starter = Mock(side_effect=enqueue)
    monkeypatch.setattr(scan_ui, "start_ai_review", starter)
    result = regrade_exam({"import_id": "paper"})
    starter.assert_called_once_with({"review_id": "saved-1"})
    assert result["ai_processing"] == 1
    assert result["phase"] == "AI处理中"


def test_regrade_preserves_other_papers(saved):
    _, old, _ = saved
    other = copy.deepcopy(old); other.update(review_id="other", import_id="other-paper")
    path = scan_ui.REVIEW_ROOT / "other/output/review.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(other), encoding="utf-8")
    before = path.read_bytes()
    result = regrade_exam({"import_id": "paper"})
    assert result["completed"] == 1
    assert path.read_bytes() == before
    with pytest.raises(ValueError, match="不匹配"):
        regrade_exam({"import_id": "paper", "review_ids": ["other"]})


def test_failed_backup_keeps_original_record(saved):
    _, _, path = saved
    before = path.read_bytes()
    scan_ui.PLATFORM_PERSISTENCE.sync_file.side_effect = OSError("backup failed")
    result = regrade_exam({"import_id": "paper"})
    assert result["failed"] == 1 and result["completed"] == 0
    assert path.read_bytes() == before


def test_repeated_regrade_keeps_distinct_backups_and_deduplicates_ids(saved):
    _, _, path = saved
    first = regrade_exam({"import_id": "paper", "review_ids": ["saved-1", "saved-1"]})
    second = regrade_exam({"import_id": "paper", "review_ids": ["saved-1"]})
    assert first["total"] == second["total"] == 1
    assert first["batch_id"] != second["batch_id"]
    assert len(list((path.parent / "regrade_history").glob("*.json"))) == 2
    assert json.loads(path.read_text(encoding="utf-8"))["score_summary"]["possible_score"] == 100


def test_selected_variant_is_used_and_requires_known_variant(saved):
    imported, review, _ = saved
    a = copy.deepcopy(imported); b = copy.deepcopy(imported)
    b["exam"]["sections"][0]["questions"][0]["score"] = 7
    b["answer_map"]["164"] = "D"
    variants = {"paper_variants": {"A": a, "B": b}}
    result = _prepare_review(review, variants, "run")
    assert result["objective"][0]["score"] == 7
    assert result["objective"][0]["expected"] == "D"
    review["answer_paper_type"] = ""
    with pytest.raises(ValueError, match="卷型"):
        _prepare_review(review, variants, "run")


def test_regrade_endpoint_accepts_saved_paper_without_upload(saved, monkeypatch):
    monkeypatch.setattr(api, "current_user", lambda request: {"id": "teacher", "sub": "teacher"})
    client = TestClient(api.app, raise_server_exceptions=False)
    response = client.post("/api/exam/regrade", json={"import_id": "paper"})
    assert response.status_code == 200, response.text
    assert response.json()["completed"] == 1
    assert response.json()["reviews"][0]["score_summary"]["possible_score"] == 100
    response = client.post("/api/exam/regrade", json={"import_id": "../invalid"})
    assert response.status_code == 400


def test_existing_import_without_saved_answers_has_clear_error(saved):
    _, _, path = saved
    path.unlink()
    with pytest.raises(ValueError, match="暂无已保存答卷"):
        regrade_exam({"import_id": "paper"})


def test_library_exposes_regrade_confirmation_and_flushes_edits():
    source = (Path(__file__).resolve().parents[2] / "ui/platform.js").read_text(encoding="utf-8")
    assert "async function regradeSavedExam(entry, button)" in source
    assert "regrade.dataset.action = 'regrade-exam'" in source
    assert "actionWrap.append(use, regrade, download, remove)" in source
    function = source.split("async function regradeSavedExam", 1)[1].split("function renderPapers", 1)[0]
    assert function.index("window.confirm") < function.index("reviewAutosave.flush") < function.index("fetch('/api/exam/regrade'")
    assert "acceptReviewTask(result)" in function
    assert "loadBatchReview(selected.review_id, false)" in function


def test_regrade_ui_behavior_in_node():
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for frontend behavior checks")
    script = Path(__file__).parent / "js/exam-regrade.cjs"
    result = subprocess.run([node, str(script)], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 scenarios passed" in result.stdout


def test_invalid_saved_review_id_returns_validation_error(saved, monkeypatch):
    monkeypatch.setattr(api, "current_user", lambda request: {"id": "teacher", "sub": "teacher"})
    client = TestClient(api.app, raise_server_exceptions=False)
    response = client.post("/api/exam/regrade", json={"import_id": "paper", "review_ids": ["../saved-1"]})
    assert response.status_code == 400
