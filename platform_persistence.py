"""Persistence coordinator for PostgreSQL metadata and Tencent COS objects."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Optional

from platform_config import PlatformSettings
from platform_cos import TencentCosStorage
from platform_database import PostgresStore


class PlatformPersistence:
    def __init__(self, settings: PlatformSettings, database: PostgresStore, cos: TencentCosStorage):
        self.settings = settings
        self.database = database
        self.cos = cos

    @property
    def enabled(self) -> bool:
        return self.settings.persistence_mode == "postgres_cos"

    def startup(self) -> None:
        if not self.enabled:
            return
        errors = self.settings.validate_startup()
        if errors:
            raise RuntimeError("；".join(errors))
        self.database.ensure_schema()
        self.database.ensure_bootstrap_admin(
            self.settings.bootstrap_admin_email,
            self.settings.bootstrap_admin_password,
            self.settings.bootstrap_admin_password_hash,
        )

    def health(self) -> dict:
        return {
            "enabled": self.enabled,
            "postgres": self.database.healthcheck() if self.enabled else {"configured": self.database.configured},
            "cos": self.cos.healthcheck() if self.enabled else {"configured": self.cos.configured},
        }

    def _key(self, kind: str, relative_path: Path) -> str:
        safe_kind = re.sub(r"[^A-Za-z0-9/_-]+", "-", str(kind)).strip("/") or "artifact"
        safe_path = "/".join(
            re.sub(r"[^A-Za-z0-9._-]+", "-", part) for part in Path(relative_path).parts
        )
        return f"{self.settings.cos_prefix}/{safe_kind}/{safe_path}"

    def sync_file(self, path: Path, kind: str, owner_user_id: str = "", relative_path: Optional[Path] = None) -> str:
        if not self.enabled:
            return ""
        path = Path(path)
        object_key = self._key(kind, relative_path or path.name)
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        result = self.cos.put_file(path, object_key, content_type)
        self.database.register_artifact(
            owner_user_id, kind, object_key, content_type,
            result["size_bytes"], result["checksum_sha256"],
        )
        return object_key

    def sync_tree(self, root: Path, kind: str, owner_user_id: str = "") -> list[str]:
        if not self.enabled:
            return []
        root = Path(root)
        keys = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            keys.append(self.sync_file(path, kind, owner_user_id, path.relative_to(root)))
        return keys

    def record_job(self, job_id: str, owner_user_id: str, kind: str, status: str, payload: dict) -> None:
        if self.enabled:
            self.database.record_job(job_id, owner_user_id, kind, status, payload)
