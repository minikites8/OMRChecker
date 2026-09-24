"""Named imports and archival deletion for both HTTP deployments."""
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

import scan_ui
from backend import app as api
from platform_config import PlatformSettings
from platform_persistence import PlatformPersistence

EXAM = json.dumps([{"name": "选择题", "questions": [{"id": "1", "title": "1. 测试", "type": "single", "score": 2, "answer": "A"}]}])


@pytest.fixture(autouse=True)
def isolated_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(scan_ui, "IMPORT_ROOT", tmp_path / "imports")
    monkeypatch.setattr(scan_ui, "REVIEW_ROOT", tmp_path / "reviews")
    settings = replace(PlatformSettings.from_env(), auth_mode="disabled", persistence_mode="local")
    persistence = PlatformPersistence(settings, Mock(), Mock())
    monkeypatch.setattr(scan_ui, "PLATFORM_PERSISTENCE", persistence)
    monkeypatch.setattr(api, "persistence", persistence)
    auth = SimpleNamespace(startup=lambda: None, enabled=False, user_from_headers=lambda headers: {"id": "teacher-1", "role": "teacher"})
    monkeypatch.setattr(api, "auth", auth)
    monkeypatch.setattr(scan_ui, "AUTH_SERVICE", auth)


def create(name="数学期中考试"):
    return scan_ui.run_exam_import({"name": name, "exam_text": EXAM})


def test_import_name_is_trimmed_saved_and_listed():
    result = create("  数学  期中考试  ")
    assert result["name"] == "数学 期中考试"
    path = scan_ui.IMPORT_ROOT / result["import_id"] / "normalized_exam.json"
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == result["name"]
    assert scan_ui.list_exam_imports()[0]["name"] == result["name"]
    assert result["answer_map"] == {"1": "A"}


@pytest.mark.parametrize("name", ["", "  ", None])
def test_optional_name_gets_stable_default(name):
    result = create(name)
    assert result["name"] == "试卷 " + result["import_id"]
    assert scan_ui.list_exam_imports()[0]["name"] == result["name"]


def test_legacy_imports_keep_working_without_name():
    result = create()
    path = scan_ui.IMPORT_ROOT / result["import_id"] / "normalized_exam.json"
    imported = json.loads(path.read_text(encoding="utf-8"))
    imported.pop("name")
    path.write_text(json.dumps(imported), encoding="utf-8")
    assert scan_ui.list_exam_imports()[0]["name"] == "试卷 " + result["import_id"]
    assert scan_ui._review_import({"import_id": result["import_id"]})[0] == result["import_id"]


@pytest.mark.parametrize("name", ["长" * 121, {"title": "bad"}, 123, 0, False, []])
def test_invalid_name_is_rejected_before_files_are_created(name):
    with pytest.raises(ValueError):
        create(name)
    assert not scan_ui.IMPORT_ROOT.exists()


def test_delete_archives_import_and_preserves_history(tmp_path):
    removed = create("试卷一")
    kept = create("试卷二")
    root = scan_ui.IMPORT_ROOT / removed["import_id"]
    history = scan_ui.REVIEW_ROOT / "history" / "input" / "normalized_exam.json"
    history.parent.mkdir(parents=True)
    original = (root / "normalized_exam.json").read_bytes()
    history.write_bytes(original)
    response = scan_ui.delete_exam_import({"import_id": removed["import_id"], "_actor_user_id": "teacher-1"})
    assert response == {"ok": True, "import_id": removed["import_id"], "deleted": True}
    assert [entry["import_id"] for entry in scan_ui.list_exam_imports()] == [kept["import_id"]]
    archived = json.loads((root / "normalized_exam.json").read_text(encoding="utf-8"))
    assert archived["deleted_at"] and archived["deleted_by"] == "teacher-1"
    assert history.read_bytes() == original
    assert (root / "answer_map.json").exists()
    with pytest.raises(ValueError, match="已删除"):
        scan_ui._review_import({"import_id": removed["import_id"]})
    assert scan_ui.delete_exam_import({"import_id": removed["import_id"]}) == response


@pytest.mark.parametrize("identifier", ["", "../outside", "..", "/absolute", "a/b", "a\\b"])
def test_delete_rejects_invalid_identifiers(identifier):
    with pytest.raises(ValueError):
        scan_ui.delete_exam_import({"import_id": identifier})


def test_delete_missing_import():
    with pytest.raises(FileNotFoundError):
        scan_ui.delete_exam_import({"import_id": "missing"})


def test_import_cos_paths_include_import_id(monkeypatch):
    sync = Mock()
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_tree", sync)
    result = create()
    assert sync.call_args.kwargs["relative_prefix"] == Path(result["import_id"])


def test_delete_syncs_tombstone_to_own_cos_key(monkeypatch):
    result = create()
    captured = {}
    def sync(path, kind, owner, relative):
        captured.update(kind=kind, relative=relative, document=json.loads(path.read_text(encoding="utf-8")))
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_file", sync)
    scan_ui.delete_exam_import({"import_id": result["import_id"]})
    assert captured["kind"] == "exam_import"
    assert captured["relative"] == Path(result["import_id"]) / "normalized_exam.json"
    assert captured["document"]["deleted_at"]


