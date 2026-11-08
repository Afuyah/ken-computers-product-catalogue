"""Database models for the catalogue.

The shape is deliberately small: one row per sellable SKU, media kept in a
separate table so a product can have any number of images/clips, and share
links so a curated selection can be sent to a customer.
"""

from __future__ import annotations

from datetime import datetime

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from .extensions import db
from .utils import short_id, utcnow

STOCK_LABELS = {
    "in_stock": "In stock",
    "low_stock": "A few pieces left",
    "out_of_stock": "Out of stock",
}

STOCK_BADGE_CLASSES = {
    "in_stock": "bg-emerald-50 text-emerald-700 ring-emerald-600/20",
    "low_stock": "bg-amber-50 text-amber-800 ring-amber-600/20",
    "out_of_stock": "bg-slate-100 text-slate-500 ring-slate-500/20",
}

CONDITIONS = ("New", "Refurbished", "Used")

MEDIA_KINDS = ("image", "video")


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    # A simple username, not an email. The underlying column keeps its original
    # name so existing databases need no migration.
    username = db.Column("email", db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="staff")  # owner | staff
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True))

    # --- auth --------------------------------------------------------------
    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"

    @property
    def display_name(self) -> str:
        return self.name or self.username

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username}>"


class Category(db.Model):
    __tablename__ = "categories"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(120), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.String(400), default="")
    parent_id = db.Column(db.Integer, db.ForeignKey("categories.id", ondelete="SET NULL"))
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    parent = db.relationship("Category", remote_side=[id], back_populates="children")
    children = db.relationship(
        "Category", back_populates="parent", order_by="Category.sort_order, Category.name"
    )
    products = db.relationship("Product", back_populates="category")

    @property
    def is_top_level(self) -> bool:
        return self.parent_id is None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Category {self.slug}>"


class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(
        db.String(16), unique=True, nullable=False, index=True, default=lambda: short_id(5)
    )
    slug = db.Column(db.String(180), unique=True, nullable=False, index=True)
    sku = db.Column(db.String(60), unique=True, nullable=False, index=True)

    title = db.Column(db.String(200), nullable=False)
    brand = db.Column(db.String(80), nullable=False, default="", index=True)
    category_id = db.Column(
        db.Integer, db.ForeignKey("categories.id", ondelete="SET NULL"), index=True
    )
    condition = db.Column(db.String(20), nullable=False, default="New")
    summary = db.Column(db.String(300), default="")
    description = db.Column(db.Text, default="")
    specs = db.Column(db.JSON, nullable=False, default=dict)

    price_kes = db.Column(db.Integer, nullable=False, default=0)
    price_was_kes = db.Column(db.Integer)
    stock_status = db.Column(db.String(20), nullable=False, default="in_stock", index=True)
    stock_note = db.Column(db.String(200), default="")

    is_featured = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_published = db.Column(db.Boolean, nullable=False, default=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    category = db.relationship("Category", back_populates="products")
    media = db.relationship(
        "Media",
        back_populates="product",
        cascade="all, delete-orphan",
        order_by="Media.sort_order, Media.id",
        lazy="selectin",
    )

    # --- presentation ------------------------------------------------------
    @property
    def stock_label(self) -> str:
        return STOCK_LABELS.get(self.stock_status, "In stock")

    @property
    def stock_badge_class(self) -> str:
        return STOCK_BADGE_CLASSES.get(
            self.stock_status, STOCK_BADGE_CLASSES["in_stock"]
        )

    @property
    def in_stock(self) -> bool:
        return self.stock_status != "out_of_stock"

    @property
    def discount_percent(self) -> int:
        if self.price_was_kes and self.price_was_kes > self.price_kes > 0:
            return round((1 - self.price_kes / self.price_was_kes) * 100)
        return 0

    @property
    def images(self) -> list["Media"]:
        return [m for m in self.media if m.kind == "image"]

    @property
    def videos(self) -> list["Media"]:
        return [m for m in self.media if m.kind == "video"]

    @property
    def primary_image(self) -> "Media | None":
        images = self.images
        return images[0] if images else None

    @property
    def specs_list(self) -> list[tuple[str, str]]:
        if not isinstance(self.specs, dict):
            return []
        return [(str(k), str(v)) for k, v in self.specs.items() if v not in (None, "")]

    @property
    def specs_text(self) -> str:
        return "\n".join(f"{key}: {value}" for key, value in self.specs_list)

    @property
    def availability_schema(self) -> str:
        return {
            "in_stock": "https://schema.org/InStock",
            "low_stock": "https://schema.org/LimitedAvailability",
            "out_of_stock": "https://schema.org/OutOfStock",
        }.get(self.stock_status, "https://schema.org/InStock")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Product {self.sku}>"


class ProductSlug(db.Model):
    """Old slugs, kept so links sent to customers never 404."""

    __tablename__ = "product_slugs"

    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(180), unique=True, nullable=False, index=True)
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False
    )
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)


