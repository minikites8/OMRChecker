"""Runtime settings persistence, validation, precedence and dual-server contracts."""
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient
from argon2 import PasswordHasher

import ai_judge
import scan_ui
import runtime_settings as config
from backend import app as api
from platform_config import PlatformSettings
from recognition_config import recognition_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    for spec in config.FIELDS:
        for key in (spec["key"], *spec["aliases"]):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OMR_SETTINGS_FILE", str(tmp_path / "settings.json"))
    yield


@pytest.fixture
def store():
    return config.get_settings_store()


def save(store, values=None, reset=None, revision=None):
    return store.update({"revision": store.read()["revision"] if revision is None else revision,
                         "values": values or {}, "reset": reset or []}, "admin-1")


def test_schema_covers_existing_deployment_environment_fields():
    supported = set(config.DEFINITIONS) | {alias for spec in config.FIELDS for alias in spec["aliases"]}
    for line in Path("deploy/.env.example").read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            assert line.split("=", 1)[0] in supported
    assert len(config.FIELDS) == 38
    assert len(config.DEFINITIONS) == len(config.FIELDS)


def test_saved_values_override_environment_aliases_and_clear_is_explicit(store, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "environment-key-sensitive")
    monkeypatch.setenv("OPENAI_MODEL", "environment-model")
    assert ai_judge.ai_config()["api_key"] == "environment-key-sensitive"
    save(store, {"HANDWRITING_AI_MODEL": "saved-model", "HANDWRITING_AI_API_KEY": "saved-key-sensitive", "HANDWRITING_AI_TIMEOUT": 42})
    assert ai_judge.ai_config()["model"] == "saved-model"
    assert ai_judge.ai_config()["api_key"] == "saved-key-sensitive"
    assert ai_judge.ai_config()["timeout"] == 42
    state = store.public_state()
    serialized = json.dumps(state)
    assert "environment-key-sensitive" not in serialized and "saved-key-sensitive" not in serialized
    secret = next(field for field in state["fields"] if field["key"] == "HANDWRITING_AI_API_KEY")
    assert secret["value"] is None and secret["configured"] and secret["source"] == "saved"
    save(store, {"HANDWRITING_AI_API_KEY": ""})
    assert ai_judge.ai_config()["api_key"] == ""
    save(store, reset=["HANDWRITING_AI_API_KEY", "HANDWRITING_AI_MODEL"])
    assert ai_judge.ai_config()["api_key"] == "environment-key-sensitive"
    assert ai_judge.ai_config()["model"] == "environment-model"


def test_ai_request_uses_a_single_configuration_snapshot(store, monkeypatch):
    calls = []
    original = store.read
    def read():
        calls.append(1)
        return original()
    monkeypatch.setattr(store, "read", read)
    ai_judge.ai_config()
    assert calls == [1]


def test_hot_values_apply_and_restart_values_remain_pending_until_new_instance(store):
    old = PlatformSettings.from_env()
    result = save(store, {"OMR_LOCAL_OCR_ENABLED": False, "OMR_SESSION_TTL_SECONDS": 3600, "OMR_AI_WORKERS": 2})
    assert recognition_settings()["recognition_mode"] == "ai_only"
    assert old.session_ttl_seconds == 28800
    assert PlatformSettings.from_env().session_ttl_seconds == 3600
    assert set(result["pending_restart"]) == {"OMR_SESSION_TTL_SECONDS", "OMR_AI_WORKERS"}
    assert config.SettingsStore(store.path).public_state()["pending_restart"] == []


def test_values_survive_fresh_python_process_and_startup_readers(store):
    save(store, {"HANDWRITING_AI_MODEL": "persisted-model", "OMR_LOCAL_OCR_ENABLED": False,
                 "OMR_REVIEW_WORKERS": 2, "OMR_AI_WORKERS": 1, "OMR_PORT": 18999,
                 "PORT": 18998, "OMR_HOST": "127.0.0.2", "OMR_MAX_REQUEST_MB": 128, "OMR_MAX_FILE_MB": 16})
    code = "import json,scan_ui,ai_judge; from runtime_settings import get_setting; print(json.dumps([ai_judge.ai_config()['model'],scan_ui.DEFAULT_REVIEW_WORKERS,scan_ui.DEFAULT_AI_WORKERS,scan_ui._configured_port(),get_setting('PORT'),scan_ui.MAX_REQUEST_BYTES,scan_ui.MAX_FILE_BYTES,scan_ui.parse_args().host]))"
    result = subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2], env=dict(os.environ, PYTHONIOENCODING="utf-8"), capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == ["persisted-model", 2, 1, 18999, "18998", 128 * 1024 * 1024, 16 * 1024 * 1024, "127.0.0.2"]


