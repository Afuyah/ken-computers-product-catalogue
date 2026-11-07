"""Media storage backends.

Two implementations share one tiny interface:

* ``LocalStorage``  – writes under MEDIA_ROOT and is served by Flask. Dev only.
* ``R2Storage``     – Cloudflare R2 (S3 API) with presigned download links.

Nothing in the app ever streams image bytes through gunicorn in production.
"""

from __future__ import annotations

import re
from pathlib import Path

from flask import current_app, url_for

_UNSAFE_FILENAME = re.compile(r'[^A-Za-z0-9._ -]+')


def _safe_filename(filename: str) -> str:
    cleaned = _UNSAFE_FILENAME.sub("", filename or "download").strip() or "download"
    return cleaned[:120]


class Storage:
    def put(self, key: str, data: bytes, content_type: str) -> None:
        raise NotImplementedError

    def url(self, key: str) -> str:
        raise NotImplementedError

    def download_url(self, key: str, filename: str, expires: int = 900) -> str:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def read(self, key: str) -> bytes:
        raise NotImplementedError


class LocalStorage(Storage):
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError("Path escapes media root")
        return path

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def url(self, key: str) -> str:
        return url_for("public.serve_media", filename=key)

    def download_url(self, key: str, filename: str, expires: int = 900) -> str:
        return url_for(
            "public.serve_media", filename=key, download=_safe_filename(filename)
        )

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError:
            pass

    def read(self, key: str) -> bytes:
        return self._path(key).read_bytes()


class R2Storage(Storage):
    def __init__(self, cfg) -> None:
        import boto3
        from botocore.config import Config as BotoConfig

        self.bucket = cfg["R2_BUCKET"]
        self.cdn_base = cfg["CDN_BASE_URL"].rstrip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{cfg['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
            aws_access_key_id=cfg["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=cfg["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
            config=BotoConfig(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
            CacheControl="public, max-age=31536000, immutable",
        )

    def url(self, key: str) -> str:
        return f"{self.cdn_base}/{key}"

    def download_url(self, key: str, filename: str, expires: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentDisposition": (
                    f'attachment; filename="{_safe_filename(filename)}"'
                ),
            },
            ExpiresIn=expires,
        )

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:  # pragma: no cover - best effort cleanup
            current_app.logger.warning("Could not delete %s from R2", key, exc_info=True)

    def read(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()


_storage: Storage | None = None
_storage_key: tuple | None = None


def get_storage() -> Storage:
    global _storage, _storage_key

    cfg = current_app.config
    key = (
        cfg["STORAGE_BACKEND"],
        cfg.get("MEDIA_ROOT"),
        cfg.get("R2_BUCKET"),
        cfg.get("CDN_BASE_URL"),
    )
    if _storage is None or _storage_key != key:
        if cfg["STORAGE_BACKEND"] == "r2":
            _storage = R2Storage(cfg)
        else:
            _storage = LocalStorage(cfg["MEDIA_ROOT"])
        _storage_key = key
    return _storage


def storage_is_local() -> bool:
    return current_app.config["STORAGE_BACKEND"] != "r2"
