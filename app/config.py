"""Application configuration, driven entirely by environment variables."""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _normalise_db_url(url: str) -> str:
    """Railway/Heroku hand out postgres:// or postgresql:// URLs.

    SQLAlchemy 2.x with psycopg 3 wants postgresql+psycopg://.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _default_database_url() -> str:
    instance = BASE_DIR / "instance"
    instance.mkdir(exist_ok=True)
    return f"sqlite:///{instance / 'catalogue.db'}"


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")

    _DATABASE_URL = _normalise_db_url(
        os.getenv("DATABASE_URL") or _default_database_url()
    )
    SQLALCHEMY_DATABASE_URI = _DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = (
        {"pool_pre_ping": True}
        if _DATABASE_URL.startswith("sqlite")
        else {
            "pool_pre_ping": True,
            "pool_size": int(os.getenv("DB_POOL_SIZE", "5")),
            "max_overflow": 2,
            "pool_recycle": 300,
        }
    )

    SITE_NAME = os.getenv("SITE_NAME", "Ken Computers MSA")
    SITE_URL = os.getenv("SITE_URL", "http://127.0.0.1:5000").rstrip("/")
    CONTACT_WHATSAPP = os.getenv("CONTACT_WHATSAPP", "254700000000")
    CONTACT_PHONE = os.getenv("CONTACT_PHONE", "+254 700 000 000")
    CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "sales@kencomputersmsa.com")
    CONTACT_LOCATION = os.getenv("CONTACT_LOCATION", "Mombasa, Kenya")

    # --- media -------------------------------------------------------------
    STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
    MEDIA_ROOT = str(Path(os.getenv("MEDIA_ROOT", str(BASE_DIR / "media"))).resolve())
    CDN_BASE_URL = os.getenv("CDN_BASE_URL", "").rstrip("/")

    R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
    R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
    R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
    R2_BUCKET = os.getenv("R2_BUCKET", "kencomputers-media")

    MAX_CONTENT_LENGTH = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1024 * 1024
    IMAGE_WIDTHS = tuple(
        int(w) for w in os.getenv("IMAGE_WIDTHS", "400,800,1600").split(",") if w.strip()
    )
    IMAGE_QUALITY = int(os.getenv("IMAGE_QUALITY", "78"))

    PRODUCTS_PER_PAGE = int(os.getenv("PRODUCTS_PER_PAGE", "24"))
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "0") == "1"
    REMEMBER_COOKIE_DURATION = 60 * 60 * 24 * 14


class TestConfig(Config):
    TESTING = True
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    WTF_CSRF_ENABLED = False
