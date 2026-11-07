"""Small helpers shared across the app."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import urlparse

from slugify import slugify as _slugify

_SAFE_SLUG = re.compile(r"[^a-z0-9]+")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def short_id(nbytes: int = 5) -> str:
    """Unguessable, URL-safe identifier fragment (10 chars at nbytes=5)."""
    return secrets.token_hex(nbytes)


def make_slug(value: str, fallback: str = "item") -> str:
    slug = _slugify(value or "", max_length=80)
    slug = _SAFE_SLUG.sub("-", slug).strip("-")
    return slug or f"{fallback}-{short_id(3)}"


def unique_slug(session, model, value: str, *, suffix_from: str | None = None) -> str:
    """Return a slug that is not yet taken by `model`."""
    base = make_slug(value)
    candidate = base
    n = 2
    while session.query(model).filter(model.slug == candidate).first() is not None:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def share_token() -> str:
    """URL-safe, unguessable share token (~16 chars)."""
    return secrets.token_urlsafe(12)


def hash_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    salted = f"kcm::{ip}".encode()
    return hashlib.sha256(salted).hexdigest()[:32]


def safe_next(target: str | None, default: str = "/admin/") -> str:
    """Only allow same-site relative redirects."""
    if not target:
        return default
    parsed = urlparse(target)
    if parsed.scheme or parsed.netloc or not target.startswith("/"):
        return default
    return target


def format_kes(amount: int | float | None) -> str:
    if amount is None:
        return "—"
    try:
        return f"KES {int(round(float(amount))):,}"
    except (TypeError, ValueError):
        return "—"


def human_bytes(num: int | None) -> str:
    if not num:
        return "—"
    size = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def truncate(text: str | None, length: int = 155) -> str:
    if not text:
        return ""
    clean = " ".join(text.split())
    return clean if len(clean) <= length else clean[: length - 1].rstrip() + "…"
