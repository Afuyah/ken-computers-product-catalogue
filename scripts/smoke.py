#!/usr/bin/env python
"""In-process smoke test: renders the public and admin routes.

Run after `scripts/setup_local.py`:

    .venv\\Scripts\\python.exe scripts\\smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import Category, Product, ShareLink  # noqa: E402

OWNER_USERNAME = os.getenv("SMOKE_USERNAME", "admin")
OWNER_PASSWORD = os.getenv("SMOKE_PASSWORD", "ChangeMe123!")

app = create_app()
app.config["WTF_CSRF_ENABLED"] = False

failures: list[str] = []

with app.app_context():
    product = db.session.query(Product).filter_by(is_published=True).first()
    category = db.session.query(Category).filter_by(parent_id=None).first()
    link = db.session.query(ShareLink).first()
    if link is None:
        link = ShareLink(token="smoke-token-123", title="Smoke selection")
        db.session.add(link)
        db.session.commit()

    # Copy everything out before the session closes.
    product_slug = product.slug if product else None
    product_id = product.id if product else None
    has_images = bool(product.images) if product else False
    category_slug = category.slug if category else None
    share_token = link.token

client = app.test_client()

public_checks = [
    ("/", 200),
    ("/search", 200),
    ("/search?q=hp", 200),
    ("/robots.txt", 200),
    ("/sitemap.xml", 200),
    ("/admin/login", 200),
    ("/this-does-not-exist", 404),
    (f"/s/{share_token}", 200),
]
if product_slug:
    public_checks.append((f"/p/{product_slug}", 200))
    # Downloads are staff-only now: anonymous visitors get redirected to login.
    public_checks.append((f"/p/{product_slug}/download", 302))
if category_slug:
    public_checks.append((f"/c/{category_slug}", 200))

print("== public ==")
for url, expected in public_checks:
    response = client.get(url)
    ok = response.status_code == expected
    print(f"{'PASS' if ok else 'FAIL'}  {response.status_code}  {url}")
    if not ok:
        failures.append(f"{url} -> {response.status_code} (expected {expected})")

home = client.get("/").get_data(as_text=True)
if "Ken Computers" not in home:
    failures.append("home page missing the brand name")
if "Shop by brand" not in home and "Shop by category" not in home:
    failures.append("home page missing the category section")

print("\n== admin ==")
login = client.post(
    "/admin/login",
    data={"username": OWNER_USERNAME, "password": OWNER_PASSWORD},
    follow_redirects=True,
)
print(f"{'PASS' if login.status_code == 200 else 'FAIL'}  {login.status_code}  POST /admin/login")
if login.status_code != 200:
    failures.append(f"login -> {login.status_code}")

admin_checks = [
    "/admin/",
    "/admin/products",
    "/admin/products/new",
    "/admin/categories",
    "/admin/banners",
    "/admin/share-links",
    "/admin/users",
]
if product_id:
    admin_checks.append(f"/admin/products/{product_id}/edit")

for url in admin_checks:
    response = client.get(url)
    ok = response.status_code == 200
    print(f"{'PASS' if ok else 'FAIL'}  {response.status_code}  {url}")
    if not ok:
        failures.append(f"{url} -> {response.status_code}")

print()
if failures:
    print("FAILURES:")
    for failure in failures:
        print(" -", failure)
    raise SystemExit(1)

print(f"Smoke test passed ({len(public_checks) + len(admin_checks)} routes).")
