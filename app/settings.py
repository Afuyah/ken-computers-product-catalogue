"""Site-wide switches, stored in the database so staff can change them live."""

from __future__ import annotations

import time

from flask import current_app

from .extensions import db
from .models import Setting

DEFAULTS = {
    "show_prices": "1",
}

# A short per-process cache keeps this off the critical path without making
# staff wait long for a change to appear.
_TTL_SECONDS = 30
_cache: dict[str, tuple[str, float]] = {}


def get_setting(key: str) -> str:
    now = time.monotonic()
    cached = _cache.get(key)
    if cached is not None and now - cached[1] < _TTL_SECONDS:
        return cached[0]

    try:
        row = db.session.get(Setting, key)
        value = row.value if row is not None else DEFAULTS.get(key, "")
    except Exception:  # noqa: BLE001 - a missing table must not break the site
        db.session.rollback()
        current_app.logger.warning("Could not read setting %s", key, exc_info=True)
        value = DEFAULTS.get(key, "")

    _cache[key] = (value, now)
    return value


def set_setting(key: str, value: str) -> None:
    row = db.session.get(Setting, key)
    if row is None:
        db.session.add(Setting(key=key, value=str(value)))
    else:
        row.value = str(value)
    db.session.commit()
    _cache.pop(key, None)


def prices_visible() -> bool:
    """When false the catalogue is a pure showcase: no prices anywhere."""
    return get_setting("show_prices") == "1"
