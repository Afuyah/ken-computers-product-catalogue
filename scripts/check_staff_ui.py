"""Verify the staff/public split on product pages."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Product  # noqa: E402

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

with app.app_context():
    product = db.session.query(Product).filter_by(is_published=True).first()
    slug = product.slug

client = app.test_client()

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    print(("PASS  " if condition else "FAIL  ") + label)
    if not condition:
        failures.append(label)


anon = client.get(f"/p/{slug}").get_data(as_text=True)
check("anonymous sees Order on WhatsApp", "Order on WhatsApp" in anon)
check("anonymous sees Staff login", "Staff login" in anon)
check("anonymous cannot see share panel", "Share this item" not in anon)
check("anonymous cannot see downloads", "All images (ZIP)" not in anon)

client.post(
    "/admin/login",
    data={"email": "admin@kencomputersmsa.com", "password": "ChangeMe123!"},
    follow_redirects=True,
)
auth = client.get(f"/p/{slug}").get_data(as_text=True)
check("staff sees share panel", "Share this item" in auth)
check("staff sees number field", 'id="share-phone"' in auth)
check("staff sees downloads", "All images (ZIP)" in auth)
check("staff does not see Order on WhatsApp", "Order on WhatsApp" not in auth)
check("staff sees sign out", "Sign out" in auth)

search = client.get("/search?q=hp").get_data(as_text=True)
check("search page has filters", 'name="sort"' in search and "Best match" in search)

print()
if failures:
    raise SystemExit(f"{len(failures)} check(s) failed")
print("Staff/public UI checks passed.")
