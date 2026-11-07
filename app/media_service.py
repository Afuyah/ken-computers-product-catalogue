"""Upload pipeline: bytes in, CDN-ready variants + database rows out."""

from __future__ import annotations

from flask import current_app

from .extensions import db
from .imaging import VideoToolingMissing, build_image_variants, transcode_video
from .models import Media, MediaFile, Product
from .storage import get_storage

CONTENT_TYPES = {
    "webp": "image/webp",
    "jpg": "image/jpeg",
    "png": "image/png",
    "mp4": "video/mp4",
}


def _key(product: Product, media: Media, label: str, fmt: str) -> str:
    return f"p/{product.public_id}/{media.public_id}/{label}.{fmt}"


def _persist(product: Product, media: Media, variants: list[dict]) -> None:
    storage = get_storage()
    for variant in variants:
        key = _key(product, media, variant["label"], variant["format"])
        storage.put(key, variant["data"], CONTENT_TYPES.get(variant["format"], "application/octet-stream"))
        media.files.append(
            MediaFile(
                label=variant["label"],
                format=variant["format"],
                width=variant.get("width"),
                height=variant.get("height"),
                bytes=len(variant["data"]),
                storage_key=key,
            )
        )


def next_sort_order(product: Product) -> int:
    return max((m.sort_order for m in product.media), default=-1) + 1


def add_image(product: Product, data: bytes, alt: str = "") -> Media:
    cfg = current_app.config
    meta, variants = build_image_variants(
        data, widths=cfg["IMAGE_WIDTHS"], quality=cfg["IMAGE_QUALITY"]
    )
    media = Media(
        product=product,
        kind="image",
        sort_order=next_sort_order(product),
        alt=alt[:250],
        width=meta["width"],
        height=meta["height"],
        bytes=sum(len(v["data"]) for v in variants),
        checksum=meta["checksum"],
        lqip=meta["lqip"],
    )
    db.session.add(media)
    db.session.flush()  # assigns media.id and media.public_id
    _persist(product, media, variants)
    db.session.commit()
    return media


def add_video(product: Product, data: bytes, alt: str = "") -> Media:
    meta, variants = transcode_video(data)
    media = Media(
        product=product,
        kind="video",
        sort_order=next_sort_order(product),
        alt=alt[:250],
        width=meta["width"],
        height=meta["height"],
        duration_s=meta["duration_s"],
        bytes=sum(len(v["data"]) for v in variants),
        checksum=meta["checksum"],
        lqip=meta["lqip"],
    )
    db.session.add(media)
    db.session.flush()
    _persist(product, media, variants)
    db.session.commit()
    return media


def delete_media(media: Media) -> None:
    storage = get_storage()
    for file in list(media.files):
        storage.delete(file.storage_key)
    db.session.delete(media)
    db.session.commit()


def delete_product_media(product: Product) -> None:
    storage = get_storage()
    for media in list(product.media):
        for file in media.files:
            storage.delete(file.storage_key)


__all__ = [
    "add_image",
    "add_video",
    "delete_media",
    "delete_product_media",
    "VideoToolingMissing",
]
