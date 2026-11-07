"""Application factory."""

from __future__ import annotations

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config
from .extensions import csrf, db, login_manager, migrate
from .utils import format_kes, human_bytes, truncate


def create_app(config_object: type | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=False)
    app.config.from_object(config_object or Config)

    # Behind Cloudflare + Fly/Railway the real scheme/host arrive in headers.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    db.init_app(app)
    migrate.init_app(app, db)
    csrf.init_app(app)
    login_manager.init_app(app)

    from . import admin, public, share

    app.register_blueprint(public.bp)
    app.register_blueprint(share.bp)
    app.register_blueprint(admin.bp)

    from . import cli

    cli.register(app)

    # --- template helpers --------------------------------------------------
    app.jinja_env.filters["kes"] = format_kes
    app.jinja_env.filters["bytes"] = human_bytes
    app.jinja_env.filters["summary"] = truncate

    @app.context_processor
    def inject_globals():
        return {"format_kes": format_kes}

    @app.template_global()
    def page_url(page: int) -> str:
        """Current URL with the page argument replaced (used by pagination)."""
        from flask import request, url_for

        params = dict(request.view_args or {})
        params.update(request.args.to_dict())
        params["page"] = page
        return url_for(request.endpoint, **params)

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", code=403, message="You do not have access to that page."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return (
            render_template(
                "error.html", code=404, message="That page or product does not exist."
            ),
            404,
        )

    @app.errorhandler(413)
    def too_large(_error):
        limit = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        return (
            render_template(
                "error.html", code=413, message=f"That upload is larger than {limit} MB."
            ),
            413,
        )

    @app.errorhandler(500)
    def server_error(_error):  # pragma: no cover
        db.session.rollback()
        return (
            render_template("error.html", code=500, message="Something went wrong on our side."),
            500,
        )

    @app.shell_context_processor
    def shell_context():
        from .models import Category, Media, Product, ShareLink, User

        return {
            "db": db,
            "User": User,
            "Product": Product,
            "Category": Category,
            "Media": Media,
            "ShareLink": ShareLink,
        }

    return app


@login_manager.user_loader
def load_user(user_id: str):
    from .models import User

    try:
        return db.session.get(User, int(user_id))
    except (TypeError, ValueError):
        return None
