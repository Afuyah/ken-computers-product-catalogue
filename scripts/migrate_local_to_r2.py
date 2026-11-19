#!/usr/bin/env python
"""Copy images from a running site's local /media storage into the R2 bucket.

Useful when uploads happened while STORAGE_BACKEND was still "local" (for
example on a Railway container): the database already holds the right storage
keys, but the bytes live on the container's disk. This pulls each file from the
public /media URL and re-uploads it to R2 under the same key, so nothing in the
database needs to change.

    python scripts/migrate_local_to_r2.py --base-url https://your-app.up.railway.app
    python scripts/migrate_local_to_r2.py --base-url ... --dry-run
"""

from __future__ import annotations

import argparse
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
from app.models import MediaFile  # noqa: E402
from app.storage import get_storage  # noqa: E402

CONTENT_TYPES = {"webp": "image/webp", "jpg": "image/jpeg", "png": "image/png", "mp4": "video/mp4"}
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="Live site, e.g. https://app.up.railway.app")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    app = create_app()

    with app.app_context():
        if app.config["STORAGE_BACKEND"] != "r2":
            print("STORAGE_BACKEND is not r2 — set it in .env first.")
            return 2

        storage = get_storage()
        files = db.session.query(MediaFile).all()
        print(f"{len(files)} file row(s) in the database\n")

        uploaded = skipped = missing = failed = 0
        for row in files:
            key = row.storage_key
            try:
                head = storage.client.head_object(Bucket=app.config["R2_BUCKET"], Key=key)
                if head.get("ContentLength"):
                    skipped += 1
                    continue
            except Exception:  # noqa: BLE001 - 404 means "not there yet"
                pass

            url = f"{base}/media/{key}"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
                data = urllib.request.urlopen(request, timeout=30).read()
            except urllib.error.HTTPError as exc:
                print(f"MISS  {exc.code}  {url}")
                missing += 1
                continue
            except Exception as exc:  # noqa: BLE001
                print(f"ERR   {type(exc).__name__}  {url}")
                failed += 1
                continue

            if args.dry_run:
                print(f"WOULD {len(data):>8}B  {key}")
                uploaded += 1
                continue

            content_type = CONTENT_TYPES.get(row.format, "application/octet-stream")
            try:
                storage.put(key, data, content_type)
                print(f"UP    {len(data):>8}B  {key}")
                uploaded += 1
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL  {type(exc).__name__}: {exc}")
                failed += 1

        print(f"\nuploaded={uploaded} already_there={skipped} missing_on_site={missing} failed={failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
