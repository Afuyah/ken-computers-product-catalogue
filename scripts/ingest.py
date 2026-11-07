#!/usr/bin/env python
"""Bulk-import the catalogue from a folder.

Layout expected::

    data/
      products.csv
      media/
        KCM-1001/
          01.jpg
          02.jpg
          clip.mp4

CSV columns (header row required, case-insensitive):
    sku, title, brand, category, price_kes, price_was_kes, stock_status,
    summary, description, specs, condition, featured, published

`specs` is a single cell: ``Processor=Intel i5; Memory=16 GB; Storage=512 GB``

Usage::

    python scripts/ingest.py ./data --dry-run
    python scripts/ingest.py ./data
    python scripts/ingest.py ./data --limit 50
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.media_service import VideoToolingMissing, add_image, add_video  # noqa: E402
from app.models import Category, Media, Product  # noqa: E402
from app.utils import make_slug  # noqa: E402

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}

STOCK_ALIASES = {
    "in stock": "in_stock",
    "instock": "in_stock",
    "in_stock": "in_stock",
    "available": "in_stock",
    "low": "low_stock",
    "few": "low_stock",
    "low stock": "low_stock",
    "low_stock": "low_stock",
    "out": "out_of_stock",
    "out of stock": "out_of_stock",
    "out_of_stock": "out_of_stock",
    "sold": "out_of_stock",
}


def truthy(value: str | None, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "yes", "y", "true", "t"}


def parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError, AttributeError):
        return default


def parse_specs(raw: str | None) -> dict[str, str]:
    specs: dict[str, str] = {}
    if not raw:
        return specs
    for chunk in raw.replace("\n", ";").split(";"):
        if not chunk.strip():
            continue
        separator = "=" if "=" in chunk else (":" if ":" in chunk else None)
        if separator is None:
            continue
        key, value = chunk.split(separator, 1)
        if key.strip() and value.strip():
            specs[key.strip()[:60]] = value.strip()[:300]
    return specs


def category_for(name: str) -> Category | None:
    name = (name or "").strip()
    if not name:
        return None
    slug = make_slug(name)
    category = db.session.query(Category).filter_by(slug=slug).first()
    if category is None:
        category = Category(name=name[:120], slug=slug)
        db.session.add(category)
        db.session.flush()
        print(f"  + category {name}")
    return category


def existing_checksums(product: Product) -> set[str]:
    return {m.checksum for m in product.media if m.checksum}


def import_row(row: dict[str, str], media_root: Path, dry_run: bool) -> tuple[bool, int]:
    data = {str(k).strip().lower(): (v or "").strip() for k, v in row.items() if k}

    sku = data.get("sku", "")
    title = data.get("title", "")
    if not sku or not title:
        print(f"  ! skipping row without sku/title: {row}")
        return False, 0

    product = db.session.query(Product).filter_by(sku=sku).first()
    created = product is None
    if created:
        if dry_run:
            print(f"  [dry] would create {sku} — {title}")
            return True, 0
        product = Product(sku=sku, title=title[:200], slug=make_slug(f"{title}-{sku}"))
        db.session.add(product)
        db.session.flush()

    product.title = title[:200]
    product.brand = data.get("brand", "")[:80]
    product.category_id = getattr(category_for(data.get("category", "")), "id", None)
    product.condition = (data.get("condition") or "New")[:20]
    product.summary = data.get("summary", "")[:300]
    product.description = data.get("description", "")
    product.specs = parse_specs(data.get("specs"))
    product.price_kes = parse_int(data.get("price_kes"))
    was = parse_int(data.get("price_was_kes"))
    product.price_was_kes = was if was > product.price_kes else None
    stock = STOCK_ALIASES.get(data.get("stock_status", "").lower(), "in_stock")
    product.stock_status = stock
    product.is_featured = truthy(data.get("featured"))
    product.is_published = truthy(data.get("published"), default=True)

    if dry_run:
        print(f"  [dry] would update {sku} — {title}")
        return True, 0

    db.session.commit()

    added = 0
    folder = media_root / sku
    if folder.is_dir():
        known = existing_checksums(product)
        for path in sorted(folder.iterdir()):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix not in IMAGE_EXT | VIDEO_EXT:
                continue
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            if digest in known:
                continue
            try:
                if suffix in VIDEO_EXT:
                    add_video(product, payload, alt=path.stem)
                else:
                    add_image(product, payload, alt=path.stem)
                known.add(digest)
                added += 1
            except VideoToolingMissing:
                print(f"  ! {path.name}: ffmpeg not installed, skipped video")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {path.name}: {exc}")

    print(f"  {'created' if created else 'updated'} {sku} — {title} ({added} new file(s))")
    return True, added


def main() -> int:
    parser = argparse.ArgumentParser(description="Import products and media into the catalogue.")
    parser.add_argument("folder", type=Path, help="Folder containing products.csv and media/")
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing.")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N rows.")
    args = parser.parse_args()

    csv_path = args.folder / "products.csv"
    if not csv_path.exists():
        print(f"Missing {csv_path}")
        return 1

    media_root = args.folder / "media"
    app = create_app()

    with app.app_context():
        with csv_path.open(newline="", encoding="utf-8-sig") as handle:
            rows = list(csv.DictReader(handle))
        if args.limit:
            rows = rows[: args.limit]

        print(f"Importing {len(rows)} row(s) from {csv_path}")
        ok = 0
        files = 0
        for index, row in enumerate(rows, start=1):
            print(f"[{index}/{len(rows)}]")
            try:
                success, added = import_row(row, media_root, args.dry_run)
            except Exception as exc:  # noqa: BLE001
                db.session.rollback()
                print(f"  ! failed: {exc}")
                continue
            ok += int(success)
            files += added

        print(f"\nDone. {ok} product(s), {files} media file(s).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