@pytest.mark.parametrize("key,value", [
    ("HANDWRITING_AI_TIMEOUT", 9), ("HANDWRITING_AI_TIMEOUT", 301), ("HANDWRITING_AI_CONCURRENCY", True),
    ("OMR_LOCAL_OCR_ENABLED", "false"), ("OMR_AI_WORKERS", 5), ("PORT", 0), ("OMR_AUTH_MODE", "root"),
    ("HANDWRITING_AI_ENDPOINT", "file:///secret"), ("HANDWRITING_AI_ENDPOINT", "http://host:999999"),
    ("HANDWRITING_AI_ENDPOINT", "http://user:password@host"), ("HANDWRITING_AI_MODEL", ""),
    ("OMR_SESSION_SECRET", "short"), ("OMR_BOOTSTRAP_ADMIN_PASSWORD", "short"),
    ("OMR_BOOTSTRAP_ADMIN_PASSWORD_HASH", "plain-password"), ("OMR_BOOTSTRAP_ADMIN_PASSWORD_HASH", "$argon2id$fixture"), ("OMR_BOOTSTRAP_ADMIN_EMAIL", "bad"),
    ("FRONTEND_ORIGINS", "*"), ("FRONTEND_ORIGINS", "https://site/path"),
    ("DATABASE_URL", "mysql://user:secret@host/db"), ("OMR_HOST", "host/path"),
    ("OMR_DATA_ROOT", "bad\x00path"), ("TENCENT_COS_PREFIX", "../path"), ("OIDC_DEFAULT_ROLE", "owner"),
])
def test_invalid_values_leave_file_unchanged(store, key, value):
    save(store, {"HANDWRITING_AI_MODEL": "before"})
    before = store.path.read_bytes()
    with pytest.raises(config.SettingsError) as caught:
        save(store, {key: value})
    assert caught.value.status == 400
    assert store.path.read_bytes() == before
    assert "plain-password" not in str(caught.value)


@pytest.mark.parametrize("payload", [None, [], {}, {"revision": True, "values": {}},
    {"revision": 0, "values": []}, {"revision": 0, "reset": [1]},
    {"revision": 0, "values": {"ARBITRARY_ENV": "value"}},
    {"revision": 0, "values": {"HANDWRITING_AI_MODEL": "a"}, "reset": ["HANDWRITING_AI_MODEL"]},
    {"revision": 0, "values": {"HANDWRITING_AI_MODEL": "a"}, "actor_user_id": "spoofed"},
])
def test_request_shape_is_strict(store, payload):
    with pytest.raises(config.SettingsError) as caught:
        store.update(payload, "admin")
    assert caught.value.status == 400
    assert not store.path.exists()


def test_required_combinations_and_file_limits(store):
    for values in [{"OMR_AUTH_MODE": "builtin"}, {"OMR_AUTH_MODE": "oidc"},
                   {"OMR_PERSISTENCE_MODE": "postgres_cos"}, {"OMR_MAX_REQUEST_MB": 8}]:
        with pytest.raises(config.SettingsError) as caught:
            save(store, values)
        assert caught.value.status == 400
    save(store, {"OMR_AUTH_MODE": "builtin", "OMR_SESSION_SECRET": "s" * 40,
                 "DATABASE_URL": "postgresql://user:private-db-password@localhost/db"})
    settings = PlatformSettings.from_env()
    assert settings.auth_mode == "builtin" and settings.session_secret == "s" * 40
    assert settings.database_url.endswith("/db")
    assert "private-db-password" not in json.dumps(store.public_state())
    with pytest.raises(config.SettingsError):
        save(store, reset=["OMR_SESSION_SECRET"])


def test_all_registered_fields_can_be_saved_and_read(store, tmp_path):
    values = {field["key"]: field["default"] for field in config.FIELDS}
    values.update({"HANDWRITING_AI_API_KEY": "api-private", "OMR_SESSION_SECRET": "s" * 40,
                   "OMR_BOOTSTRAP_ADMIN_PASSWORD": "Private-password-123", "OMR_BOOTSTRAP_ADMIN_PASSWORD_HASH": PasswordHasher().hash("Private-password-123"),
                   "OMR_BOOTSTRAP_ADMIN_EMAIL": "admin@example.test", "DATABASE_URL": "postgresql://user:secret@localhost/db",
                   "OIDC_ISSUER_URL": "https://id.example.test", "OIDC_CLIENT_ID": "id", "OIDC_CLIENT_SECRET": "oidc-private",
                   "OIDC_REDIRECT_URI": "https://app.example.test/auth/oidc/callback", "OMR_AUTH_MODE": "oidc_or_builtin",
                   "OMR_PERSISTENCE_MODE": "postgres_cos", "OMR_DATA_ROOT": str(tmp_path / "business"),
                   "TENCENT_COS_SECRET_ID": "secret-id", "TENCENT_COS_SECRET_KEY": "cos-private", "TENCENT_COS_REGION": "region",
                   "TENCENT_COS_BUCKET": "bucket", "TENCENT_COS_APPID": "appid"})
    result = save(store, values)
    assert len(result["changed_keys"]) == 38
    assert store.path.exists() and config.settings_path() == store.path
    loaded = PlatformSettings.from_env()
    assert loaded.cos_bucket == "bucket" and loaded.data_root == tmp_path / "business"
    assert not loaded.validate_startup()
    raw = json.loads(store.path.read_text(encoding="utf-8"))
    assert raw["values"] == values
    assert set(raw["audit"][0]) == {"revision", "actor_user_id", "action", "changed_fields", "created_at"}


