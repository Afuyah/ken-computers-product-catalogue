"""Catalogue search.

PostgreSQL is the search engine here: a stored tsvector column with weighted
fields plus trigram indexes for partial and typo-tolerant matches. That scales
comfortably past 50,000 SKUs at a fraction of the cost and complexity of a
separate search cluster.

`build_query` is the single place that knows how to search, so swapping in
Elasticsearch/Meilisearch later only means replacing this module.
"""

from __future__ import annotations

import re

from flask import current_app
from sqlalchemy import Select, func, literal_column, or_, select, text

from .extensions import db
from .models import Product

# People type "16gb" and "8gb"; the spec text stores "16 GB". Splitting the
# number from the unit makes both forms match the same tokens.
_UNIT_RE = re.compile(r"(\d)\s*(gb|tb|mb|ghz|mhz)\b", re.IGNORECASE)


def normalise_query(query: str) -> str:
    return _UNIT_RE.sub(r"\1 \2", (query or "").strip())

SORTS = {
    "relevance": (),
    "new": (Product.updated_at.desc(),),
    "price_asc": (Product.price_kes.asc(),),
    "price_desc": (Product.price_kes.desc(),),
    "name": (Product.title.asc(),),
}

# Weighted document: title/sku matter most, description least.
_SEARCH_ALTER = """
ALTER TABLE products ADD COLUMN IF NOT EXISTS search_vector tsvector
GENERATED ALWAYS AS (
    setweight(to_tsvector('simple', coalesce(title, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(sku, '')), 'A') ||
    setweight(to_tsvector('simple', coalesce(brand, '')), 'B') ||
    setweight(to_tsvector('simple', coalesce(summary, '')), 'C') ||
    setweight(to_tsvector('simple', coalesce(specs::text, '')), 'C') ||
    setweight(to_tsvector('simple', coalesce(description, '')), 'D')
) STORED
"""

_SEARCH_INDEX = (
    "CREATE INDEX IF NOT EXISTS ix_products_search_vector "
    "ON products USING GIN (search_vector)"
)

_TRGM_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_products_title_trgm ON products USING GIN (title gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_products_brand_trgm ON products USING GIN (brand gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_products_sku_trgm ON products USING GIN (sku gin_trgm_ops)",
)


def is_postgres() -> bool:
    return db.engine.dialect.name == "postgresql"


def configure_search(app) -> None:
    """Lower the trigram threshold so one-letter typos still match.

    "elitbok" scores 0.22 against "HP EliteBook 840 G8"; PostgreSQL's default
    cut-off is 0.30. 0.2 keeps the index usable while catching common slips.
    """
    with app.app_context():
        engine = db.engine
        if engine.dialect.name != "postgresql":
            return

        from sqlalchemy import event

        @event.listens_for(engine, "connect")
        def _lower_trigram_threshold(dbapi_connection, _record):  # noqa: ANN001
            try:
                cursor = dbapi_connection.cursor()
                cursor.execute("SET pg_trgm.similarity_threshold = 0.2")
                cursor.close()
            except Exception:  # noqa: BLE001 - pg_trgm may not be installed
                try:
                    dbapi_connection.rollback()
                except Exception:  # noqa: BLE001
                    pass


def ensure_search_indexes() -> list[str]:
    """Create the search column and indexes if they are missing.

    Safe to run on every boot: every statement is idempotent.
    """
    if not is_postgres():
        return []

    applied: list[str] = []
    with db.engine.begin() as conn:
        conn.execute(text(_SEARCH_ALTER))
        conn.execute(text(_SEARCH_INDEX))
        applied.append("tsvector")

        # pg_trgm needs the extension; on a locked-down database this may fail,
        # in which case full-text search still works without typo tolerance.
        try:
            with conn.begin_nested():
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                for statement in _TRGM_INDEXES:
                    conn.execute(text(statement))
            applied.append("trigram")
        except Exception:  # noqa: BLE001
            current_app.logger.warning(
                "pg_trgm unavailable; falling back to full-text search only",
                exc_info=True,
            )
    return applied


_trgm_available: bool | None = None


def _has_trigram() -> bool:
    """Is pg_trgm installed? Cached per process."""
    global _trgm_available
    if _trgm_available is None:
        try:
            _trgm_available = bool(
                db.session.execute(
                    text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")
                ).first()
            )
        except Exception:  # noqa: BLE001
            _trgm_available = False
    return _trgm_available


def _text_search(stmt: Select, query: str) -> Select:
    """Attach the search predicate and ranking for `query`."""
    if is_postgres():
        normalised = normalise_query(query)
        tsquery = func.websearch_to_tsquery("simple", normalised)
        vector = literal_column("products.search_vector")
        like = f"%{query}%"

        # Full-text first; ILIKE catches partial tokens like "eliteb";
        # trigram similarity catches typos like "elitbok" on longer queries.
        conditions = [
            vector.op("@@")(tsquery),
            Product.title.ilike(like),
            Product.brand.ilike(like),
            Product.sku.ilike(like),
        ]
        ordering = [
            func.ts_rank(vector, tsquery).desc(),
            Product.updated_at.desc(),
        ]

        if len(query) >= 4 and _has_trigram():
            conditions.append(Product.title.op("%")(query))
            ordering.insert(1, func.similarity(Product.title, query).desc())

        return stmt.where(or_(*conditions)).order_by(*ordering)

    like = f"%{query}%"
    return stmt.where(
        or_(
            Product.title.ilike(like),
            Product.brand.ilike(like),
            Product.sku.ilike(like),
            Product.summary.ilike(like),
            Product.description.ilike(like),
        )
    ).order_by(Product.updated_at.desc())


def build_query(
    query: str = "",
    *,
    category_ids: list[int] | None = None,
    brand: str = "",
    stock: str = "",
    min_price: int = 0,
    max_price: int = 0,
    sort: str = "relevance",
) -> Select:
    stmt = select(Product).where(Product.is_published.is_(True))

    if category_ids:
        stmt = stmt.where(Product.category_id.in_(category_ids))
    if brand:
        stmt = stmt.where(Product.brand == brand)
    if stock in ("in_stock", "low_stock", "out_of_stock"):
        stmt = stmt.where(Product.stock_status == stock)
    if min_price:
        stmt = stmt.where(Product.price_kes >= min_price)
    if max_price:
        stmt = stmt.where(Product.price_kes <= max_price)

    query = (query or "").strip()
    if query:
        stmt = _text_search(stmt, query)
        if sort in SORTS and sort != "relevance":
            stmt = stmt.order_by(None).order_by(*SORTS[sort])
    else:
        stmt = stmt.order_by(*SORTS.get(sort if sort in SORTS and sort != "relevance" else "new"))

    return stmt
