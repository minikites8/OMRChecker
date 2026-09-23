"""Runtime configuration for the deployed OMRChecker platform."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


_ALLOWED_AUTH_MODES = {"disabled", "oidc", "builtin", "oidc_or_builtin"}
_ALLOWED_PERSISTENCE_MODES = {"local", "postgres_cos"}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(maximum, int(_env(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _normalize_mode(value: str, allowed: set[str], default: str) -> str:
    value = (value or default).strip().lower()
    return value if value in allowed else default


def _normalize_prefix(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9/_-]+", "-", value.strip()).strip("/")
    return value or "omrchecker"


@dataclass(frozen=True)
class PlatformSettings:
    auth_mode: str
    persistence_mode: str
    session_secret: str
    session_ttl_seconds: int
    public_base_url: str
    oidc_issuer_url: str
    oidc_client_id: str
    oidc_client_secret: str
    oidc_redirect_uri: str
    oidc_scopes: str
    default_oidc_role: str
    database_url: str
    cos_secret_id: str
    cos_secret_key: str
    cos_region: str
    cos_bucket: str
    cos_appid: str
    cos_prefix: str
    bootstrap_admin_email: str
    bootstrap_admin_password: str
    bootstrap_admin_password_hash: str
    data_root: Path

    @classmethod
    def from_env(cls) -> "PlatformSettings":
        data_root = Path(_env("OMR_DATA_ROOT", "./data")).expanduser().resolve()
        return cls(
            auth_mode=_normalize_mode(_env("OMR_AUTH_MODE", "disabled"), _ALLOWED_AUTH_MODES, "disabled"),
            persistence_mode=_normalize_mode(_env("OMR_PERSISTENCE_MODE", "local"), _ALLOWED_PERSISTENCE_MODES, "local"),
            session_secret=_env("OMR_SESSION_SECRET"),
            session_ttl_seconds=_int_env("OMR_SESSION_TTL_SECONDS", 28800, 300, 2592000),
            public_base_url=_env("OMR_PUBLIC_BASE_URL", "http://127.0.0.1:8765").rstrip("/"),
            oidc_issuer_url=_env("OIDC_ISSUER_URL").rstrip("/"),
            oidc_client_id=_env("OIDC_CLIENT_ID"),
            oidc_client_secret=_env("OIDC_CLIENT_SECRET"),
            oidc_redirect_uri=_env("OIDC_REDIRECT_URI"),
            oidc_scopes=_env("OIDC_SCOPES", "openid profile email"),
            default_oidc_role=_env("OIDC_DEFAULT_ROLE", "teacher") or "teacher",
            database_url=_env("DATABASE_URL"),
            cos_secret_id=_env("TENCENT_COS_SECRET_ID"),
            cos_secret_key=_env("TENCENT_COS_SECRET_KEY"),
            cos_region=_env("TENCENT_COS_REGION"),
            cos_bucket=_env("TENCENT_COS_BUCKET"),
            cos_appid=_env("TENCENT_COS_APPID"),
            cos_prefix=_normalize_prefix(_env("TENCENT_COS_PREFIX", "omrchecker")),
            bootstrap_admin_email=_env("OMR_BOOTSTRAP_ADMIN_EMAIL").lower(),
            bootstrap_admin_password=_env("OMR_BOOTSTRAP_ADMIN_PASSWORD"),
            bootstrap_admin_password_hash=_env("OMR_BOOTSTRAP_ADMIN_PASSWORD_HASH"),
            data_root=data_root,
        )

    @property
    def auth_enabled(self) -> bool:
        return self.auth_mode != "disabled"

    @property
    def oidc_enabled(self) -> bool:
        return self.auth_mode in {"oidc", "oidc_or_builtin"}

    @property
    def builtin_enabled(self) -> bool:
        return self.auth_mode in {"builtin", "oidc_or_builtin"}

    @property
    def postgres_configured(self) -> bool:
        return bool(self.database_url)

    @property
    def cos_configured(self) -> bool:
        return all(
            (
                self.cos_secret_id,
                self.cos_secret_key,
                self.cos_region,
                self.cos_bucket,
                self.cos_appid,
            )
        )

    def validate_startup(self) -> list[str]:
        errors = []
        if self.auth_enabled and not self.session_secret:
            errors.append("OMR_SESSION_SECRET 未配置")
        if self.oidc_enabled:
            for name, value in (
                ("OIDC_ISSUER_URL", self.oidc_issuer_url),
                ("OIDC_CLIENT_ID", self.oidc_client_id),
                ("OIDC_CLIENT_SECRET", self.oidc_client_secret),
            ):
                if not value:
                    errors.append(f"{name} 未配置")
        if self.auth_enabled and not self.postgres_configured:
            errors.append("登录系统需要 DATABASE_URL")
        if self.persistence_mode == "postgres_cos":
            if not self.postgres_configured:
                errors.append("PostgreSQL 持久化需要 DATABASE_URL")
            if not self.cos_configured:
                errors.append("腾讯云 COS 持久化需要 TENCENT_COS_* 配置")
        return errors

    def public_config(self) -> dict:
        return {
            "auth_mode": self.auth_mode,
            "persistence_mode": self.persistence_mode,
            "oidc_enabled": self.oidc_enabled,
            "builtin_enabled": self.builtin_enabled,
            "postgres_configured": self.postgres_configured,
            "cos_configured": self.cos_configured,
        }