class Media(db.Model):
    __tablename__ = "media"

    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(
        db.String(16), unique=True, nullable=False, index=True, default=lambda: short_id(5)
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = db.Column(db.String(10), nullable=False, default="image")
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    duration_s = db.Column(db.Float)
    bytes = db.Column(db.Integer)
    checksum = db.Column(db.String(64), index=True)
    alt = db.Column(db.String(250), default="")
    lqip = db.Column(db.Text, default="")  # base64 data URI placeholder
    status = db.Column(db.String(20), nullable=False, default="ready")

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    product = db.relationship("Product", back_populates="media")
    files = db.relationship(
        "MediaFile", back_populates="media", cascade="all, delete-orphan", lazy="selectin"
    )

    def file(self, label: str) -> "MediaFile | None":
        for f in self.files:
            if f.label == label:
                return f
        return None

    @property
    def thumb(self) -> "MediaFile | None":
        return self.file("thumb") or self.file("card")

    @property
    def card(self) -> "MediaFile | None":
        return self.file("card") or self.file("thumb")

    @property
    def full(self) -> "MediaFile | None":
        return self.file("full") or self.file("card")

    @property
    def video_file(self) -> "MediaFile | None":
        return self.file("video")

    @property
    def poster(self) -> "MediaFile | None":
        return self.file("poster")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Media {self.public_id} {self.kind}>"


class MediaFile(db.Model):
    __tablename__ = "media_files"

    id = db.Column(db.Integer, primary_key=True)
    media_id = db.Column(
        db.Integer, db.ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label = db.Column(db.String(20), nullable=False)  # thumb|card|full|original|poster|video
    format = db.Column(db.String(10), nullable=False)  # webp|jpg|png|mp4
    width = db.Column(db.Integer)
    height = db.Column(db.Integer)
    bytes = db.Column(db.Integer)
    storage_key = db.Column(db.String(400), nullable=False)

    media = db.relationship("Media", back_populates="files")

    @property
    def url(self) -> str:
        from .storage import get_storage

        return get_storage().url(self.storage_key)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<MediaFile {self.label} {self.format}>"


class ShareLink(db.Model):
    __tablename__ = "share_links"

    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(32), unique=True, nullable=False, index=True)
    title = db.Column(db.String(160), nullable=False, default="Selection")
    note = db.Column(db.Text, default="")
    expires_at = db.Column(db.DateTime(timezone=True))
    pin_hash = db.Column(db.String(255))
    revoked_at = db.Column(db.DateTime(timezone=True))
    show_prices = db.Column(db.Boolean, nullable=False, default=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)

    items = db.relationship(
        "ShareItem",
        back_populates="share_link",
        cascade="all, delete-orphan",
        order_by="ShareItem.sort_order",
        lazy="selectin",
    )

    @property
    def is_live(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is not None and self.expires_at <= utcnow():
            return False
        return True

    @property
    def requires_pin(self) -> bool:
        return bool(self.pin_hash)

    def check_pin(self, pin: str) -> bool:
        return bool(self.pin_hash) and check_password_hash(self.pin_hash, pin or "")

    def products(self) -> list[Product]:
        return [item.product for item in self.items if item.product is not None]


class ShareItem(db.Model):
    __tablename__ = "share_items"

    share_link_id = db.Column(
        db.Integer, db.ForeignKey("share_links.id", ondelete="CASCADE"), primary_key=True
    )
    product_id = db.Column(
        db.Integer, db.ForeignKey("products.id", ondelete="CASCADE"), primary_key=True
    )
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    share_link = db.relationship("ShareLink", back_populates="items")
    product = db.relationship("Product")


class DownloadEvent(db.Model):
    __tablename__ = "download_events"

    id = db.Column(db.Integer, primary_key=True)
    media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="SET NULL"))
    product_id = db.Column(db.Integer, db.ForeignKey("products.id", ondelete="SET NULL"), index=True)
    share_link_id = db.Column(db.Integer, db.ForeignKey("share_links.id", ondelete="SET NULL"))
    ip_hash = db.Column(db.String(64))
    user_agent = db.Column(db.String(300))
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"))
    action = db.Column(db.String(40), nullable=False)
    entity_type = db.Column(db.String(40), nullable=False)
    entity_id = db.Column(db.String(40))
    detail = db.Column(db.JSON, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow, index=True)


