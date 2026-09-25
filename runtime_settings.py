"""Administrator-managed settings with environment fallback and atomic persistence."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


class SettingsError(ValueError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def field(key, group, label, kind="string", default="", *, secret=False,
          apply="restart", options=None, minimum=None, maximum=None, aliases=(), help=""):
    return {"key": key, "group": group, "label": label, "type": kind,
            "default": default, "secret": secret, "apply": apply,
            "options": options, "minimum": minimum, "maximum": maximum,
            "aliases": list(aliases), "help": help}


FIELDS = [
    field("HANDWRITING_AI_ENDPOINT", "ai", "AI 接口地址", "url", "https://api.openai.com/v1/chat/completions", apply="live", aliases=("OPENAI_CHAT_COMPLETIONS_URL",)),
    field("HANDWRITING_AI_MODEL", "ai", "AI 模型", default="gpt-4o-mini", apply="live", aliases=("OPENAI_MODEL",)),
    field("HANDWRITING_AI_API_KEY", "ai", "AI API Key", secret=True, apply="live", aliases=("OPENAI_API_KEY",)),
    field("HANDWRITING_AI_TIMEOUT", "ai", "AI 超时（秒）", "integer", 180, apply="live", minimum=10, maximum=300),
    field("HANDWRITING_AI_CONCURRENCY", "ai", "单任务 AI 请求并发", "integer", 3, apply="live", minimum=1, maximum=8),
    field("HANDWRITING_AI_MAX_RETRIES", "ai", "AI 临时错误重试次数", "integer", 3, apply="live", minimum=0, maximum=6, help="针对 HTTP 408、429、500、502、503、504、529 使用指数退避重试。"),
    field("OMR_LOCAL_OCR_ENABLED", "recognition", "默认启用本地 OCR", "boolean", True, apply="live", help="关闭后默认使用仅 AI 识别；任务表单仍可单独选择识别方式。"),
    field("OMR_REVIEW_WORKERS", "recognition", "批改任务工作线程", "integer", min(4, max(2, (os.cpu_count() or 2) // 2)), minimum=1, maximum=4),
    field("OMR_AI_WORKERS", "recognition", "AI 后台任务工作线程", "integer", 3, minimum=1, maximum=4),
    field("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "recognition", "跳过 Paddle 模型源检查", "boolean", True),
    field("OMR_MAX_REQUEST_MB", "recognition", "单次上传总量（MB）", "integer", 256, minimum=1, maximum=1024),
    field("OMR_MAX_FILE_MB", "recognition", "单个文件大小（MB）", "integer", 24, minimum=1, maximum=1024),
    field("OMR_AUTH_MODE", "auth", "登录模式", "enum", "disabled", options=["disabled", "builtin", "oidc", "oidc_or_builtin"]),
    field("OMR_SESSION_SECRET", "auth", "会话签名密钥", secret=True, help="至少 32 个字符；更换后，重启服务会注销现有登录会话。"),
    field("OMR_SESSION_TTL_SECONDS", "auth", "会话有效期（秒）", "integer", 28800, minimum=300, maximum=2592000),
    field("OMR_PUBLIC_BASE_URL", "auth", "平台公开地址", "url", "http://127.0.0.1:8765"),
    field("OMR_BOOTSTRAP_ADMIN_EMAIL", "auth", "初始管理员邮箱", "email", help="在新数据库中创建初始管理员；现有账号通过用户管理维护。"),
    field("OMR_BOOTSTRAP_ADMIN_PASSWORD", "auth", "初始管理员密码", secret=True, help="至少 12 个字符；与密码哈希同时配置时，密码哈希优先。"),
    field("OMR_BOOTSTRAP_ADMIN_PASSWORD_HASH", "auth", "初始管理员密码哈希", secret=True, help="Argon2 格式；用于新数据库初始化。"),
    field("OIDC_ISSUER_URL", "oidc", "OIDC 发行者地址", "url"),
    field("OIDC_CLIENT_ID", "oidc", "OIDC Client ID"),
    field("OIDC_CLIENT_SECRET", "oidc", "OIDC Client Secret", secret=True),
    field("OIDC_REDIRECT_URI", "oidc", "OIDC 回调地址", "url"),
    field("OIDC_SCOPES", "oidc", "OIDC Scopes", default="openid profile email"),
    field("OIDC_DEFAULT_ROLE", "oidc", "OIDC 新账号默认角色", "enum", "teacher", options=["teacher", "admin"]),
    field("OMR_PERSISTENCE_MODE", "storage", "持久化模式", "enum", "local", options=["local", "postgres_cos"]),
    field("DATABASE_URL", "storage", "PostgreSQL 连接地址", secret=True, help="postgresql://用户:密码@主机:端口/数据库；保存后重启连接新数据库。"),
    field("OMR_DATA_ROOT", "storage", "业务数据目录", "path", str(Path(__file__).resolve().parent), help="更换目录后重启生效；原有数据保留在原目录。"),
    field("TENCENT_COS_SECRET_ID", "cos", "COS Secret ID", secret=True),
    field("TENCENT_COS_SECRET_KEY", "cos", "COS Secret Key", secret=True),
    field("TENCENT_COS_REGION", "cos", "COS 地域"),
    field("TENCENT_COS_BUCKET", "cos", "COS Bucket"),
    field("TENCENT_COS_APPID", "cos", "COS App ID"),
    field("TENCENT_COS_PREFIX", "cos", "COS 对象前缀", default="omrchecker"),
    field("OMR_HOST", "server", "服务监听地址", default="127.0.0.1", help="Docker 通常使用 0.0.0.0；由服务启动入口读取。"),
    field("PORT", "server", "FastAPI 服务端口", "integer", 8000, minimum=1, maximum=65535),
    field("OMR_PORT", "server", "本地 HTTP 服务端口", "integer", 8765, minimum=1, maximum=65535, aliases=("PORT",)),
    field("FRONTEND_ORIGINS", "server", "允许访问的前端源", default="http://localhost:8765", help="多个 http(s) 源用英文逗号分隔；包含协议、域名和可选端口。"),
]
DEFINITIONS = {entry["key"]: entry for entry in FIELDS}
GROUPS = {"ai": "AI 服务", "recognition": "识别与资源", "auth": "登录与会话",
          "oidc": "OIDC 单点登录", "storage": "数据存储", "cos": "腾讯云 COS", "server": "服务与跨域"}


def text_value(value):
    return str(value).lower() if isinstance(value, bool) else str(value)


def resolve_value(key, values, default=None):
    if key in values:
        return values[key], "saved"
    spec = DEFINITIONS[key]
    for name in (key, *spec["aliases"]):
        value = os.environ.get(name, "").strip()
        if value:
            return value, "environment"
    return spec["default"] if default is None else default, "default"


def display_value(spec, value):
    if spec["type"] == "boolean":
        return text_value(value).lower() in ("true", "1", "yes", "on")
    if spec["type"] == "integer":
        try:
            return max(spec["minimum"], min(spec["maximum"], int(value)))
        except (ValueError, TypeError):
            return spec["default"]
    return str(value)


def valid_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and parsed.port != 0 and not parsed.username and not parsed.password and not any(c.isspace() for c in value)
    except ValueError:
        return False


def validate_value(key, value):
    spec = DEFINITIONS[key]
    kind = spec["type"]
    message = spec["label"] + "："
    if kind == "boolean":
        if type(value) is not bool:
            raise SettingsError(400, message + "请使用布尔值")
        return value
    if kind == "integer":
        if type(value) is not int or not spec["minimum"] <= value <= spec["maximum"]:
            raise SettingsError(400, message + f"范围为 {spec['minimum']}–{spec['maximum']} 的整数")
        return value
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise SettingsError(400, message + "请填写 4096 字符以内的单行文本")
    value = value.strip()
    if kind == "enum" and value not in spec["options"]:
        raise SettingsError(400, message + "请选择有效选项")
    if kind == "url" and value and not valid_url(value):
        raise SettingsError(400, message + "请使用有效的 http(s) 地址")
    if kind == "email" and value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise SettingsError(400, message + "请填写有效邮箱")
    if key in {"HANDWRITING_AI_ENDPOINT", "HANDWRITING_AI_MODEL", "OMR_DATA_ROOT", "OMR_PUBLIC_BASE_URL", "OMR_HOST"} and not value:
        raise SettingsError(400, message + "请填写内容")
    if key == "OMR_SESSION_SECRET" and value and len(value) < 32:
        raise SettingsError(400, message + "长度至少为 32 个字符")
    if key == "OMR_BOOTSTRAP_ADMIN_PASSWORD" and value and not 12 <= len(value) <= 128:
        raise SettingsError(400, message + "长度为 12–128 个字符")
    if key == "OMR_BOOTSTRAP_ADMIN_PASSWORD_HASH" and value:
        from argon2 import extract_parameters
        try:
            extract_parameters(value)
        except (ValueError, TypeError):
            raise SettingsError(400, message + "请使用有效的 Argon2 哈希")
    if key == "DATABASE_URL" and value:
        try:
            parsed = urlsplit(value)
            valid = parsed.scheme in {"postgres", "postgresql"} and parsed.hostname and parsed.path.strip("/") and parsed.port != 0
        except ValueError:
            valid = False
        if not valid:
            raise SettingsError(400, message + "请使用有效的 PostgreSQL URL")
    if key == "OMR_HOST" and not re.fullmatch(r"[A-Za-z0-9_.:\-]+", value):
        raise SettingsError(400, message + "请使用主机名或 IP 地址")
    if key == "FRONTEND_ORIGINS" and value:
        for origin in value.split(","):
            origin = origin.strip()
            if not valid_url(origin) or urlsplit(origin).path not in ("", "/") or urlsplit(origin).query or urlsplit(origin).fragment:
                raise SettingsError(400, message + "请使用逗号分隔的 http(s) 源")
    if key == "TENCENT_COS_PREFIX" and (not value or not re.fullmatch(r"[A-Za-z0-9/_-]+", value)):
        raise SettingsError(400, message + "请使用字母、数字、斜线、下划线或连字符")
    return value


def validate_combination(values):
    get = lambda key: resolve_value(key, values)[0]
    required = []
    mode = get("OMR_AUTH_MODE")
    if mode != "disabled":
        required += ["OMR_SESSION_SECRET", "DATABASE_URL"]
    if mode in {"oidc", "oidc_or_builtin"}:
        required += ["OIDC_ISSUER_URL", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET"]
        if "openid" not in str(get("OIDC_SCOPES")).split():
            raise SettingsError(400, "OIDC Scopes 须包含 openid")
    if get("OMR_PERSISTENCE_MODE") == "postgres_cos":
        required += ["DATABASE_URL", "TENCENT_COS_SECRET_ID", "TENCENT_COS_SECRET_KEY", "TENCENT_COS_REGION", "TENCENT_COS_BUCKET", "TENCENT_COS_APPID"]
    missing = [DEFINITIONS[key]["label"] for key in required if not get(key)]
    if missing:
        raise SettingsError(400, "请补充关联配置：" + "、".join(missing))
    if int(get("OMR_MAX_FILE_MB")) > int(get("OMR_MAX_REQUEST_MB")):
        raise SettingsError(400, "单次上传总量须大于等于单个文件大小")


class SettingsStore:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.RLock()
        initial = self.read()
        self.active = {key: text_value(resolve_value(key, initial["values"])[0]) for key in DEFINITIONS}

    def read(self):
        try:
            if not self.path.exists():
                return {"version": 1, "revision": 0, "values": {}, "audit": []}
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or data.get("version") != 1 or type(data.get("revision")) is not int
                    or data["revision"] < 0 or not isinstance(data.get("values"), dict)
                    or set(data["values"]) - DEFINITIONS.keys() or not isinstance(data.get("audit"), list)):
                raise ValueError("schema")
            for key, value in data["values"].items():
                validate_value(key, value)
            return data
        except (OSError, ValueError, TypeError) as error:
            raise SettingsError(503, "配置文件读取失败，请检查服务端配置文件") from error

    @contextmanager
    def writing(self):
        # File locking serializes independent worker processes; replace keeps reads atomic.
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with open(str(self.path) + ".lock", "a+b") as lock:
                lock.seek(0, os.SEEK_END)
                if lock.tell() == 0:
                    lock.write(b"0"); lock.flush()
                lock.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    lock.seek(0)
                    if os.name == "nt":
                        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock, fcntl.LOCK_UN)

    def get(self, key, default=None):
        return text_value(resolve_value(key, self.read()["values"], default)[0])

    def public_state(self, data=None):
        data = self.read() if data is None else data
        entries, pending = [], []
        for spec in FIELDS:
            key = spec["key"]
            value, source = resolve_value(key, data["values"])
            restart = spec["apply"] == "restart" and text_value(value) != self.active[key]
            if restart:
                pending.append(key)
            entries.append({**spec, "default": None if spec["secret"] else spec["default"],
                            "value": None if spec["secret"] else display_value(spec, value),
                            "configured": bool(value), "source": source, "overridden": key in data["values"],
                            "pending_restart": restart})
        return {"ok": True, "revision": data["revision"], "groups": GROUPS, "fields": entries,
                "pending_restart": pending, "audit": data["audit"][-20:][::-1]}

    def update(self, payload, actor_id):
        if not isinstance(payload, dict) or set(payload) - {"revision", "values", "reset"}:
            raise SettingsError(400, "请提交 revision、values 与可选 reset 字段")
        revision, values, resets = payload.get("revision"), payload.get("values", {}), payload.get("reset", [])
        if type(revision) is not int or revision < 0 or not isinstance(values, dict) or not isinstance(resets, list) or not all(isinstance(k, str) for k in resets):
            raise SettingsError(400, "配置版本或参数格式错误")
        if (set(values) | set(resets)) - DEFINITIONS.keys() or set(values) & set(resets):
            raise SettingsError(400, "请检查参数名称以及保存与恢复默认的选项")
        if not values and not resets:
            raise SettingsError(400, "请提交要修改的配置")
        validated = {key: validate_value(key, value) for key, value in values.items()}
        with self.writing():
            current = self.read()
            if revision != current["revision"]:
                raise SettingsError(409, "配置已被其他管理员更新，请重新加载后保存")
            merged = {key: value for key, value in current["values"].items() if key not in resets}
            merged.update(validated)
            validate_combination(merged)
            changed = sorted(key for key in set(validated) | set(resets)
                             if (key in current["values"]) != (key in merged) or current["values"].get(key) != merged.get(key))
            if not changed:
                return {**self.public_state(current), "changed_keys": []}
            audit = {"revision": revision + 1, "actor_user_id": str(actor_id), "action": "admin.settings.update",
                     "changed_fields": changed, "created_at": datetime.now(timezone.utc).isoformat()}
            data = {"version": 1, "revision": revision + 1, "values": merged, "audit": (current["audit"] + [audit])[-200:]}
            descriptor, temporary = tempfile.mkstemp(prefix=".settings-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(data, output, ensure_ascii=False, indent=2)
                    output.flush(); os.fsync(output.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return {**self.public_state(data), "changed_keys": changed}


_STORES = {}
_STORE_LOCK = threading.Lock()


def settings_path():
    # This bootstrap location stays stable when the business data directory changes.
    explicit = os.environ.get("OMR_SETTINGS_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    base = Path(os.environ.get("OMR_DATA_ROOT") or (Path(__file__).resolve().parent / "data"))
    return base.expanduser().resolve() / "platform-settings.json"


def get_settings_store():
    path = settings_path()
    with _STORE_LOCK:
        if path not in _STORES:
            _STORES[path] = SettingsStore(path)
        return _STORES[path]


def get_setting(key, default=None):
    return get_settings_store().get(key, default)
