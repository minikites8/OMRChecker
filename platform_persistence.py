"""Persistence coordinator for PostgreSQL metadata and Tencent COS objects."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlsplit

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
        from workspace_context import current_workspace
        workspace = current_workspace()
        prefix = self.settings.cos_prefix
        if workspace and workspace["id"] != "shared":
            prefix += "/workspaces/" + workspace["id"]
        return f"{prefix}/{safe_kind}/{safe_path}"

    def _media_key(self, url: str) -> str:
        """Resolve a browser asset URL within the current workspace's COS prefix."""
        from workspace_context import current_workspace, split_workspace_path
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc:
            return ""
        workspace_id, path = split_workspace_path(parsed.path)
        if workspace_id:
            workspace = current_workspace()
            if not workspace or workspace["id"] != workspace_id:
                return ""
        match = re.fullmatch(r"/(reviews|imports|jobs)/(.+)", path)
        if not match:
            return ""
        relative = unquote(match.group(2))
        if (any(part in {"", ".", ".."} for part in relative.split("/"))
                or any(character in relative for character in ("\\", "\x00", ":"))):
            return ""
        content_type = mimetypes.guess_type(relative)[0] or ""
        if content_type != "application/pdf" and not content_type.startswith("image/"):
            return ""
        kind = {"reviews": "review", "imports": "exam_import", "jobs": "scan_job"}[match.group(1)]
        return self._key(kind, Path(relative))

    def resolve_asset_urls(self, payload):
        """Return fresh signed links for registered COS images/PDFs; keep stored JSON portable."""
        if not self.enabled:
            return payload
        keys = {}
        def collect(value):
            if isinstance(value, dict):
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
            elif isinstance(value, str) and value.startswith("/"):
                key = self._media_key(value)
                if key:
                    keys[value] = key
        collect(payload)
        if not keys:
            return payload
        objects = self.database.find_artifacts(list(dict.fromkeys(keys.values())))
        requested = set(keys.values())
        signed = {}
        for item in objects:
            mime = str(item.get("content_type") or "").split(";", 1)[0].lower()
            key = item["object_key"]
            if key in requested and (mime == "application/pdf" or mime.startswith("image/")):
                signed[key] = self.cos.presigned_url(key)
        def replace(value):
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            if isinstance(value, str) and keys.get(value) in signed:
                fragment = urlsplit(value).fragment
                return signed[keys[value]] + ("#" + fragment if fragment else "")
            return value
        return replace(payload)

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

    def sync_tree(
        self, root: Path, kind: str, owner_user_id: str = "",
        relative_prefix: Optional[Path] = None,
    ) -> list[str]:
        if not self.enabled:
            return []
        root = Path(root)
        keys = []
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root)
            if relative_prefix is not None:
                relative = Path(relative_prefix) / relative
            keys.append(self.sync_file(path, kind, owner_user_id, relative))
        return keys

    def record_job(self, job_id: str, owner_user_id: str, kind: str, status: str, payload: dict) -> None:
        if self.enabled:
            from workspace_context import current_workspace
            workspace = current_workspace()
            if workspace:
                payload = {**payload, "workspace_id": workspace["id"]}
            if workspace and workspace['id'] != 'shared':
                job_id = workspace['id'] + ':' + job_id
            self.database.record_job(job_id, owner_user_id, kind, status, payload)


    def delete_review(self, review_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", str(review_id or "")):
            raise ValueError("请提供有效的批改编号")
        if self.enabled:
            prefix = self._key("review", Path(review_id)) + "/"
            self.cos.delete_prefix(prefix)
            from workspace_context import current_workspace
            workspace = current_workspace()
            record_id = workspace['id'] + ':' + review_id if workspace and workspace['id'] != 'shared' else review_id
            self.database.delete_review_artifacts(record_id, prefix)