BANNER_THEMES = {
    "blue": "from-blue-600 via-blue-700 to-slate-900",
    "emerald": "from-emerald-500 via-emerald-700 to-slate-900",
    "slate": "from-slate-600 via-slate-800 to-black",
    "amber": "from-amber-400 via-orange-600 to-slate-900",
    "rose": "from-rose-500 via-rose-700 to-slate-900",
}


class Banner(db.Model):
    """An advertising slide for the home page hero."""

    __tablename__ = "banners"

    id = db.Column(db.Integer, primary_key=True)
    eyebrow = db.Column(db.String(60), nullable=False, default="")
    headline = db.Column(db.String(140), nullable=False)
    subtext = db.Column(db.String(260), nullable=False, default="")
    cta_label = db.Column(db.String(40), nullable=False, default="")
    cta_url = db.Column(db.String(300), nullable=False, default="")
    secondary_label = db.Column(db.String(40), nullable=False, default="")
    secondary_url = db.Column(db.String(300), nullable=False, default="")

    theme = db.Column(db.String(20), nullable=False, default="blue")
    align = db.Column(db.String(10), nullable=False, default="left")

    image_key = db.Column(db.String(400), nullable=False, default="")
    image_key_sm = db.Column(db.String(400), nullable=False, default="")
    image_alt = db.Column(db.String(200), nullable=False, default="")
    lqip = db.Column(db.Text, default="")

    sort_order = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True, index=True)
    starts_at = db.Column(db.Date)
    ends_at = db.Column(db.Date)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    @property
    def gradient(self) -> str:
        return BANNER_THEMES.get(self.theme, BANNER_THEMES["blue"])

    @property
    def image_url(self) -> str:
        if not self.image_key:
            return ""
        from .storage import get_storage

        return get_storage().url(self.image_key)

    @property
    def image_small_url(self) -> str:
        if not self.image_key_sm:
            return self.image_url
        from .storage import get_storage

        return get_storage().url(self.image_key_sm)

    @property
    def is_centered(self) -> bool:
        return self.align == "center"

    def is_live(self, today=None) -> bool:
        from datetime import date

        today = today or date.today()
        if not self.is_active:
            return False
        if self.starts_at and self.starts_at > today:
            return False
        if self.ends_at and self.ends_at < today:
            return False
        return True

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Banner {self.headline!r}>"
