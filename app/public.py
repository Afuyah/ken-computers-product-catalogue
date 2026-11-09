"""Public catalogue: browse, filter, search, product pages, SEO endpoints."""

from __future__ import annotations

from datetime import datetime

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from flask_login import current_user
from sqlalchemy import func, select

from .banners import live_banners
from .extensions import db
from .models import Category, Product, ProductSlug
from .search import build_query
from .storage import storage_is_local
from .utils import safe_next, truncate

bp = Blueprint("public", __name__)

SORTS = {
    "new": (Product.created_at.desc(),),
    "price_asc": (Product.price_kes.asc(),),
    "price_desc": (Product.price_kes.desc(),),
    "name": (Product.title.asc(),),
}


def _published() -> object:
    return Product.is_published.is_(True)


def _category_ids(category: Category) -> list[int]:
    ids = [category.id]
    ids.extend(child.id for child in category.children if child.is_active)
    return ids


def _apply_filters(stmt, *, category_ids=None):
    if category_ids:
        stmt = stmt.where(Product.category_id.in_(category_ids))
    brand = (request.args.get("brand") or "").strip()
    if brand:
        stmt = stmt.where(Product.brand == brand)
    stock = (request.args.get("stock") or "").strip()
    if stock in ("in_stock", "low_stock", "out_of_stock"):
        stmt = stmt.where(Product.stock_status == stock)
    try:
        min_price = int(request.args.get("min_price") or 0)
    except ValueError:
        min_price = 0
    try:
        max_price = int(request.args.get("max_price") or 0)
    except ValueError:
        max_price = 0
    if min_price:
        stmt = stmt.where(Product.price_kes >= min_price)
    if max_price:
        stmt = stmt.where(Product.price_kes <= max_price)
    return stmt


def _base_query():
    return select(Product).where(_published())


@bp.get("/")
def home():
    featured = db.session.scalars(
        _base_query()
        .where(Product.is_featured.is_(True))
        .order_by(Product.updated_at.desc())
        .limit(8)
    ).all()
    latest = db.session.scalars(
        _base_query().order_by(Product.created_at.desc()).limit(8)
    ).all()
    categories = db.session.scalars(
        select(Category)
        .where(Category.parent_id.is_(None), Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name)
    ).all()
    counts = dict(
        db.session.execute(
            select(Product.category_id, func.count(Product.id))
            .where(_published())
            .group_by(Product.category_id)
        ).all()
    )
    # Roll child counts up so a parent category shows its true total.
    for category in categories:
        if category.children:
            counts[category.id] = counts.get(category.id, 0) + sum(
                counts.get(child.id, 0) for child in category.children
            )

    # A single top-level category with children (Laptops > HP, Dell…) reads
    # better as a brand grid on the home page.
    brand_view = len(categories) == 1 and bool(categories[0].children)
    display_categories = (
        [child for child in categories[0].children if child.is_active]
        if brand_view
        else categories
    )

    total = db.session.scalar(select(func.count(Product.id)).where(_published())) or 0
    return render_template(
        "public/home.html",
        featured=featured,
        latest=latest,
        categories=display_categories,
        brand_view=brand_view,
        counts=counts,
        total=total,
        banners=live_banners(),
    )


@bp.get("/c/<slug>")
def category(slug: str):
    category = db.session.scalar(
        select(Category).where(Category.slug == slug, Category.is_active.is_(True))
    )
    if category is None:
        abort(404)

    ids = _category_ids(category)
    stmt = _apply_filters(_base_query(), category_ids=ids)
    sort = request.args.get("sort", "new")
    stmt = stmt.order_by(*SORTS.get(sort, SORTS["new"]))
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(
        stmt, page=page, per_page=current_app.config["PRODUCTS_PER_PAGE"], error_out=False
    )

    brands = db.session.scalars(
        select(Product.brand)
        .where(_published(), Product.category_id.in_(ids), Product.brand != "")
        .distinct()
        .order_by(Product.brand)
    ).all()

    return render_template(
        "public/category.html",
        category=category,
        pagination=pagination,
        products=pagination.items,
        brands=brands,
        sort=sort,
    )


@bp.get("/p/<slug>")
def product(slug: str):
    product = db.session.scalar(select(Product).where(Product.slug == slug))
    if product is None or not product.is_published:
        old = db.session.scalar(select(ProductSlug).where(ProductSlug.slug == slug))
        if old is not None and (old_product := db.session.get(Product, old.product_id)):
            return redirect(url_for("public.product", slug=old_product.slug), code=301)
        abort(404)

    related = []
    if product.category_id:
        related = db.session.scalars(
            _base_query()
            .where(Product.category_id == product.category_id, Product.id != product.id)
            .order_by(Product.updated_at.desc())
            .limit(4)
        ).all()

    return render_template("public/product.html", product=product, related=related)


