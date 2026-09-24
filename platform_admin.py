"""Shared administrator API contract for the FastAPI and local HTTP servers."""
from __future__ import annotations

import re

from runtime_settings import SettingsError, get_settings_store


class AdminError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def require_admin(auth, cookie: str) -> dict:
    user = auth.user_from_headers(cookie)
    if not user:
        raise AdminError(401, "需要登录")
    if user.get("role") != "admin":
        raise AdminError(403, "此功能仅供管理员使用")
    return user


def public_admin_user(user: dict) -> dict:
    fields = ("id", "email", "display_name", "role", "auth_provider", "is_active", "created_at", "updated_at")
    return {key: user.get(key) for key in fields}


def pagination(query: dict) -> tuple[int, int]:
    try:
        page = int(query.get("page", 1))
        size = int(query.get("page_size", 20))
    except (ValueError, TypeError):
        raise AdminError(400, "分页参数须为整数")
    if page < 1 or page > 100000 or size < 1 or size > 100:
        raise AdminError(400, "页码须为 1–100000，每页条数须为 1–100")
    return page, size


def validate_user(payload, creating=False) -> dict:
    if not isinstance(payload, dict):
        raise AdminError(400, "请提交 JSON 对象")
    allowed = {"email", "display_name", "role", "password"} if creating else {"display_name", "role", "is_active", "password"}
    if set(payload) - allowed:
        raise AdminError(400, "请求包含额外字段")
    result = dict(payload)
    if creating:
        email = result.get("email")
        if not isinstance(email, str) or len(email.strip()) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()):
            raise AdminError(400, "请填写有效邮箱")
        result["email"] = email.strip().lower()
        result.setdefault("display_name", "")
        result.setdefault("role", "teacher")
    if "display_name" in result:
        name = result["display_name"]
        if not isinstance(name, str) or len(name.strip()) > 80:
            raise AdminError(400, "显示名称上限为 80 个字符")
        result["display_name"] = name.strip()
    if "role" in result and result["role"] not in ("admin", "teacher"):
        raise AdminError(400, "角色须为 admin 或 teacher")
    if "is_active" in result and type(result["is_active"]) is not bool:
        raise AdminError(400, "账号状态须为布尔值")
    if creating or "password" in result:
        password = result.pop("password", None)
        if not isinstance(password, str) or not 12 <= len(password) <= 128:
            raise AdminError(400, "密码长度须为 12–128 个字符")
        from argon2 import PasswordHasher
        result["password_hash"] = PasswordHasher().hash(password)
    if not result:
        raise AdminError(400, "请提交要更新的字段")
    return result


class AdminService:
    def __init__(self, settings, database, auth):
        self.settings, self.database, self.auth = settings, database, auth

    @property
    def managed(self):
        return self.auth.enabled and self.database.configured

    def execute(self, method, path, cookie="", query=None, payload=None):
        actor = require_admin(self.auth, cookie)
        query = query or {}
        if path == "/api/admin/settings" and method in {"GET", "PATCH"}:
            try:
                store = get_settings_store()
                if method == "GET":
                    return store.public_state()
                return store.update(payload, actor.get("id") or actor.get("sub") or "")
            except SettingsError as error:
                raise AdminError(error.status, str(error)) from error
        if method == "GET" and path == "/api/admin/overview":
            return {
                "ok": True,
                "platform": self.settings.public_config(),
                "user_management_enabled": self.managed,
                "mode_message": "管理员操作将写入审计记录" if self.managed else "本地工作模式；配置登录与 PostgreSQL 后启用账号管理",
                "counts": self.database.admin_overview() if self.managed else None,
            }
        routes = {("GET", "/api/admin/users"), ("POST", "/api/admin/users"), ("GET", "/api/admin/audit-events")}
        updating = method == "PATCH" and re.fullmatch(r"/api/admin/users/[A-Za-z0-9_-]{1,128}", path)
        if (method, path) not in routes and not updating:
            raise AdminError(404, "管理接口不存在")
        if not self.managed:
            raise AdminError(503, "请配置登录与 PostgreSQL 以启用账号管理")
        if method == "GET":
            page, size = pagination(query)
            if path.endswith("audit-events"):
                result = self.database.admin_list_audit(page, size)
            else:
                search = str(query.get("search", "")).strip()
                role, active = query.get("role", ""), query.get("active", "")
                if len(search) > 100 or role not in ("", "admin", "teacher") or active not in ("", "true", "false"):
                    raise AdminError(400, "请检查搜索与筛选参数")
                result = self.database.admin_list_users(page, size, search, role, active)
                result["items"] = [public_admin_user(user) for user in result["items"]]
            return {"ok": True, "page": page, "page_size": size, **result}
        creating = method == "POST"
        if creating and not self.settings.builtin_enabled:
            raise AdminError(409, "当前登录模式通过 OIDC 创建账号")
        values = validate_user(payload, creating)
        try:
            user = self.database.admin_save_user(str(actor.get("id") or actor.get("sub")), None if creating else path.rsplit("/", 1)[1], values)
        except Exception as error:
            if getattr(error, "sqlstate", None) == "23505":
                raise AdminError(409, "该邮箱已被使用") from error
            raise
        return {"ok": True, "user": public_admin_user(user)}
