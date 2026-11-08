"""Quick, readable check that catalogue search returns sensible results."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import func, select  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.search import build_query  # noqa: E402

CASES = ["hp", "i5", "laser", "eliteb", "elitbok", "toner", "16gb", "zzzz"]

app = create_app()
with app.app_context():
    print(f"{'query':<10} {'hits':>5}   top result")
    print("-" * 60)
    for query in CASES:
        stmt = build_query(query)
        total = db.session.scalar(select(func.count()).select_from(stmt.subquery()))
        first = db.session.scalars(stmt.limit(1)).first()
        title = first.title[:38] if first else "(none)"
        print(f"{query:<10} {total:>5}   {title}")