@bp.get("/search")
def search():
    q = (request.args.get("q") or "").strip()
    category_slug = (request.args.get("category") or "").strip()
    stock = (request.args.get("stock") or "").strip()
    sort = request.args.get("sort") or ("relevance" if q else "new")

    category_ids = None
    if category_slug:
        category = db.session.scalar(
            select(Category).where(Category.slug == category_slug)
        )
        if category is not None:
            category_ids = _category_ids(category)

    stmt = build_query(q, category_ids=category_ids, stock=stock, sort=sort)
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(
        stmt, page=page, per_page=current_app.config["PRODUCTS_PER_PAGE"], error_out=False
    )

    categories = db.session.scalars(
        select(Category)
        .where(Category.parent_id.is_(None), Category.is_active.is_(True))
        .order_by(Category.sort_order, Category.name)
    ).all()

    return render_template(
        "public/search.html",
        q=q,
        pagination=pagination,
        products=pagination.items,
        categories=categories,
        category_slug=category_slug,
        stock=stock,
        sort=sort,
    )


@bp.get("/healthz")
def healthz():
    db.session.execute(select(1))
    return {"status": "ok"}


@bp.get("/preview")
def preview():
    """Toggle the customer view for a signed-in staff member."""
    if not current_user.is_authenticated:
        abort(404)
    session["staff_preview"] = not session.get("staff_preview", False)
    return redirect(safe_next(request.args.get("next"), url_for("public.home")))


# ---------------------------------------------------------------------------
# Local media serving (development only; production uses the CDN)
# ---------------------------------------------------------------------------


@bp.get("/media/<path:filename>")
def serve_media(filename: str):
    if not storage_is_local():
        abort(404)
    download = request.args.get("download")
    return send_from_directory(
        current_app.config["MEDIA_ROOT"],
        filename,
        conditional=True,
        max_age=3600,
        as_attachment=bool(download),
        download_name=download or None,
    )


# ---------------------------------------------------------------------------
# SEO
# ---------------------------------------------------------------------------


@bp.get("/robots.txt")
def robots():
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /s/",
        f"Sitemap: {url_for('public.sitemap', _external=True)}",
    ]
    return Response("\n".join(lines), mimetype="text/plain")


@bp.get("/sitemap.xml")
def sitemap():
    products = db.session.scalars(
        _base_query().order_by(Product.updated_at.desc())
    ).all()
    categories = db.session.scalars(
        select(Category).where(Category.is_active.is_(True))
    ).all()

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for url in [url_for("public.home", _external=True)]:
        parts.append(f"<url><loc>{url}</loc><changefreq>daily</changefreq></url>")
    for category in categories:
        parts.append(
            "<url><loc>{}</loc><changefreq>weekly</changefreq></url>".format(
                url_for("public.category", slug=category.slug, _external=True)
            )
        )
    for product in products:
        parts.append(
            "<url><loc>{}</loc><lastmod>{}</lastmod></url>".format(
                url_for("public.product", slug=product.slug, _external=True),
                product.updated_at.strftime("%Y-%m-%d"),
            )
        )
    parts.append("</urlset>")
    return Response("".join(parts), mimetype="application/xml")


# ---------------------------------------------------------------------------
# Context helpers
# ---------------------------------------------------------------------------


@bp.app_context_processor
def inject_site():
    cfg = current_app.config
    try:
        categories = db.session.scalars(
            select(Category)
            .where(Category.parent_id.is_(None), Category.is_active.is_(True))
            .order_by(Category.sort_order, Category.name)
        ).all()
    except Exception:  # noqa: BLE001 - never let navigation break error pages
        db.session.rollback()
        current_app.logger.exception("Could not load navigation categories")
        categories = []
    return {
        "nav_categories": categories,
        "site_name": cfg["SITE_NAME"],
        "site_url": cfg["SITE_URL"],
        "contact_whatsapp": cfg["CONTACT_WHATSAPP"],
        "contact_phone": cfg["CONTACT_PHONE"],
        "contact_email": cfg["CONTACT_EMAIL"],
        "contact_location": cfg["CONTACT_LOCATION"],
        "cdn_base_url": cfg["CDN_BASE_URL"],
        "staff_mode": current_user.is_authenticated
        and not session.get("staff_preview", False),
        "meta_description": truncate(
            f"{cfg['SITE_NAME']} — laptops, printers and accessories in "
            f"{cfg['CONTACT_LOCATION']}. Genuine stock, clear prices.",
            160,
        ),
        "current_year": datetime.now().year,
    }