def test_storage_failure_keeps_import_available_and_retryable(monkeypatch):
    result = create()
    root = scan_ui.IMPORT_ROOT / result["import_id"]
    previous = (root / "normalized_exam.json").read_bytes()
    sync = Mock(side_effect=RuntimeError("COS offline"))
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_file", sync)
    with pytest.raises(RuntimeError, match="COS offline"):
        scan_ui.delete_exam_import({"import_id": result["import_id"]})
    assert (root / "normalized_exam.json").read_bytes() == previous
    assert scan_ui.list_exam_imports()[0]["import_id"] == result["import_id"]
    assert list(root.glob(".deleting-*.json")) == []
    sync.side_effect = None
    scan_ui.delete_exam_import({"import_id": result["import_id"]})
    assert scan_ui.list_exam_imports() == []


def test_sync_tree_prefix_keeps_exam_objects_separate(tmp_path):
    settings = replace(PlatformSettings.from_env(), persistence_mode="postgres_cos", cos_prefix="omrchecker")
    database, cos = Mock(), Mock()
    cos.put_file.return_value = {"size_bytes": 2, "checksum_sha256": "hash"}
    persistence = PlatformPersistence(settings, database, cos)
    folder = tmp_path / "tree"
    folder.mkdir()
    (folder / "normalized_exam.json").write_text("{}", encoding="utf-8")
    first = persistence.sync_tree(folder, "exam_import", relative_prefix=Path("one"))
    second = persistence.sync_tree(folder, "exam_import", relative_prefix=Path("two"))
    assert first == ["omrchecker/exam_import/one/normalized_exam.json"]
    assert second == ["omrchecker/exam_import/two/normalized_exam.json"]
    assert database.register_artifact.call_count == 2
    assert persistence.sync_tree(folder, "review") == ["omrchecker/review/normalized_exam.json"]


def test_fastapi_import_list_and_delete():
    with TestClient(api.app) as client:
        response = client.post("/api/exam/import", json={"name": "命名试卷", "exam_text": EXAM})
        assert response.status_code == 200
        imported = response.json()
        assert imported["name"] == "命名试卷"
        assert client.get("/api/exam/imports").json()["imports"][0]["name"] == "命名试卷"
        deleted = client.post("/api/exam/delete", json={"import_id": imported["import_id"]})
        assert deleted.status_code == 200 and deleted.json()["deleted"]
        assert client.get("/api/exam/imports").json()["imports"] == []


def test_fastapi_validation_and_missing_import():
    client = TestClient(api.app)
    assert client.post("/api/exam/import", json={"name": "x" * 121, "exam_text": EXAM}).status_code == 400
    assert client.post("/api/exam/delete", json={"import_id": "../outside"}).status_code == 400
    assert client.post("/api/exam/delete", json={"import_id": "missing"}).status_code == 404


def test_delete_requires_authenticated_session(monkeypatch):
    result = create()
    monkeypatch.setattr(api, "auth", SimpleNamespace(enabled=True, user_from_headers=lambda _: None))
    response = TestClient(api.app).post("/api/exam/delete", json={"import_id": result["import_id"]})
    assert response.status_code == 401
    assert len(scan_ui.list_exam_imports()) == 1


def test_legacy_http_delete_endpoint():
    result = create()
    server = scan_ui.create_server("127.0.0.1", 0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_address[1]}/api/exam/delete"
        request = Request(endpoint, data=json.dumps({"import_id": result["import_id"]}).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=5) as response:
            assert response.status == 200 and json.load(response)["deleted"]
        missing = Request(endpoint, data=b'{"import_id":"missing"}', headers={"Content-Type": "application/json"}, method="POST")
        with pytest.raises(HTTPError) as error:
            urlopen(missing, timeout=5)
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def test_ui_has_name_delete_confirmation_and_selection_preservation():
    html = (scan_ui.UI_ROOT / "index.html").read_text(encoding="utf-8")
    js = (scan_ui.UI_ROOT / "app.js").read_text(encoding="utf-8")
    platform = (scan_ui.UI_ROOT / "platform.js").read_text(encoding="utf-8-sig")
    assert 'id="examImportName"' in html and 'maxlength="120"' in html
    assert "name:importEl.name.value.trim()" in js
    assert "window.confirm('确认删除《'" in js
    assert "loadReviewImports(undefined,true)" in js
    assert "preserveReview&&selectedExists" in js
    assert "actionWrap.append(use, download, remove)" in platform


def test_fastapi_storage_failure_returns_retryable_error(monkeypatch):
    result = create()
    monkeypatch.setattr(scan_ui.PLATFORM_PERSISTENCE, "sync_file", Mock(side_effect=RuntimeError("COS offline")))
    response = TestClient(api.app).post("/api/exam/delete", json={"import_id": result["import_id"]})
    assert response.status_code == 503
    assert "稍后重试" in response.json()["detail"]
    assert len(scan_ui.list_exam_imports()) == 1
