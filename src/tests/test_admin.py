"""Admin contracts across both transports; SQLite adapts repository SQL for local tests."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
import scan_ui
from backend import app as api
from platform_admin import AdminError, public_admin_user
from platform_auth import AuthService
from platform_config import PlatformSettings
from platform_database import PostgresStore, SCHEMA_SQL

PASSWORD = "Teacher-test-password-123!"
HASH = PasswordHasher().hash(PASSWORD)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    @staticmethod
    def row(value):
        if value is None:
            return None
        value = dict(value)
        if "is_active" in value:
            value["is_active"] = bool(value["is_active"])
        if "details" in value:
            value["details"] = json.loads(value["details"])
        return value

    def fetchone(self):
        return self.row(self.cursor.fetchone())

    def fetchall(self):
        return [self.row(row) for row in self.cursor.fetchall()]


class TestStore(PostgresStore):
    __test__ = False

    def __init__(self):
        super().__init__("test-only")
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock, self.sql, self.fail_audit = threading.RLock(), [], False
        self.db.executescript("""
            CREATE TABLE app_users (id TEXT PRIMARY KEY,email TEXT UNIQUE,display_name TEXT DEFAULT '',
                role TEXT,auth_provider TEXT,password_hash TEXT,is_active BOOLEAN DEFAULT TRUE,
                session_version INTEGER DEFAULT 0,created_at TEXT DEFAULT CURRENT_TIMESTAMP,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE platform_jobs (id TEXT);
            CREATE TABLE platform_artifacts (id TEXT,size_bytes INTEGER);
            CREATE TABLE platform_audit_events (id TEXT PRIMARY KEY,actor_user_id TEXT,action TEXT,
                resource_type TEXT,resource_id TEXT,details TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        """)
        for identifier, role, provider in [("admin", "admin", "builtin"), ("teacher", "teacher", "builtin"), ("oidc", "teacher", "oidc")]:
            self.db.execute("INSERT INTO app_users (id,email,display_name,role,auth_provider,password_hash) VALUES (?,?,?,?,?,?)",
                            (identifier, identifier + "@example.test", identifier, role, provider, HASH))
        self.db.commit()

    @contextmanager
    def connection(self):
        with self.lock:
            try:
                yield self
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise

    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if sql.startswith("LOCK TABLE"):
            return None
        if self.fail_audit and "INSERT INTO platform_audit_events" in sql:
            raise RuntimeError("audit unavailable")
        sql = sql.replace("ILIKE %s", "LIKE %s ESCAPE '\\'").replace("%s", "?").replace("::jsonb", "").replace("NOW()", "CURRENT_TIMESTAMP")
        return Cursor(self.db.execute(sql, params))


@pytest.fixture
def store():
    db = TestStore()
    yield db
    db.db.close()


@pytest.fixture(params=["fastapi", "legacy"])
def client(request, monkeypatch, store):
    settings = replace(PlatformSettings.from_env(), auth_mode="builtin", persistence_mode="local", session_secret="admin-test-secret", database_url="test-only")
    auth = AuthService(settings, store)
    for obj, names in [(api, ("settings", "database", "auth")), (scan_ui, ("PLATFORM_SETTINGS", "PLATFORM_DATABASE", "AUTH_SERVICE"))]:
        for name, value in zip(names, (settings, store, auth)):
            monkeypatch.setattr(obj, name, value)
    cookies = {name: auth.session_cookie(store.find_user_by_id(name)).split(";", 1)[0] for name in ["admin", "teacher", "oidc"]}
    transport, server, worker = None, None, None
    if request.param == "fastapi":
        transport = TestClient(api.app)
    else:
        server = scan_ui.create_server("127.0.0.1", 0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()

    def call(method, path, actor="admin", payload=None, raw=None, content_type="application/json"):
        headers = {"Content-Type": content_type}
        if actor:
            headers["Cookie"] = cookies.get(actor, actor)
        body = raw if raw is not None else (json.dumps(payload) if payload is not None else None)
        if transport:
            response = transport.request(method, path, headers=headers, content=body)
            return response.status_code, response.json()
        req = Request(f"http://127.0.0.1:{server.server_port}" + path, method=method, headers=headers, data=body.encode() if body is not None else None)
        try:
            response = urlopen(req, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            return response.status, json.loads(response.read())

    call.auth, call.store, call.cookies = auth, store, cookies
    yield call
    if server:
        server.shutdown(); server.server_close(); worker.join(timeout=5)
    if transport:
        transport.close()


@pytest.mark.parametrize("actor,expected", [(None, 401), ("teacher", 403), ("oidc", 403)])
@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/admin/overview", None), ("GET", "/api/admin/users", None), ("GET", "/api/admin/audit-events", None),
    ("POST", "/api/admin/users", {"email": "new@example.test", "password": PASSWORD}),
    ("PATCH", "/api/admin/users/teacher", {"role": "admin"}),
])
def test_all_routes_authorize_on_server(client, actor, expected, method, path, payload):
    code, result = client(method, path, actor, payload)
    assert code == expected and result["ok"] is False
    assert client.store.admin_list_audit(1, 20)["total"] == 0


def test_account_lifecycle_live_permissions_and_audit(client):
    code, result = client("GET", "/api/admin/overview")
    assert code == 200 and result["counts"]["admins"] == 1
    code, result = client("POST", "/api/admin/users", payload={"email": "  NEW@example.test ", "display_name": "新教师", "password": PASSWORD})
    assert code == 200 and result["user"]["email"] == "new@example.test"
    assert "password_hash" not in result["user"] and "session_version" not in result["user"]
    user_id = result["user"]["id"]
    assert client.store.verify_builtin_user("new@example.test", PASSWORD)
    code, result = client("GET", "/api/admin/users?search=new&role=teacher&active=true&page_size=1")
    assert code == 200 and result["total"] == 1 and result["items"][0]["id"] == user_id
    cookie = client.auth.session_cookie(client.store.find_user_by_id(user_id)).split(";", 1)[0]
    assert client("PATCH", "/api/admin/users/" + user_id, payload={"role": "admin"})[0] == 200
    assert client("GET", "/api/admin/overview", cookie)[0] == 200
    assert client("PATCH", "/api/admin/users/" + user_id, payload={"role": "teacher"})[0] == 200
    assert client("GET", "/api/admin/overview", cookie)[0] == 403
    assert client("PATCH", "/api/admin/users/" + user_id, payload={"is_active": False})[0] == 200
    assert client("GET", "/api/admin/overview", cookie)[0] == 401
    assert client("PATCH", "/api/admin/users/" + user_id, payload={"is_active": True})[0] == 200
    assert client("GET", "/api/admin/overview", cookie)[0] == 401
    cookie2 = client.auth.session_cookie(client.store.find_user_by_id(user_id)).split(";", 1)[0]
    assert client("PATCH", "/api/admin/users/" + user_id, payload={"password": "New-password-123456!"})[0] == 200
    assert client.auth.user_from_headers(cookie2) is None
    assert client.store.verify_builtin_user("new@example.test", "New-password-123456!")
    code, audit = client("GET", "/api/admin/audit-events?page_size=2")
    assert code == 200 and audit["total"] == 6 and len(audit["items"]) == 2
    assert PASSWORD not in json.dumps(audit) and "password_hash" not in json.dumps(audit)


@pytest.mark.parametrize("payload", [{"role": "teacher"}, {"is_active": False}])
def test_self_admin_protection(client, payload):
    assert client("PATCH", "/api/admin/users/admin", payload=payload)[0] == 409
    assert client.store.find_user_by_id("admin")["role"] == "admin"
    assert client.store.find_user_by_id("admin")["is_active"]


@pytest.mark.parametrize("payload", [{}, [], {"role": "owner"}, {"is_active": "false"}, {"password": "short"}, {"password_hash": "injected"}, {"display_name": 42}, {"email": "new@example.test"}, {"_actor_user_id": "admin"}])
def test_validates_types_and_fields(client, payload):
    assert client("PATCH", "/api/admin/users/teacher", payload=payload)[0] == 400


@pytest.mark.parametrize("query", ["page=0", "page=abc", "page_size=101", "page_size=-1", "role=owner", "active=1"])
def test_validates_query(client, query):
    assert client("GET", "/api/admin/users?" + query)[0] == 400


def test_duplicate_missing_oidc_and_invalid_json(client):
    assert client("POST", "/api/admin/users", payload={"email": "ADMIN@example.test", "password": PASSWORD})[0] == 409
    assert client("PATCH", "/api/admin/users/missing", payload={"role": "admin"})[0] == 404
    assert client("PATCH", "/api/admin/users/oidc", payload={"password": PASSWORD})[0] == 409
    assert client("POST", "/api/admin/users", raw="{")[0] == 400
    assert client("POST", "/api/admin/users", raw="{}", content_type="text/plain")[0] == 415
    assert client("POST", "/api/admin/users", raw="x" * 16385)[0] == 413
    assert client("POST", "/api/admin/users", payload={"email": "bad", "password": PASSWORD})[0] == 400


def test_disabled_and_demoted_admin_cookie(client):
    with client.store.connection() as connection:
        connection.execute("UPDATE app_users SET role='teacher' WHERE id='admin'")
    assert client("GET", "/api/admin/overview")[0] == 403
    with client.store.connection() as connection:
        connection.execute("UPDATE app_users SET is_active=FALSE WHERE id='admin'")
    assert client("GET", "/api/admin/overview")[0] == 401


def test_local_mode_exposes_management_state(client):
    client.auth.settings = replace(client.auth.settings, auth_mode="disabled")
    code, result = client("GET", "/api/admin/overview", None)
    assert code == 200 and result["user_management_enabled"] is False and result["counts"] is None
    assert client("GET", "/api/admin/users", None)[0] == 503


def test_database_failure_redaction_and_atomic_write(client):
    client.store.fail_audit = True
    code, result = client("PATCH", "/api/admin/users/teacher", payload={"role": "admin"})
    assert code == 503 and "audit unavailable" not in json.dumps(result)
    assert client.store.find_user_by_id("teacher")["role"] == "teacher"


def test_repository_rechecks_actor_and_locks_mutations(store):
    with pytest.raises(AdminError) as caught:
        store.admin_save_user("teacher", "admin", {"is_active": False})
    assert caught.value.status == 403
    assert any("LOCK TABLE app_users IN SHARE ROW EXCLUSIVE MODE" in sql for sql, _ in store.sql)
    assert store.find_user_by_id("admin")["is_active"]


def test_literal_search_and_pagination(store):
    for text in ["%", "_", "' OR TRUE; --", "\\"]:
        assert store.admin_list_users(1, 20, text)["total"] == 0
    assert store.admin_list_users(1, 1)["total"] == 3


def test_migration_and_public_fields():
    assert "ADD COLUMN IF NOT EXISTS session_version" in SCHEMA_SQL
    result = public_admin_user({"id": "u", "password_hash": "secret", "oidc_subject": "private", "session_version": 2})
    assert set(result) == {"id", "email", "display_name", "role", "auth_provider", "is_active", "created_at", "updated_at"}


def test_session_uses_live_database_role_and_legacy_cookie_version(store):
    import time
    settings = replace(PlatformSettings.from_env(), auth_mode="builtin", session_secret="test-secret")
    auth = AuthService(settings, store)
    signed = auth._pack({"sub": "teacher", "role": "admin", "exp": int(time.time()) + 60})
    assert auth.user_from_headers("omr_session=" + signed)["role"] == "teacher"
    with store.connection() as connection:
        connection.execute("UPDATE app_users SET session_version=1 WHERE id='teacher'")
    assert auth.user_from_headers("omr_session=" + signed) is None


def test_oidc_login_checks_disabled_account(store, monkeypatch):
    from platform_auth import AuthError
    settings = replace(PlatformSettings.from_env(), auth_mode="oidc", session_secret="test-secret")
    auth = AuthService(settings, store)
    monkeypatch.setattr(auth, "_unpack", lambda _: {"state": "state", "verifier": "verifier", "nonce": "nonce"})
    monkeypatch.setattr(auth, "_discovery_document", lambda: {"token_endpoint": "fixture"})
    monkeypatch.setattr(auth, "_fetch_json", lambda *args, **kwargs: {"id_token": "fixture"})
    monkeypatch.setattr(auth, "_verify_id_token", lambda *args: {"sub": "oidc", "email": "oidc@example.test"})
    monkeypatch.setattr(store, "upsert_oidc_user", lambda *args: {"id": "oidc", "is_active": False})
    with pytest.raises(AuthError, match="账号已停用"):
        auth.complete_oidc("code", "state", "", "fixture")


def test_openapi_documents_update_path_and_distinct_write_fields():
    schema = api.app.openapi()
    create = schema["paths"]["/api/admin/users"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    patch = schema["paths"]["/api/admin/users/{user_id}"]["patch"]
    update = patch["requestBody"]["content"]["application/json"]["schema"]
    assert create["required"] == ["email", "password"]
    assert "email" not in update["properties"] and "is_active" not in create["properties"]
    assert patch["parameters"][0]["name"] == "user_id"
