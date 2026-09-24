"""Small PostgreSQL repository used by authentication and artifact persistence."""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'teacher',
    auth_provider TEXT NOT NULL,
    oidc_issuer TEXT,
    oidc_subject TEXT,
    password_hash TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (oidc_issuer, oidc_subject)
);
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS session_version INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS platform_artifacts (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT,
    kind TEXT NOT NULL,
    object_key TEXT NOT NULL UNIQUE,
    content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
    size_bytes BIGINT NOT NULL DEFAULT 0,
    checksum_sha256 TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS platform_jobs (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS platform_audit_events (
    id TEXT PRIMARY KEY,
    actor_user_id TEXT,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


class PostgresStore:
    def __init__(self, dsn: str = ""):
        self.dsn = dsn.strip()

    @property
    def configured(self) -> bool:
        return bool(self.dsn)

    def _driver(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as error:
            raise RuntimeError("PostgreSQL 依赖缺失，请安装 psycopg[binary]") from error
        return psycopg, dict_row

    @contextmanager
    def connection(self) -> Iterator[object]:
        if not self.configured:
            raise RuntimeError("DATABASE_URL 未配置")
        psycopg, dict_row = self._driver()
        connection = psycopg.connect(self.dsn, row_factory=dict_row)
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def healthcheck(self) -> dict:
        if not self.configured:
            return {"configured": False, "ok": False, "message": "DATABASE_URL 未配置"}
        try:
            with self.connection() as connection:
                connection.execute("SELECT 1")
            return {"configured": True, "ok": True}
        except Exception as error:
            return {"configured": True, "ok": False, "message": str(error)}

    def ensure_schema(self) -> None:
        with self.connection() as connection:
            connection.execute(SCHEMA_SQL)

    def find_user_by_email(self, email: str) -> Optional[dict]:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM app_users WHERE lower(email)=lower(%s) AND is_active=TRUE",
                (email.strip(),),
            ).fetchone()

    def find_user_by_oidc(self, issuer: str, subject: str) -> Optional[dict]:
        with self.connection() as connection:
            return connection.execute(
                "SELECT * FROM app_users WHERE oidc_issuer=%s AND oidc_subject=%s AND is_active=TRUE",
                (issuer, subject),
            ).fetchone()

    def ensure_bootstrap_admin(self, email: str, password: str, password_hash: str) -> None:
        if not email or (not password and not password_hash):
            return
        existing = self.find_user_by_email(email)
        if existing:
            return
        if not password_hash:
            try:
                from argon2 import PasswordHasher
            except ImportError as error:
                raise RuntimeError("内置登录需要 argon2-cffi") from error
            password_hash = PasswordHasher().hash(password)
        now = datetime.now(timezone.utc)
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO app_users
                   (id,email,display_name,role,auth_provider,password_hash,created_at,updated_at)
                   VALUES (%s,%s,%s,'admin','builtin',%s,%s,%s)
                   ON CONFLICT (email) DO NOTHING""",
                (uuid.uuid4().hex, email.lower(), email.lower(), password_hash, now, now),
            )

    def verify_builtin_user(self, email: str, password: str) -> Optional[dict]:
        user = self.find_user_by_email(email)
        if not user or user.get("auth_provider") != "builtin" or not user.get("password_hash"):
            return None
        try:
            from argon2 import PasswordHasher
            from argon2.exceptions import VerifyMismatchError
        except ImportError as error:
            raise RuntimeError("内置登录需要 argon2-cffi") from error
        try:
            PasswordHasher().verify(user["password_hash"], password)
        except VerifyMismatchError:
            return None
        return dict(user)

    def upsert_oidc_user(self, issuer: str, subject: str, email: str, display_name: str, role: str) -> dict:
        existing = self.find_user_by_oidc(issuer, subject)
        now = datetime.now(timezone.utc)
        if existing:
            with self.connection() as connection:
                return connection.execute(
                    """UPDATE app_users SET email=%s, display_name=%s, updated_at=%s
                       WHERE id=%s RETURNING *""",
                    (email.lower(), display_name, now, existing["id"]),
                ).fetchone()
        with self.connection() as connection:
            return connection.execute(
                """INSERT INTO app_users
                   (id,email,display_name,role,auth_provider,oidc_issuer,oidc_subject,created_at,updated_at)
                   VALUES (%s,%s,%s,%s,'oidc',%s,%s,%s,%s)
                   ON CONFLICT (oidc_issuer,oidc_subject) DO UPDATE
                   SET email=EXCLUDED.email, display_name=EXCLUDED.display_name, updated_at=EXCLUDED.updated_at
                   RETURNING *""",
                (uuid.uuid4().hex, email.lower(), display_name, role, issuer, subject, now, now),
            ).fetchone()

    def register_artifact(self, owner_user_id: str, kind: str, object_key: str,
                          content_type: str, size_bytes: int, checksum_sha256: str) -> str:
        artifact_id = uuid.uuid4().hex
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO platform_artifacts
                   (id,owner_user_id,kind,object_key,content_type,size_bytes,checksum_sha256)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (object_key) DO UPDATE SET
                   owner_user_id=EXCLUDED.owner_user_id, size_bytes=EXCLUDED.size_bytes,
                   checksum_sha256=EXCLUDED.checksum_sha256""",
                (artifact_id, owner_user_id or None, kind, object_key, content_type, size_bytes, checksum_sha256),
            )
        return artifact_id

    def record_job(self, job_id: str, owner_user_id: str, kind: str, status: str, payload: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO platform_jobs (id,owner_user_id,kind,status,payload)
                   VALUES (%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (id) DO UPDATE SET status=EXCLUDED.status,
                   payload=EXCLUDED.payload, updated_at=NOW()""",
                (job_id, owner_user_id or None, kind, status, __import__("json").dumps(payload, ensure_ascii=False)),
            )

    def audit(self, actor_user_id: str, action: str, resource_type: str, resource_id: str, details: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                """INSERT INTO platform_audit_events
                   (id,actor_user_id,action,resource_type,resource_id,details)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
                (uuid.uuid4().hex, actor_user_id or None, action, resource_type, resource_id,
                 __import__("json").dumps(details, ensure_ascii=False)),
            )

    def find_user_by_id(self, user_id: str) -> Optional[dict]:
        with self.connection() as connection:
            return connection.execute("SELECT * FROM app_users WHERE id=%s", (user_id,)).fetchone()

    def admin_overview(self) -> dict:
        with self.connection() as connection:
            return connection.execute("""SELECT
                (SELECT COUNT(*) FROM app_users) AS users,
                (SELECT COUNT(*) FROM app_users WHERE is_active) AS active_users,
                (SELECT COUNT(*) FROM app_users WHERE is_active AND role='admin') AS admins,
                (SELECT COUNT(*) FROM platform_jobs) AS jobs,
                (SELECT COUNT(*) FROM platform_artifacts) AS artifacts,
                (SELECT COALESCE(SUM(size_bytes),0) FROM platform_artifacts) AS storage_bytes""").fetchone()

    def admin_list_users(self, page, size, search="", role="", active="") -> dict:
        # Escape LIKE metacharacters so search text stays literal.
        term = "%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = "WHERE (email ILIKE %s OR display_name ILIKE %s)"
        params = [term, term]
        if role:
            where += " AND role=%s"
            params.append(role)
        if active:
            where += " AND is_active=%s"
            params.append(active == "true")
        with self.connection() as connection:
            total = connection.execute("SELECT COUNT(*) AS total FROM app_users " + where, params).fetchone()["total"]
            rows = connection.execute(
                "SELECT id,email,display_name,role,auth_provider,is_active,created_at,updated_at FROM app_users "
                + where + " ORDER BY created_at DESC,id LIMIT %s OFFSET %s", params + [size, (page - 1) * size],
            ).fetchall()
        return {"total": total, "items": rows}

    def admin_list_audit(self, page, size) -> dict:
        with self.connection() as connection:
            total = connection.execute("SELECT COUNT(*) AS total FROM platform_audit_events").fetchone()["total"]
            rows = connection.execute("""SELECT e.id,e.actor_user_id,u.email AS actor_email,e.action,
                e.resource_type,e.resource_id,e.details,e.created_at
                FROM platform_audit_events e LEFT JOIN app_users u ON u.id=e.actor_user_id
                ORDER BY e.created_at DESC,e.id LIMIT %s OFFSET %s""", (size, (page - 1) * size)).fetchall()
        return {"total": total, "items": rows}

    def admin_save_user(self, actor_id, user_id, values) -> dict:
        from platform_admin import AdminError
        import json
        with self.connection() as connection:
            # Serialize account changes, including the final-admin invariant.
            connection.execute("LOCK TABLE app_users IN SHARE ROW EXCLUSIVE MODE")
            actor = connection.execute("SELECT * FROM app_users WHERE id=%s", (actor_id,)).fetchone()
            if not actor or not actor["is_active"] or actor["role"] != "admin":
                raise AdminError(403, "管理员身份已更新，请重新登录")
            if user_id is None:
                duplicate = connection.execute("SELECT id FROM app_users WHERE lower(email)=lower(%s)", (values["email"],)).fetchone()
                if duplicate:
                    raise AdminError(409, "该邮箱已被使用")
                user_id = uuid.uuid4().hex
                row = connection.execute("""INSERT INTO app_users
                    (id,email,display_name,role,auth_provider,password_hash)
                    VALUES (%s,%s,%s,%s,'builtin',%s) RETURNING *""",
                    (user_id, values["email"], values["display_name"], values["role"], values["password_hash"]),
                ).fetchone()
                action = "admin.user.create"
            else:
                current = connection.execute("SELECT * FROM app_users WHERE id=%s", (user_id,)).fetchone()
                if not current:
                    raise AdminError(404, "账号不存在")
                role = values.get("role", current["role"])
                active = values.get("is_active", current["is_active"])
                if actor_id == user_id and (role != "admin" or not active):
                    raise AdminError(409, "请保留当前管理员账号的角色与启用状态")
                if current["role"] == "admin" and current["is_active"] and (role != "admin" or not active):
                    count = connection.execute("SELECT COUNT(*) AS total FROM app_users WHERE role='admin' AND is_active=TRUE").fetchone()["total"]
                    if count <= 1:
                        raise AdminError(409, "系统须保留至少一个启用的管理员")
                if "password_hash" in values and current["auth_provider"] != "builtin":
                    raise AdminError(409, "OIDC 账号的密码由身份提供方管理")
                columns = [key for key in ("display_name", "role", "is_active", "password_hash") if key in values]
                assignments = [key + "=%s" for key in columns]
                # Reset passwords and disable/enable transitions revoke existing cookies.
                if "password_hash" in values or ("is_active" in values and values["is_active"] != current["is_active"]):
                    assignments.append("session_version=session_version+1")
                assignments.append("updated_at=NOW()")
                row = connection.execute("UPDATE app_users SET " + ",".join(assignments) + " WHERE id=%s RETURNING *",
                                         [values[key] for key in columns] + [user_id]).fetchone()
                action = "admin.user.update"
            details = {"email": row["email"], "role": row["role"], "is_active": row["is_active"],
                       "changed_fields": ["password" if key == "password_hash" else key for key in values]}
            connection.execute("""INSERT INTO platform_audit_events
                (id,actor_user_id,action,resource_type,resource_id,details)
                VALUES (%s,%s,%s,'user',%s,%s::jsonb)""",
                (uuid.uuid4().hex, actor_id, action, user_id, json.dumps(details, ensure_ascii=False)))
            return row
