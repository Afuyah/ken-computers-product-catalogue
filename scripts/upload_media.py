#!/usr/bin/env python
"""Bulk-attach product images (and short clips) from a folder tree.

Drop files into folders named after the product SKU or slug::

    images/
      LAPT-00215/01.jpg
      LAPT-00215/02.jpg
      VAR-8-R8-S256-PI5/01.jpg
      hp440-g7-core-i7-8gb-ram-256gb-ssd/01.jpg

Then::

    python scripts/upload_media.py --dry-run
    python scripts/upload_media.py

Each file runs through the normal pipeline (EXIF stripped, 400/800/1600 WebP +
clean original, uploaded to R2). Files whose checksum already exists on the
product are skipped, so re-running is safe.
"""

from __future__ import annotations

import argparse
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
from app.models import Product  # noqa: E402

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v"}


def build_lookup() -> dict[str, Product]:
    lookup: dict[str, Product] = {}
    for product in db.session.query(Product).all():
        if product.sku:
            lookup[product.sku.strip().upper()] = product
        if product.slug:
            lookup[product.slug.strip().lower()] = product
    return lookup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", default="images", help="Folder of <SKU>/<files> (default: images)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Only process the first N folders")
    args = parser.parse_args()

    root = Path(args.folder)
    if not root.is_dir():
        print(f"Folder not found: {root.resolve()}")
        return 2

    folders = sorted([p for p in root.iterdir() if p.is_dir()])
    if args.limit:
        folders = folders[: args.limit]
    if not folders:
        print(f"No sub-folders in {root.resolve()}")
        return 2

    app = create_app()
    with app.app_context():
        lookup = build_lookup()
        matched = unmatched = uploaded = skipped = failed = 0

        for folder in folders:
            key = folder.name.strip()
            product = lookup.get(key.upper()) or lookup.get(key.lower())
            if product is None:
                print(f"NO MATCH  {folder.name}  (no product with that SKU or slug)")
                unmatched += 1
                continue

            matched += 1
            known = {m.checksum for m in product.media if m.checksum}
            files = sorted(f for f in folder.iterdir() if f.is_file())
            print(f"\n{product.sku:<20} {product.title[:52]:<52} {len(files)} file(s)")

            for file in files:
                suffix = file.suffix.lower()
                if suffix not in IMAGE_EXT | VIDEO_EXT:
                    continue
                data = file.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                if digest in known:
                    skipped += 1
                    print(f"   skip   {file.name} (already uploaded)")
                    continue
                if args.dry_run:
                    uploaded += 1
                    print(f"   would  {file.name} ({len(data) // 1024} KB)")
                    continue
                try:
                    if suffix in VIDEO_EXT:
                        add_video(product, data, alt=file.stem)
                    else:
                        add_image(product, data, alt=file.stem)
                    known.add(digest)
                    uploaded += 1
                    print(f"   up     {file.name} ({len(data) // 1024} KB)")
                except VideoToolingMissing as exc:
                    print(f"   video  {file.name}: {exc}")
                    failed += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"   FAIL   {file.name}: {exc}")
                    failed += 1

        print(
            f"\nfolders={len(folders)} matched={matched} unmatched={unmatched} "
            f"uploaded={uploaded} skipped={skipped} failed={failed}"
        )
        if args.dry_run:
            print("Dry run — nothing was uploaded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
