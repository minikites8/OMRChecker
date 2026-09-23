"""Tencent Cloud COS adapter with lazy SDK loading."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Optional

from platform_config import PlatformSettings


class TencentCosStorage:
    def __init__(self, settings: PlatformSettings):
        self.settings = settings
        self._client = None

    @property
    def configured(self) -> bool:
        return self.settings.cos_configured

    def _get_client(self):
        if not self.configured:
            raise RuntimeError("腾讯云 COS 配置不完整")
        if self._client is None:
            try:
                from qcloud_cos import CosConfig, CosS3Client
            except ImportError as error:
                raise RuntimeError("腾讯云 COS 依赖缺失，请安装 cos-python-sdk-v5") from error
            config = CosConfig(
                Region=self.settings.cos_region,
                SecretId=self.settings.cos_secret_id,
                SecretKey=self.settings.cos_secret_key,
                Token=None,
                Scheme="https",
            )
            self._client = CosS3Client(config)
        return self._client

    def healthcheck(self) -> dict:
        if not self.configured:
            return {"configured": False, "ok": False, "message": "TENCENT_COS_* 未配置"}
        try:
            self._get_client().head_bucket(Bucket=self.settings.cos_bucket)
            return {"configured": True, "ok": True}
        except Exception as error:
            return {"configured": True, "ok": False, "message": str(error)}

    def put_file(self, path: Path, object_key: str, content_type: str = "application/octet-stream") -> dict:
        path = Path(path)
        with path.open("rb") as stream:
            self._get_client().put_object(
                Bucket=self.settings.cos_bucket,
                Body=stream,
                Key=object_key,
                ContentType=content_type,
            )
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return {"object_key": object_key, "size_bytes": path.stat().st_size, "checksum_sha256": digest}

    def put_bytes(self, content: bytes, object_key: str, content_type: str = "application/octet-stream") -> dict:
        self._get_client().put_object(
            Bucket=self.settings.cos_bucket,
            Body=content,
            Key=object_key,
            ContentType=content_type,
        )
        return {"object_key": object_key, "size_bytes": len(content), "checksum_sha256": hashlib.sha256(content).hexdigest()}

    def presigned_url(self, object_key: str, expires: int = 900) -> str:
        return self._get_client().get_presigned_download_url(
            Bucket=self.settings.cos_bucket,
            Key=object_key,
            Expired=expires,
        )
