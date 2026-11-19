#!/usr/bin/env python
"""Attach product images from a list of URLs.

Use this when you already have image links you are allowed to use — your own
photos on cloud storage, a distributor's asset links, or manufacturer partner
library URLs.

Input is a CSV with a header row:

    sku,url
    LAPT-00215,https://example.com/hp-zbook-14-g7-front.jpg
    LAPT-00215,https://example.com/hp-zbook-14-g7-side.jpg
    VAR-8-R8-S256-PI5,https://example.com/dell-3390.jpg

The SKU may be any product's SKU or its slug. The URL column may also be a
local file path. Every image goes through the normal pipeline (EXIF stripped,
400/800/1600 WebP + original, uploaded to R2) and duplicates are skipped by
checksum.

    python scripts/upload_from_urls.py images.csv --dry-run
    python scripts/upload_from_urls.py images.csv
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.media_service import add_image  # noqa: E402
from app.models import Product  # noqa: E402

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)


def build_lookup() -> dict[str, Product]:
    lookup: dict[str, Product] = {}
    for product in db.session.query(Product).all():
        if product.sku:
            lookup[product.sku.strip().upper()] = product
        if product.slug:
            lookup[product.slug.strip().lower()] = product
    return lookup


def fetch(source: str) -> bytes:
    if source.startswith(("http://", "https://")):
        request = urllib.request.Request(
            source, headers={"User-Agent": BROWSER_UA, "Accept": "image/*,*/*"}
        )
        return urllib.request.urlopen(request, timeout=40).read()
    return Path(source).read_bytes()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_file", help="CSV with columns: sku,url")
    parser.add_argument("--dry-run", action="store_true", help="Match and report only")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    path = Path(args.csv_file)
    if not path.is_file():
        print(f"CSV not found: {path}")
        return 2

    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = [row for row in csv.DictReader(handle)]
    if args.limit:
        rows = rows[: args.limit]

    app = create_app()
    with app.app_context():
        lookup = build_lookup()
        uploaded = skipped = failed = unmatched = 0

        for index, row in enumerate(rows, start=1):
            sku = (row.get("sku") or "").strip()
            source = (row.get("url") or "").strip()
            if not sku or not source:
                continue

            product = lookup.get(sku.upper()) or lookup.get(sku.lower())
            if product is None:
                print(f"[{index}] NO MATCH  {sku}")
                unmatched += 1
                continue

            try:
                data = fetch(source)
            except urllib.error.HTTPError as exc:
                print(f"[{index}] FAIL {exc.code}  {sku}  {source[:70]}")
                failed += 1
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"[{index}] FAIL {type(exc).__name__}  {sku}  {source[:70]}")
                failed += 1
                continue

            digest = hashlib.sha256(data).hexdigest()
            if digest in {m.checksum for m in product.media if m.checksum}:
                skipped += 1
                print(f"[{index}] skip  {sku}  already on the product")
                continue

            if args.dry_run:
                uploaded += 1
                print(f"[{index}] would {sku}  {len(data) // 1024} KB  {source[:60]}")
                continue

            try:
                add_image(product, data, alt=Path(source.split("?")[0]).stem)
                uploaded += 1
                print(f"[{index}] up    {sku}  {len(data) // 1024} KB")
            except Exception as exc:  # noqa: BLE001
                print(f"[{index}] FAIL {sku}: {exc}")
                failed += 1

        print(
            f"\nrows={len(rows)} uploaded={uploaded} skipped={skipped} "
            f"unmatched={unmatched} failed={failed}"
        )
        if args.dry_run:
            print("Dry run — nothing was uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
