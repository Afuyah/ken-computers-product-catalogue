"""Home-page advertising banners: image processing and live selection."""

from __future__ import annotations

from datetime import date
from io import BytesIO

from PIL import Image, ImageOps
from sqlalchemy import or_, select

from .extensions import db
from .models import Banner
from .storage import get_storage

WIDE_WIDTH = 1600
SMALL_WIDTH = 800
QUALITY = 82


def _prepare(data: bytes) -> Image.Image:
    image = ImageOps.exif_transpose(Image.open(BytesIO(data)))
    if image.mode in ("RGBA", "LA", "P"):
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (15, 23, 42))
        background.paste(rgba, mask=rgba.split()[-1])
        image = background
    else:
        image = image.convert("RGB")
    if image.width > 2400:
        image.thumbnail((2400, 2400), Image.LANCZOS)
    return image


def _resize(image: Image.Image, width: int) -> Image.Image:
    if image.width <= width:
        return image.copy()
    height = max(1, round(image.height * width / image.width))
    return image.resize((width, height), Image.LANCZOS)


def _lqip(image: Image.Image) -> str:
    import base64

    tiny = image.copy()
    tiny.thumbnail((20, 20))
    buffer = BytesIO()
    tiny.save(buffer, "WEBP", quality=40, method=0)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode()


def save_banner_image(banner: Banner, data: bytes) -> None:
    """Replace the banner's background image with freshly optimised WebP files."""
    image = _prepare(data)
    storage = get_storage()

    delete_banner_image(banner, commit=False)

    wide = _resize(image, WIDE_WIDTH)
    small = _resize(image, SMALL_WIDTH)

    wide_key = f"banners/{banner.id}/wide.webp"
    small_key = f"banners/{banner.id}/small.webp"

    wide_buffer = BytesIO()
    wide.save(wide_buffer, "WEBP", quality=QUALITY, method=4)
    storage.put(wide_key, wide_buffer.getvalue(), "image/webp")

    small_buffer = BytesIO()
    small.save(small_buffer, "WEBP", quality=QUALITY, method=4)
    storage.put(small_key, small_buffer.getvalue(), "image/webp")

    banner.image_key = wide_key
    banner.image_key_sm = small_key
    banner.lqip = _lqip(image)


def delete_banner_image(banner: Banner, *, commit: bool = True) -> None:
    storage = get_storage()
    for key in (banner.image_key, banner.image_key_sm):
        if key:
            storage.delete(key)
    banner.image_key = ""
    banner.image_key_sm = ""
    banner.lqip = ""
    if commit:
        db.session.commit()


def live_banners(today: date | None = None) -> list[Banner]:
    """Active banners inside their schedule window, in display order."""
    today = today or date.today()
    stmt = (
        select(Banner)
        .where(
            Banner.is_active.is_(True),
            or_(Banner.starts_at.is_(None), Banner.starts_at <= today),
            or_(Banner.ends_at.is_(None), Banner.ends_at >= today),
        )
        .order_by(Banner.sort_order, Banner.id)
    )
    return list(db.session.scalars(stmt).all())
