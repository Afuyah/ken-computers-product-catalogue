#!/usr/bin/env python
"""Re-attach existing R2 objects to products after media rows are lost.

Bucket keys look like ``p/<product-public-id>/<media-public-id>/<label>.<ext>``.
If the database rows disappear (for example because the catalogue was
re-imported), the objects stay in the bucket but nothing points at them. This
script rebuilds the Media/MediaFile rows from the keys themselves, so no image
is re-uploaded and no bytes change.

You only need to say which bucket prefix belongs to which product:

    python scripts/relink_media.py --map 1f19c1ec30=LAPT-00262 --map e2f4887bfd=LAPT-00264
    python scripts/relink_media.py --map ... --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from PIL import Image  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.imaging import lqip_data_uri  # noqa: E402
from app.models import Media, MediaFile, Product  # noqa: E402
from app.storage import get_storage  # noqa: E402

LABELS = ("thumb", "card", "full", "original")


def keys_for(storage, bucket: str, prefix: str) -> dict[str, dict[str, str]]:
    response = storage.client.list_objects_v2(Bucket=bucket, Prefix=f"p/{prefix}/")
    grouped: dict[str, dict[str, str]] = {}
    for item in response.get("Contents", []):
        parts = item["Key"].split("/")
        if len(parts) != 4:
            continue
        _, _product, media_id, filename = parts
        label, _, ext = filename.rpartition(".")
        grouped.setdefault(media_id, {})[label] = filename
    return grouped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", action="append", default=[], metavar="PREFIX=SKU",
                        help="Bucket product prefix -> product SKU (repeatable)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not args.map:
        print("Nothing to do. Pass at least one --map PREFIX=SKU")
        return 2

    app = create_app()
    with app.app_context():
        storage = get_storage()
        bucket = app.config["R2_BUCKET"]
        created = skipped = 0

        for mapping in args.map:
            if "=" not in mapping:
                print(f"Bad mapping: {mapping}")
                continue
            prefix, sku = mapping.split("=", 1)
            product = db.session.query(Product).filter_by(sku=sku.strip()).first()
            if product is None:
                print(f"NO PRODUCT  {sku}")
                continue

            grouped = keys_for(storage, bucket, prefix.strip())
            if not grouped:
                print(f"NO OBJECTS  p/{prefix.strip()}/")
                continue

            print(f"\n{prefix} -> {product.sku} {product.title[:50]} ({len(grouped)} photo(s))")
            next_order = max((m.sort_order for m in product.media), default=-1) + 1

            for index, (media_id, files) in enumerate(sorted(grouped.items())):
                if db.session.query(Media).filter_by(public_id=media_id).first():
                    skipped += 1
                    print(f"   skip {media_id} (already linked)")
                    continue

                metadata: dict[str, dict] = {}
                checksum = ""
                lqip = ""
                width = height = 0
                total = 0

                for label in LABELS:
                    relative = files.get(label)
                    if not relative:
                        continue
                    key = f"p/{prefix.strip()}/{media_id}/{relative}"
                    payload = storage.read(key)
                    total += len(payload)
                    with Image.open(io.BytesIO(payload)) as image:
                        metadata[label] = {
                            "key": key,
                            "format": "jpg" if label == "original" else "webp",
                            "width": image.width,
                            "height": image.height,
                            "bytes": len(payload),
                        }
                        if label == "card":
                            width, height = image.width, image.height
                            lqip = lqip_data_uri(image.convert("RGB"))
                        if label == "original":
                            checksum = hashlib.sha256(payload).hexdigest()

                if not metadata:
                    continue

                print(f"   link {media_id}: {', '.join(sorted(metadata))}")
                if args.dry_run:
                    created += 1
                    continue

                media = Media(
                    public_id=media_id,
                    product_id=product.id,
                    kind="image",
                    sort_order=next_order + index,
                    width=width or metadata.get("full", {}).get("width", 0),
                    height=height or metadata.get("full", {}).get("height", 0),
                    bytes=total,
                    checksum=checksum,
                    lqip=lqip,
                    alt=product.title[:250],
                )
                db.session.add(media)
                for label in LABELS:
                    info = metadata.get(label)
                    if not info:
                        continue
                    media.files.append(
                        MediaFile(
                            label=label,
                            format=info["format"],
                            width=info["width"],
                            height=info["height"],
                            bytes=info["bytes"],
                            storage_key=info["key"],
                        )
                    )
                created += 1

            db.session.commit()

        print(f"\nlinked={created} skipped={skipped}")
        if args.dry_run:
            print("Dry run — nothing was written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
