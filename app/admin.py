"""Admin back office: products, media, categories, users, share links."""

from __future__ import annotations

from datetime import timedelta
from functools import wraps

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func, or_, select
from werkzeug.security import generate_password_hash

from .extensions import db
from .media_service import (
    VideoToolingMissing,
    add_image,
    add_video,
    delete_media,
    delete_product_media,
)
from .models import (
    CONDITIONS,
    STOCK_LABELS,
    AuditLog,
    Category,
    Media,
    Product,
    ProductSlug,
    ShareItem,
    ShareLink,
    User,
)
from .utils import make_slug, safe_next, share_token, unique_slug, utcnow

bp = Blueprint("admin", __name__, url_prefix="/admin")


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


def owner_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if not current_user.is_owner:
            abort(403)
        return view(*args, **kwargs)

    return wrapped


def log(action: str, entity_type: str, entity_id=None, detail=None) -> None:
    db.session.add(
        AuditLog(
            user_id=current_user.id if current_user.is_authenticated else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else None,
            detail=detail or {},
        )
    )


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))

    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        user = db.session.scalar(select(User).where(User.email == email))
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=bool(request.form.get("remember")))
            user.last_login_at = utcnow()
            db.session.commit()
            return redirect(safe_next(request.args.get("next")))
        flash("Wrong email or password.", "error")

    return render_template("admin/login.html")


@bp.post("/logout")
@login_required
def logout():
    logout_user()
    flash("Signed out.", "info")
    return redirect(url_for("admin.login"))


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@bp.get("/")
@login_required
def dashboard():
    stats = {
        "products": db.session.scalar(select(func.count(Product.id))) or 0,
        "published": db.session.scalar(
            select(func.count(Product.id)).where(Product.is_published.is_(True))
        )
        or 0,
        "out_of_stock": db.session.scalar(
            select(func.count(Product.id)).where(Product.stock_status == "out_of_stock")
        )
        or 0,
        "low_stock": db.session.scalar(
            select(func.count(Product.id)).where(Product.stock_status == "low_stock")
        )
        or 0,
        "media": db.session.scalar(select(func.count(Media.id))) or 0,
        "share_links": db.session.scalar(select(func.count(ShareLink.id))) or 0,
    }
    recent = db.session.scalars(
        select(Product).order_by(Product.updated_at.desc()).limit(8)
    ).all()
    return render_template("admin/dashboard.html", stats=stats, recent=recent)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------


@bp.get("/products")
@login_required
def products():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status") or ""
    stmt = select(Product)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Product.title.ilike(like), Product.sku.ilike(like), Product.brand.ilike(like))
        )
    if status == "published":
        stmt = stmt.where(Product.is_published.is_(True))
    elif status == "draft":
        stmt = stmt.where(Product.is_published.is_(False))
    elif status == "out_of_stock":
        stmt = stmt.where(Product.stock_status == "out_of_stock")
    stmt = stmt.order_by(Product.updated_at.desc())
    page = request.args.get("page", 1, type=int)
    pagination = db.paginate(stmt, page=page, per_page=40, error_out=False)
    return render_template(
        "admin/products.html", pagination=pagination, products=pagination.items, q=q, status=status
    )


def _parse_specs(raw: str) -> dict:
    specs: dict[str, str] = {}
    for line in (raw or "").splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            key, value = key.strip(), value.strip()
            if key and value:
                specs[key[:60]] = value[:300]
    return specs


def _form_int(name: str, default: int = 0) -> int:
    raw = (request.form.get(name) or "").replace(",", "").strip()
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _apply_product_form(product: Product) -> None:
    product.title = (request.form.get("title") or "").strip()[:200] or "Untitled"
    product.sku = (request.form.get("sku") or "").strip()[:60]
    product.brand = (request.form.get("brand") or "").strip()[:80]
    product.condition = request.form.get("condition") or "New"
    product.summary = (request.form.get("summary") or "").strip()[:300]
    product.description = (request.form.get("description") or "").strip()
    product.price_kes = max(0, _form_int("price_kes"))
    price_was = _form_int("price_was_kes")
    product.price_was_kes = price_was if price_was > product.price_kes else None
    product.stock_status = (
        request.form.get("stock_status")
        if request.form.get("stock_status") in STOCK_LABELS
        else "in_stock"
    )
    product.stock_note = (request.form.get("stock_note") or "").strip()[:200]
    product.is_featured = request.form.get("is_featured") == "on"
    product.is_published = request.form.get("is_published") == "on"
    product.specs = _parse_specs(request.form.get("specs") or "")

    category_id = request.form.get("category_id", type=int)
    product.category_id = category_id or None

    new_slug = make_slug(request.form.get("slug") or product.title)
    if new_slug != product.slug:
        exists = db.session.scalar(select(Product).where(Product.slug == new_slug))
        if exists is None or exists.id == product.id:
            if product.id is not None and product.slug:
                db.session.add(ProductSlug(slug=product.slug, product_id=product.id))
            product.slug = new_slug


