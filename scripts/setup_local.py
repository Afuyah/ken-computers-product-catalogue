#!/usr/bin/env python
"""Create tables, an owner account and the demo catalogue.

Reliable fallback for a fresh virtualenv (no need for the `flask` executable):

    .venv\\Scripts\\python.exe scripts\\setup_local.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.cli import seed_demo  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import User  # noqa: E402

OWNER_EMAIL = "admin@kencomputersmsa.com"
OWNER_PASSWORD = "ChangeMe123!"

app = create_app()

with app.app_context():
    db.create_all()
    print("Tables created.")

    if db.session.query(User).filter_by(email=OWNER_EMAIL).first() is None:
        owner = User(email=OWNER_EMAIL, name="Owner", role="owner", is_active=True)
        owner.set_password(OWNER_PASSWORD)
        db.session.add(owner)
        db.session.commit()
        print(f"Owner created: {OWNER_EMAIL} / {OWNER_PASSWORD}")

    seed_demo.callback(products=24, images=2)
