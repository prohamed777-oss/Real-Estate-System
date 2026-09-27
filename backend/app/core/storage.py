"""Storage backends (A4): Supabase Storage (S3-compatible) for production,
local disk for development. Selected by SUPABASE_URL presence."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Protocol

import httpx

from app.core.config import settings

log = logging.getLogger("revenue_os.storage")

MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "application/pdf",
                  "video/mp4", "application/octet-stream"}


def sanitize_filename(filename: str) -> str:
    """Path-traversal-proof: strip dirs + whitelist safe chars."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename or "file").name)
    return safe[:120] or "file"


class StorageBackend(Protocol):
    async def put(self, key: str, content: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    def public_url(self, key: str) -> str: ...


class LocalStorage:
    """Dev-only: files on local disk."""

    def __init__(self) -> None:
        self.root = Path("/tmp/revenue-os-media")

    async def put(self, key: str, content: bytes, content_type: str) -> str:
        path = self.root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return f"local://{path}"

    async def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def public_url(self, key: str) -> str:
        return f"/media/{key}"


class SupabaseStorage:
    """Production: Supabase Storage (S3-compatible REST)."""

    def __init__(self) -> None:
        self.base = f"{settings.supabase_url}/storage/v1"
        self.key = settings.supabase_anon_key  # service_key preferred in production
        self.bucket = settings.storage_bucket

    async def put(self, key: str, content: bytes, content_type: str) -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.base}/object/{self.bucket}/{key}",
                content=content,
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": content_type,
                    "x-upsert": "true",
                },
            )
        if resp.status_code >= 400:
            log.error("Supabase storage upload failed: %d %s", resp.status_code, resp.text[:200])
            raise RuntimeError(f"Storage upload failed: {resp.status_code}")
        return f"supabase://{self.bucket}/{key}"

    async def get(self, key: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.base}/object/{self.bucket}/{key}",
                headers={"Authorization": f"Bearer {self.key}"},
            )
        resp.raise_for_status()
        return resp.content

    def public_url(self, key: str) -> str:
        return f"{self.base}/object/public/{self.bucket}/{key}"


def get_storage() -> StorageBackend:
    if settings.supabase_url and settings.app_env == "production":
        return SupabaseStorage()
    return LocalStorage()


def validate_upload(content: bytes, content_type: str) -> None:
    if len(content) > MAX_FILE_SIZE:
        raise ValueError(f"File too large: {len(content)} bytes (max {MAX_FILE_SIZE})")
    if content_type not in ALLOWED_TYPES:
        raise ValueError(f"Content type {content_type!r} not allowed")