@bp.route("/products/new", methods=["GET", "POST"])
@login_required
def product_new():
    product = Product(
        sku="",
        title="",
        slug="",
        condition="New",
        stock_status="in_stock",
        price_kes=0,
        is_published=True,
        is_featured=False,
        specs={},
    )
    if request.method == "POST":
        _apply_product_form(product)
        if not product.sku:
            flash("SKU is required.", "error")
        elif db.session.scalar(select(Product).where(Product.sku == product.sku)):
            flash("That SKU already exists.", "error")
        else:
            if not product.slug:
                product.slug = unique_slug(db.session, Product, product.title)
            db.session.add(product)
            log("create", "product", product.sku, {"title": product.title})
            db.session.commit()
            flash("Product created. Now add photos.", "success")
            return redirect(url_for("admin.product_edit", product_id=product.id))

    categories = db.session.scalars(select(Category).order_by(Category.name)).all()
    return render_template(
        "admin/product_form.html",
        product=product,
        categories=categories,
        conditions=CONDITIONS,
        stock_labels=STOCK_LABELS,
        is_new=True,
    )


@bp.route("/products/<int:product_id>/edit", methods=["GET", "POST"])
@login_required
def product_edit(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    if request.method == "POST":
        old_price = product.price_kes
        _apply_product_form(product)
        log(
            "update",
            "product",
            product.sku,
            {"price_from": old_price, "price_to": product.price_kes},
        )
        db.session.commit()
        flash("Changes saved.", "success")
        return redirect(url_for("admin.product_edit", product_id=product.id))

    categories = db.session.scalars(select(Category).order_by(Category.name)).all()
    return render_template(
        "admin/product_form.html",
        product=product,
        categories=categories,
        conditions=CONDITIONS,
        stock_labels=STOCK_LABELS,
        is_new=False,
    )


@bp.post("/products/<int:product_id>/delete")
@login_required
def product_delete(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)
    delete_product_media(product)
    log("delete", "product", product.sku, {"title": product.title})
    db.session.delete(product)
    db.session.commit()
    flash("Product deleted.", "success")
    return redirect(url_for("admin.products"))


@bp.post("/products/<int:product_id>/duplicate")
@login_required
def product_duplicate(product_id: int):
    source = db.session.get(Product, product_id)
    if source is None:
        abort(404)
    clone = Product(
        sku=f"{source.sku}-COPY",
        title=f"{source.title} (copy)",
        brand=source.brand,
        category_id=source.category_id,
        condition=source.condition,
        summary=source.summary,
        description=source.description,
        specs=dict(source.specs or {}),
        price_kes=source.price_kes,
        stock_status=source.stock_status,
        is_published=False,
    )
    clone.slug = unique_slug(db.session, Product, clone.title)
    db.session.add(clone)
    db.session.commit()
    flash("Duplicated as a draft. Add new photos and a SKU.", "success")
    return redirect(url_for("admin.product_edit", product_id=clone.id))


# ---------------------------------------------------------------------------
# Media
# ---------------------------------------------------------------------------


@bp.post("/products/<int:product_id>/media")
@login_required
def media_upload(product_id: int):
    product = db.session.get(Product, product_id)
    if product is None:
        abort(404)

    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        flash("Choose at least one file.", "error")
        return redirect(url_for("admin.product_edit", product_id=product.id))

    added = 0
    for file in files:
        data = file.read()
        if not data:
            continue
        mimetype = (file.mimetype or "").lower()
        try:
            if mimetype.startswith("video/") or file.filename.lower().endswith(
                (".mp4", ".mov", ".m4v", ".webm")
            ):
                add_video(product, data, alt=file.filename)
            else:
                add_image(product, data, alt=file.filename)
            added += 1
        except VideoToolingMissing as exc:
            flash(f"{file.filename}: {exc}", "error")
        except Exception as exc:  # noqa: BLE001 - surface the real reason to the admin
            current_app.logger.exception("Upload failed for %s", file.filename)
            flash(f"{file.filename} could not be processed: {exc}", "error")

    if added:
        log("upload", "product", product.sku, {"files": added})
        db.session.commit()
        flash(f"{added} file(s) uploaded and optimised.", "success")
    return redirect(url_for("admin.product_edit", product_id=product.id))


@bp.post("/products/<int:product_id>/media/<int:media_id>/delete")
@login_required
def media_delete(product_id: int, media_id: int):
    media = db.session.get(Media, media_id)
    if media is None or media.product_id != product_id:
        abort(404)
    delete_media(media)
    flash("Media removed.", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


@bp.post("/products/<int:product_id>/media/<int:media_id>/cover")
@login_required
def media_cover(product_id: int, media_id: int):
    media = db.session.get(Media, media_id)
    if media is None or media.product_id != product_id:
        abort(404)
    lowest = db.session.scalar(
        select(func.min(Media.sort_order)).where(Media.product_id == product_id)
    )
    media.sort_order = (lowest if lowest is not None else 0) - 1
    db.session.commit()
    flash("Cover image updated.", "success")
    return redirect(url_for("admin.product_edit", product_id=product_id))


@bp.post("/products/<int:product_id>/media/<int:media_id>/move")
@login_required
def media_move(product_id: int, media_id: int):
    media = db.session.get(Media, media_id)
    if media is None or media.product_id != product_id:
        abort(404)
    direction = request.form.get("direction")
    siblings = sorted(media.product.media, key=lambda m: (m.sort_order, m.id))
    index = siblings.index(media)
    swap_with = index - 1 if direction == "up" else index + 1
    if 0 <= swap_with < len(siblings):
        other = siblings[swap_with]
        media.sort_order, other.sort_order = other.sort_order, media.sort_order
        db.session.commit()
    return redirect(url_for("admin.product_edit", product_id=product_id))


# ---------------------------------------------------------------------------
# Categories
# ---------------------------------------------------------------------------


@bp.route("/categories", methods=["GET", "POST"])
@login_required
def categories():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        if not name:
            flash("Category name is required.", "error")
        else:
            slug = (request.form.get("slug") or "").strip() or make_slug(name)
            if db.session.scalar(select(Category).where(Category.slug == slug)):
                flash("That slug is already in use.", "error")
            else:
                category = Category(
                    name=name[:120],
                    slug=slug,
                    description=(request.form.get("description") or "").strip()[:400],
                    parent_id=request.form.get("parent_id", type=int) or None,
                    sort_order=request.form.get("sort_order", type=int) or 0,
                )
                db.session.add(category)
                log("create", "category", slug, {"name": name})
                db.session.commit()
                flash("Category created.", "success")
        return redirect(url_for("admin.categories"))

    all_categories = db.session.scalars(
        select(Category).order_by(Category.parent_id, Category.sort_order, Category.name)
    ).all()
    return render_template("admin/categories.html", categories=all_categories)


@bp.post("/categories/<int:category_id>/update")
@login_required
def category_update(category_id: int):
    category = db.session.get(Category, category_id)
    if category is None:
        abort(404)
    category.name = (request.form.get("name") or category.name).strip()[:120]
    category.description = (request.form.get("description") or "").strip()[:400]
    category.sort_order = request.form.get("sort_order", type=int) or category.sort_order
    category.is_active = request.form.get("is_active") == "on"
    parent_id = request.form.get("parent_id", type=int)
    category.parent_id = parent_id if parent_id != category.id else category.parent_id
    db.session.commit()
    flash("Category updated.", "success")
    return redirect(url_for("admin.categories"))


@bp.post("/categories/<int:category_id>/delete")
@login_required
def category_delete(category_id: int):
    category = db.session.get(Category, category_id)
    if category is None:
        abort(404)
    in_use = db.session.scalar(
        select(func.count(Product.id)).where(Product.category_id == category.id)
    )
    if in_use:
        flash("Move its products to another category first.", "error")
        return redirect(url_for("admin.categories"))
    log("delete", "category", category.slug)
    db.session.delete(category)
    db.session.commit()
    flash("Category deleted.", "success")
    return redirect(url_for("admin.categories"))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


@bp.get("/users")
@owner_required
def users():
    all_users = db.session.scalars(select(User).order_by(User.created_at)).all()
    return render_template("admin/users.html", users=all_users)


@bp.post("/users")
@owner_required
def user_create():
    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    if not email or len(password) < 8:
        flash("Email and a password of at least 8 characters are required.", "error")
        return redirect(url_for("admin.users"))
    if db.session.scalar(select(User).where(User.email == email)):
        flash("That email is already registered.", "error")
        return redirect(url_for("admin.users"))
    user = User(
        email=email,
        name=(request.form.get("name") or "").strip()[:120],
        role="staff",
        is_active=True,
    )
    user.set_password(password)
    db.session.add(user)
    log("create", "user", email)
    db.session.commit()
    flash("User created.", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/toggle")
@owner_required
def user_toggle(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        flash("You cannot deactivate yourself.", "error")
    else:
        user.is_active = not user.is_active
        db.session.commit()
        flash("User updated.", "success")
    return redirect(url_for("admin.users"))


@bp.post("/users/<int:user_id>/password")
@owner_required
def user_password(user_id: int):
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    password = request.form.get("password") or ""
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
    else:
        user.set_password(password)
        db.session.commit()
        flash("Password changed.", "success")
    return redirect(url_for("admin.users"))


# ---------------------------------------------------------------------------
# Share links
# ---------------------------------------------------------------------------


@bp.get("/share-links")
@login_required
def share_links():
    links = db.session.scalars(
        select(ShareLink).order_by(ShareLink.created_at.desc())
    ).all()
    return render_template("admin/share_links.html", links=links)


@bp.route("/share-links/new", methods=["GET", "POST"])
@login_required
def share_link_new():
    if request.method == "GET":
        raw_ids = request.values.getlist("ids")
        product_ids = [int(value) for value in raw_ids if str(value).isdigit()]
    else:
        product_ids = []
    selected = (
        db.session.scalars(select(Product).where(Product.id.in_(product_ids))).all()
        if product_ids
        else []
    )

    if request.method == "POST":
        chosen = request.form.getlist("product_ids", type=int)
        if not chosen:
            flash("Select at least one product.", "error")
        else:
            days = request.form.get("expires_days", type=int) or 0
            pin = (request.form.get("pin") or "").strip()
            link = ShareLink(
                token=share_token(),
                title=(request.form.get("title") or "Selection").strip()[:160],
                note=(request.form.get("note") or "").strip(),
                show_prices=request.form.get("show_prices") == "on",
                expires_at=utcnow() + timedelta(days=days) if days > 0 else None,
                created_by_id=current_user.id,
            )
            if pin:
                link.pin_hash = generate_password_hash(pin)
            db.session.add(link)
            db.session.flush()
            for order, product_id in enumerate(chosen):
                db.session.add(
                    ShareItem(share_link_id=link.id, product_id=product_id, sort_order=order)
                )
            log("create", "share_link", link.id, {"products": len(chosen)})
            db.session.commit()
            flash("Share link created.", "success")
            return redirect(url_for("admin.share_links"))

    return render_template("admin/share_link_new.html", selected=selected)


@bp.post("/share-links/<int:link_id>/revoke")
@login_required
def share_link_revoke(link_id: int):
    link = db.session.get(ShareLink, link_id)
    if link is None:
        abort(404)
    link.revoked_at = utcnow()
    db.session.commit()
    flash("Link revoked. It now shows as expired.", "success")
    return redirect(url_for("admin.share_links"))


@bp.post("/share-links/<int:link_id>/delete")
@login_required
def share_link_delete(link_id: int):
    link = db.session.get(ShareLink, link_id)
    if link is None:
        abort(404)
    db.session.delete(link)
    db.session.commit()
    flash("Share link deleted.", "success")
    return redirect(url_for("admin.share_links"))
