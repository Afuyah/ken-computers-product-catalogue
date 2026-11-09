"""Verify the staff/public split on product pages."""

from __future__ import annotations

import os
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
check("anonymous sees customer footer WhatsApp", "Chat on WhatsApp" in anon)
check("anonymous has no staff workspace", "Staff workspace" not in anon)

client.post(
    "/admin/login",
    data={
        "username": os.getenv("SMOKE_USERNAME", "admin"),
        "password": os.getenv("SMOKE_PASSWORD", "ChangeMe123!"),
    },
    follow_redirects=True,
)
auth = client.get(f"/p/{slug}").get_data(as_text=True)
check("staff sees share panel", "Share this item" in auth)
check("staff sees number field", 'id="share-phone"' in auth)
check("staff sees downloads", "All images (ZIP)" in auth)
check("staff does not see Order on WhatsApp", "Order on WhatsApp" not in auth)
check("staff sees sign out", "Sign out" in auth)
check("staff sees staff workspace bar", "Staff workspace" in auth)
check("staff nav has management links", "Share links" in auth and "Dashboard" in auth)
check("staff footer drops customer WhatsApp", "Chat on WhatsApp" not in auth)
check("staff sees Edit product button", "✎ Edit product" in auth)

grid = client.get("/search").get_data(as_text=True)
check("staff cards expose Edit", "✎ Edit" in grid)
check("staff cards expose Share", "↗ Share" in grid)
check("staff cards expose ZIP", "⬇ ZIP" in grid)

preview_toggle = client.get("/preview?next=/", follow_redirects=True).get_data(as_text=True)
check("preview hides staff chrome", "Staff workspace" not in preview_toggle)
check("preview shows customer header", "Staff login" in preview_toggle)
check("preview banner is visible", "Previewing as a customer" in preview_toggle)
check("preview hides card actions", "⬇ ZIP" not in preview_toggle)
client.get("/preview?next=/", follow_redirects=True)
back = client.get("/search").get_data(as_text=True)
check("leaving preview restores staff mode", "Staff workspace" in back and "⬇ ZIP" in back)

anon_grid = app.test_client().get("/search").get_data(as_text=True)
check("public cards stay clean", "✎ Edit" not in anon_grid and "⬇ ZIP" not in anon_grid)

search = client.get("/search?q=hp").get_data(as_text=True)
check("search page has filters", 'name="sort"' in search and "Best match" in search)

print()
if failures:
    raise SystemExit(f"{len(failures)} check(s) failed")
print("Staff/public UI checks passed.")