def test_parallel_repositories_detect_revision_conflict(store):
    other = config.SettingsStore(store.path)
    barrier = threading.Barrier(2)
    def write(repo, model):
        barrier.wait(timeout=5)
        try:
            return save(repo, {"HANDWRITING_AI_MODEL": model}, revision=0)["revision"]
        except config.SettingsError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = [future.result(timeout=15) for future in [workers.submit(write, store, "a"), workers.submit(write, other, "b")]]
    assert sorted(outcomes) == [1, 409]
    assert len(store.read()["audit"]) == 1


def test_failed_atomic_replace_preserves_previous_revision(store, monkeypatch):
    save(store, {"HANDWRITING_AI_MODEL": "before"})
    before = store.path.read_bytes()
    def fail(*args):
        raise OSError("fixture replace failure")
    monkeypatch.setattr(config.os, "replace", fail)
    with pytest.raises(OSError):
        save(store, {"HANDWRITING_AI_MODEL": "after"})
    assert store.path.read_bytes() == before
    assert not list(store.path.parent.glob(".settings-*.tmp"))


def test_corrupt_configuration_is_reported_without_contents(store):
    store.path.write_text('{"sensitive": "file-secret"', encoding="utf-8")
    with pytest.raises(config.SettingsError) as caught:
        store.public_state()
    assert caught.value.status == 503 and "file-secret" not in str(caught.value)


@pytest.fixture(params=["fastapi", "legacy"])
def client(request, monkeypatch):
    users = {"admin": {"id": "admin-1", "role": "admin"}, "teacher": {"id": "teacher-1", "role": "teacher"}}
    auth = SimpleNamespace(enabled=True, user_from_headers=lambda cookie: users.get(cookie))
    monkeypatch.setattr(api, "auth", auth); monkeypatch.setattr(scan_ui, "AUTH_SERVICE", auth)
    transport = server = worker = None
    if request.param == "fastapi":
        transport = TestClient(api.app)
    else:
        server = scan_ui.create_server("127.0.0.1", 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    def call(method, actor="admin", payload=None):
        headers = {"Content-Type": "application/json", "Cookie": actor or ""}
        if transport:
            response = transport.request(method, "/api/admin/settings", headers=headers, json=payload)
            assert response.headers["cache-control"] == "no-store"
            return response.status_code, response.json()
        req = Request(f"http://127.0.0.1:{server.server_port}/api/admin/settings", method=method, headers=headers,
                      data=json.dumps(payload).encode() if payload is not None else None)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())
    yield call
    if server:
        server.shutdown(); server.server_close(); worker.join(timeout=5)
    if transport:
        transport.close()


@pytest.mark.parametrize("actor,code", [(None, 401), ("teacher", 403)])
@pytest.mark.parametrize("method", ["GET", "PATCH"])
def test_only_admin_can_access_settings(client, actor, code, method, store):
    assert client(method, actor, {"revision": 0, "values": {"HANDWRITING_AI_MODEL": "forbidden"}})[0] == code
    assert not store.path.exists()


def test_settings_api_roundtrip_secret_redaction_and_conflict(client, store):
    status, state = client("GET")
    assert status == 200 and len(state["fields"]) == 38
    code, result = client("PATCH", payload={"revision": state["revision"], "values": {"HANDWRITING_AI_API_KEY": "admin-key-sensitive", "HANDWRITING_AI_MODEL": "api-model"}})
    assert code == 200 and result["revision"] == 1
    assert "admin-key-sensitive" not in json.dumps(result)
    assert ai_judge.ai_config()["api_key"] == "admin-key-sensitive"
    assert result["audit"][0]["actor_user_id"] == "admin-1"
    assert client("PATCH", payload={"revision": 0, "values": {"HANDWRITING_AI_MODEL": "stale"}})[0] == 409
    assert client("PATCH", payload={"revision": 1, "values": {"HANDWRITING_AI_TIMEOUT": 999}})[0] == 400
    assert client("PATCH", payload={"revision": 1, "reset": ["HANDWRITING_AI_API_KEY"]})[0] == 200
    assert ai_judge.ai_config()["api_key"] == ""


def test_settings_work_in_local_mode(client, monkeypatch):
    auth = SimpleNamespace(enabled=False, user_from_headers=lambda _: {"id": "local", "role": "admin"})
    monkeypatch.setattr(api, "auth", auth); monkeypatch.setattr(scan_ui, "AUTH_SERVICE", auth)
    assert client("GET", None)[0] == 200
    assert client("PATCH", None, {"revision": 0, "values": {"HANDWRITING_AI_MODEL": "local-model"}})[0] == 200


def test_openapi_and_docker_entrypoint_use_managed_settings():
    assert {"get", "patch"}.issubset(api.app.openapi()["paths"]["/api/admin/settings"])
    assert 'CMD ["python", "-m", "backend.main"]' in Path("Dockerfile.backend").read_text(encoding="utf-8")
    for name in [".gitignore", ".dockerignore"]:
        assert "**/platform-settings.json" in Path(name).read_text(encoding="utf-8")
