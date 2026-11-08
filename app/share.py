"""Customer-facing share links and file downloads."""

from __future__ import annotations

import io
import zipfile

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from flask_login import login_required
from sqlalchemy import select

from .extensions import db
from .models import DownloadEvent, Media, Product, ShareLink
from .storage import get_storage
from .utils import hash_ip, utcnow

bp = Blueprint("share", __name__)


def _session_key(token: str) -> str:
    return f"share_ok_{token}"


@bp.route("/s/<token>", methods=["GET", "POST"])
def view(token: str):
    link = db.session.scalar(select(ShareLink).where(ShareLink.token == token))
    if link is None:
        abort(404)

    unlocked = session.get(_session_key(token)) is True

    if link.requires_pin and not unlocked:
        if request.method == "POST":
            if link.check_pin(request.form.get("pin") or ""):
                session[_session_key(token)] = True
                return redirect(url_for("share.view", token=token))
            flash("That PIN is not correct.", "error")
        return render_template("public/share_pin.html", link=link)

    if not link.is_live:
        return render_template("public/share_expired.html", link=link), 410

    return render_template(
        "public/share.html", link=link, products=link.products(), unlocked=unlocked
    )


@bp.get("/d/<media_public_id>")
@login_required
def download(media_public_id: str):
    media = db.session.scalar(select(Media).where(Media.public_id == media_public_id))
    if media is None:
        abort(404)

    product = db.session.get(Product, media.product_id)
    if product is None or not product.is_published:
        abort(404)

    target = media.file("original") or media.full or media.video_file
    if target is None:
        abort(404)

    db.session.add(
        DownloadEvent(
            media_id=media.id,
            product_id=product.id,
            ip_hash=hash_ip(request.remote_addr),
            user_agent=(request.user_agent.string or "")[:300],
        )
    )
    db.session.commit()

    base = product.slug or "download"
    filename = f"{base}-{media.public_id}.{target.format}"
    return redirect(get_storage().download_url(target.storage_key, filename), code=302)


@bp.get("/p/<slug>/download")
@login_required
def download_all(slug: str):
    product = db.session.scalar(select(Product).where(Product.slug == slug))
    if product is None or not product.is_published:
        abort(404)
    if not product.images:
        abort(404)

    storage = get_storage()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for index, media in enumerate(product.images, start=1):
            target = media.file("original") or media.full
            if target is None:
                continue
            try:
                data = storage.read(target.storage_key)
            except Exception:  # noqa: BLE001 - a missing file must not break the zip
                continue
            archive.writestr(f"{product.slug}/{index:02d}.{target.format}", data)

    buffer.seek(0)
    if buffer.getbuffer().nbytes <= 22:  # empty zip archive
        abort(404)

    db.session.add(
        DownloadEvent(
            product_id=product.id,
            ip_hash=hash_ip(request.remote_addr),
            user_agent=(request.user_agent.string or "")[:300],
        )
    )
    db.session.commit()

    return send_file(
        buffer,
        mimetype="application/zip",
        as_attachment=True,
        download_name=f"{product.slug}-images.zip",
    )
