#!/usr/bin/env python
"""Import laptops from the Ken Computers POS database into this catalogue.

Reads only from the POS database. Writes only to our own database, and only
after ``--apply`` is passed; the default is a dry run.

    python scripts/pos_import.py --dry-run
    python scripts/pos_import.py --apply

Rules applied to the POS data:

* only ``item_type = 'Laptop'`` rows that are not deleted;
* products with variants become one catalogue item per variant (the variant
  holds the real price, stock and specs);
* stock: 0 -> out_of_stock, 1-4 -> low_stock, 5+ -> in_stock;
* prices below 5,000 KES are treated as "not set" (the POS uses 100.00 as a
  placeholder) and imported as 0, which the site shows as "Ask for price";
* every string is sanitised: slashes and control characters removed,
  whitespace collapsed;
* a short description is generated for each laptop.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlalchemy import delete, select  # noqa: E402

from app import create_app  # noqa: E402
from app.extensions import db  # noqa: E402
from app.models import (  # noqa: E402
    Banner,
    Category,
    DownloadEvent,
    Media,
    Product,
    ProductSlug,
    ShareItem,
    ShareLink,
)
from app.utils import make_slug  # noqa: E402

PRICE_FLOOR = 5000  # below this the POS price is a placeholder

CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
SEPARATOR_RE = re.compile(r"[/\\|]+")
SPACE_RE = re.compile(r"\s+")

COI_RE = re.compile(r"\bCOI\s*([3579])\b", re.IGNORECASE)
RAM_RE = re.compile(r"(\d{1,3})\s*GB\s*(?:RAM|DDR\d?|DDR)", re.IGNORECASE)
RAM_TIGHT_RE = re.compile(r"(\d{1,3})\s*GBRAM", re.IGNORECASE)
STORAGE_RE = re.compile(
    r"(\d{2,4})\s*(GB|TB)\s*(SSD|HDD|NVME|ROM)", re.IGNORECASE
)
STORAGE_TIGHT_RE = re.compile(r"(\d{2,4})\s*(SSD|HDD)", re.IGNORECASE)
GEN_RE = re.compile(r"(\d{1,2})\s*(?:ST|ND|RD|TH)\s*GEN", re.IGNORECASE)
PROCESSOR_RE = re.compile(
    r"(core\s*i[3579]|ryzen\s*\d?|celeron|pentium|athlon|ultra\s*[579]|m[123]\s*(?:pro|max)?)",
    re.IGNORECASE,
)

BRAND_ALIASES = {
    "hp": "HP",
    "hewlett packard": "HP",
    "dell": "Dell",
    "lenovo": "Lenovo",
    "asus": "ASUS",
    "acer": "Acer",
    "apple": "Apple",
    "macbook": "Apple",
    "toshiba": "Toshiba",
    "samsung": "Samsung",
    "microsoft": "Microsoft",
    "msi": "MSI",
    "razer": "Razer",
    "huawei": "Huawei",
    "fujitsu": "Fujitsu",
}

CONDITION_LABELS = {
    "new": "New",
    "refurb": "Refurbished",
    "refurbished": "Refurbished",
    "used": "Used",
    "second": "Used",
}


@dataclass
class Item:
    title: str
    sku: str
    brand: str
    price: int
    stock: int
    condition: str
    specs: dict[str, str] = field(default_factory=dict)
    description: str = ""
    published: bool = True
    source_id: str = ""


# ---------------------------------------------------------------------------
# Sanitising
# ---------------------------------------------------------------------------


def clean(value: str | None, limit: int = 200) -> str:
    if not value:
        return ""
    text = str(value)
    text = CONTROL_RE.sub(" ", text)
    text = SEPARATOR_RE.sub(" ", text)
    text = (
        text.replace("&amp;", "&")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
        .replace("\u00a0", " ")
    )
    text = SPACE_RE.sub(" ", text).strip(" -_,.;:")
    return text[:limit].strip()


def expand_processor(text: str) -> str:
    return COI_RE.sub(lambda m: f"Core i{m.group(1)}", text)


def polish_name(text: str) -> str:
    """Tidy the shorthand the POS uses into readable product names."""
    text = re.sub(r"(?<=[A-Za-z0-9])\s*COI\s*([3579])", r" Core i\1", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCOI\s*([3579])\b", r"Core i\1", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d{2,4})\s*SSD\b", r"\1GB SSD", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d{2,4})\s*HDD\b", r"\1GB HDD", text, flags=re.IGNORECASE)
    text = re.sub(r"(\d+)\s*GBRAM\b", r"\1GB RAM", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(\d{1,2})(?:ST|ND|RD|TH)\s*GEN\b",
        lambda m: f"{m.group(1)}th Gen",
        text,
        flags=re.IGNORECASE,
    )
    # Trailing POS shorthand such as TKMMM / CMMMM / TMTMMM is internal noise.
    text = re.sub(r"\s+[TCKNSM]{3,6}$", "", text, flags=re.IGNORECASE)
    return SPACE_RE.sub(" ", text).strip()


def parse_specs(name: str) -> dict[str, str]:
    specs: dict[str, str] = {}
    text = expand_processor(name)

    processor = PROCESSOR_RE.search(text)
    if processor:
        specs["Processor"] = processor.group(1).title().replace("Core I", "Core i").replace("I3", "i3").replace("I5", "i5").replace("I7", "i7").replace("I9", "i9")

    generation = GEN_RE.search(text)
    if generation:
        specs["Generation"] = f"{generation.group(1)}th Gen"

    ram = RAM_RE.search(text) or RAM_TIGHT_RE.search(text)
    if ram:
        specs["Memory"] = f"{int(ram.group(1))} GB"

    storage = STORAGE_RE.search(text)
    if storage:
        specs["Storage"] = f"{int(storage.group(1))} {storage.group(2).upper()} {storage.group(3).upper()}".replace(" NVME", " NVMe")
    else:
        tight = STORAGE_TIGHT_RE.search(text)
        if tight:
            specs["Storage"] = f"{int(tight.group(1))} GB {tight.group(2).upper()}"

    return specs


def stock_status(quantity: int) -> str:
    if quantity <= 0:
        return "out_of_stock"
    if quantity <= 4:
        return "low_stock"
    return "in_stock"


def brand_of(name: str, brand_name: str | None) -> str:
    candidate = clean(brand_name or "", 80)
    if candidate:
        key = candidate.lower()
        return BRAND_ALIASES.get(key, candidate)
    first = clean(name).split(" ")[0].lower() if name else ""
    for alias, label in BRAND_ALIASES.items():
        if first.startswith(alias):
            return label
    return "Other"


def _compact(text: str) -> str:
    return SPACE_RE.sub("", text or "").lower()


def build_description(item: Item) -> str:
    bits = []
    specs = item.specs
    processor = specs.get("Processor", "")
    generation = specs.get("Generation", "")
    title_compact = _compact(item.title)

    # Do not repeat what the title already says.
    if processor and _compact(processor) not in title_compact:
        bits.append(f"{processor} ({generation})" if generation else processor)
    if specs.get("Memory") and _compact(specs["Memory"]) not in title_compact:
        bits.append(f"{specs['Memory']} RAM")
    if specs.get("Storage") and _compact(specs["Storage"]) not in title_compact:
        bits.append(specs["Storage"])

    sentence = f"{item.condition} {item.title} laptop"
    if bits:
        if len(bits) == 1:
            sentence += f" with {bits[0]}"
        else:
            sentence += " with " + ", ".join(bits[:-1]) + f" and {bits[-1]}"
    sentence += "."

    warranty = specs.get("Warranty", "")
    if warranty:
        warranty_text = warranty.lower().replace(" months", "-month").replace(" month", "-month")
        sentence += f" Supplied with a {warranty_text} warranty"
    else:
        sentence += " Genuine stock, checked before dispatch"
    sentence += " by Ken Computers MSA, Mombasa."
    return sentence


def summary_of(description: str) -> str:
    first = description.split(".")[0].strip()
    return (first + ".")[:300]


# ---------------------------------------------------------------------------
# Reading the POS
# ---------------------------------------------------------------------------


def fetch_laptops(url: str) -> list[Item]:
    import psycopg

    connection = psycopg.connect(url, connect_timeout=30)
    connection.read_only = True
    cursor = connection.cursor()

    cursor.execute(
        """
        SELECT p.id, p.name, p.sku, p.condition, p.price, p.stock_on_hand,
               p.warranty_months, p.is_active, p.has_variants, b.name
        FROM products p
        JOIN item_types it ON it.id = p.item_type_id
        LEFT JOIN brands b ON b.id = p.brand_id
        WHERE it.name = 'Laptop' AND p.is_deleted = false
        ORDER BY p.id
        """
    )
    products = cursor.fetchall()

    cursor.execute(
        """
        SELECT v.product_id, v.sku, v.ram, v.storage, v.processor, v.generation,
               v.color, v.condition, v.price, v.stock_on_hand, v.warranty_months, v.extra_specs
        FROM product_variants v
        JOIN products p ON p.id = v.product_id
        JOIN item_types it ON it.id = p.item_type_id
        WHERE it.name = 'Laptop' AND p.is_deleted = false AND v.is_deleted = false
        ORDER BY v.product_id, v.id
        """
    )
    variants: dict[int, list[tuple]] = {}
    for row in cursor.fetchall():
        variants.setdefault(row[0], []).append(row)
    connection.close()

    items: list[Item] = []
    for (
        pid,
        name,
        sku,
        condition,
        price,
        stock_on_hand,
        warranty_months,
        is_active,
        has_variants,
        brand_name,
    ) in products:
        product_variants = variants.get(pid, [])
        base_name = polish_name(clean(expand_processor(clean(name, 140))))
        brand = brand_of(name, brand_name)

        if has_variants and product_variants:
            for variant in product_variants:
                (
                    _pid,
                    v_sku,
                    ram,
                    storage,
                    processor,
                    generation,
                    color,
                    v_condition,
                    v_price,
                    v_stock,
                    v_warranty,
                    extra,
                ) = variant
                specs: dict[str, str] = {}
                if processor:
                    specs["Processor"] = clean(processor, 60)
                if generation:
                    specs["Generation"] = clean(generation, 30)
                if ram:
                    specs["Memory"] = clean(ram, 30)
                if storage:
                    specs["Storage"] = clean(storage, 40)
                if color:
                    specs["Colour"] = clean(color, 30)
                if isinstance(extra, dict):
                    for key, value in list(extra.items())[:6]:
                        key, value = clean(str(key), 40), clean(str(value), 80)
                        if key and value:
                            specs.setdefault(key, value)
                warranty = v_warranty or warranty_months
                if warranty:
                    specs["Warranty"] = f"{int(warranty)} months"

                suffix = ", ".join(
                    part
                    for part in (
                        specs.get("Memory", ""),
                        specs.get("Storage", ""),
                        specs.get("Processor", ""),
                    )
                    if part
                )
                title = f"{base_name} ({suffix})" if suffix else base_name
                price_value = int(v_price) if v_price and v_price >= PRICE_FLOOR else 0
                quantity = int(v_stock or 0)
                item = Item(
                    title=clean(title, 200),
                    sku=clean(v_sku or f"{sku}-{title[:6]}", 60).upper(),
                    brand=brand,
                    price=price_value,
                    stock=quantity,
                    condition=CONDITION_LABELS.get(
                        clean(v_condition or condition, 20).lower(), "Refurbished"
                    ),
                    specs=specs,
                    published=bool(is_active),
                    source_id=f"variant:{v_sku}",
                )
                items.append(item)
            continue

        specs = parse_specs(base_name)
        warranty = warranty_months
        if warranty:
            specs["Warranty"] = f"{int(warranty)} months"
        price_value = int(price) if price and price >= PRICE_FLOOR else 0
        item = Item(
            title=clean(base_name, 200),
            sku=clean(sku or f"POS-{pid}", 60).upper(),
            brand=brand,
            price=price_value,
            stock=int(stock_on_hand or 0),
            condition=CONDITION_LABELS.get(clean(condition, 20).lower(), "Refurbished"),
            specs=specs,
            published=bool(is_active),
            source_id=f"product:{pid}",
        )
        items.append(item)

    for item in items:
        item.description = build_description(item)

    return items


# ---------------------------------------------------------------------------
# Writing to our database
# ---------------------------------------------------------------------------


def unique_title(title: str, used: set[str]) -> str:
    candidate = title
    if candidate and candidate not in used:
        used.add(candidate)
        return candidate
    base = candidate or "Laptop"
    for n in range(2, 50):
        proposal = f"{base} #{n}"
        if proposal not in used:
            used.add(proposal)
            return proposal
    return base


def clear_catalogue() -> None:
    """Remove every piece of catalogue content, keep staff accounts."""
    from flask import current_app

    db.session.execute(delete(DownloadEvent))
    db.session.execute(delete(ShareItem))
    db.session.execute(delete(ShareLink))
    db.session.execute(delete(ProductSlug))
    db.session.execute(delete(Media))
    db.session.execute(delete(Product))
    db.session.execute(delete(Banner))
    db.session.execute(delete(Category))
    db.session.commit()

    # Drop the demo images from local storage too, so nothing is orphaned.
    if current_app.config["STORAGE_BACKEND"] != "r2":
        import shutil

        root = Path(current_app.config["MEDIA_ROOT"])
        if root.is_dir():
            for entry in root.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)


def import_items(items: list[Item]) -> dict[str, int]:
    laptops = Category(
        name="Laptops", slug="laptops", description="Business, gaming and everyday laptops.", sort_order=0
    )
    db.session.add(laptops)
    db.session.flush()

    brand_categories: dict[str, Category] = {}
    used_titles: set[str] = set()
    used_slugs: set[str] = set()
    used_skus: set[str] = set()
    stats = {"created": 0, "published": 0, "out": 0, "low": 0, "in": 0}

    for order, item in enumerate(items):
        brand_key = item.brand or "Other"
        category = brand_categories.get(brand_key)
        if category is None:
            base_slug = make_slug(f"laptops-{brand_key}")
            slug = base_slug
            n = 2
            while slug in used_slugs:
                slug = f"{base_slug}-{n}"
                n += 1
            used_slugs.add(slug)
            category = Category(
                name=brand_key, slug=slug, parent_id=laptops.id, sort_order=len(brand_categories)
            )
            db.session.add(category)
            db.session.flush()
            brand_categories[brand_key] = category

        title = unique_title(item.title, used_titles)
        base_slug = make_slug(title) or f"laptop-{order}"
        slug = base_slug
        n = 2
        while slug in used_slugs:
            slug = f"{base_slug}-{n}"
            n += 1
        used_slugs.add(slug)

        sku = item.sku
        if not sku or sku in used_skus:
            sku = f"{sku or 'POS'}-{order}"
        used_skus.add(sku)

        status = stock_status(item.stock)
        product = Product(
            sku=sku[:60],
            title=title,
            slug=slug,
            brand=item.brand,
            category_id=category.id,
            condition=item.condition,
            summary=summary_of(item.description),
            description=item.description,
            specs=item.specs,
            price_kes=item.price,
            stock_status=status,
            is_published=item.published,
            is_featured=False,
        )
        db.session.add(product)
        stats["created"] += 1
        stats["published"] += int(item.published)
        stats[{"out_of_stock": "out", "low_stock": "low", "in_stock": "in"}[status]] += 1

    db.session.commit()
    return stats


def refresh_text() -> int:
    """Regenerate summary/description for every product already in the catalogue."""
    products = db.session.query(Product).all()
    for product in products:
        item = Item(
            title=product.title,
            sku=product.sku,
            brand=product.brand,
            price=product.price_kes,
            stock=0,
            condition=product.condition,
            specs=dict(product.specs or {}),
        )
        product.description = build_description(item)
        product.summary = summary_of(product.description)
    db.session.commit()
    return len(products)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.getenv("POS_DATABASE_URL", ""), help="POS database URL")
    parser.add_argument("--apply", action="store_true", help="Write to the catalogue (default: dry run)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only; same as the default")
    parser.add_argument("--keep-existing", action="store_true", help="Do not clear existing content")
    parser.add_argument("--sample", type=int, default=8, help="Rows to print in the preview")
    parser.add_argument("--refresh-text", action="store_true", help="Only regenerate descriptions")
    args = parser.parse_args()

    if args.refresh_text:
        app = create_app()
        with app.app_context():
            count = refresh_text()
        print(f"Refreshed descriptions for {count} product(s).")
        return 0

    if not args.url:
        print("No POS URL. Pass --url or set POS_DATABASE_URL.")
        return 2

    items = fetch_laptops(args.url)
    print(f"Read {len(items)} laptop item(s) from the POS.\n")

    brands: dict[str, int] = {}
    for item in items:
        brands[item.brand] = brands.get(item.brand, 0) + 1
    print("Brands:", ", ".join(f"{k}={v}" for k, v in sorted(brands.items(), key=lambda kv: -kv[1])))

    priced = sum(1 for item in items if item.price > 0)
    print(f"With a real price: {priced} | ask-for-price: {len(items) - priced}")
    print(f"Stock: {sum(1 for i in items if i.stock <= 0)} out | "
          f"{sum(1 for i in items if 1 <= i.stock <= 4)} low | "
          f"{sum(1 for i in items if i.stock >= 5)} in\n")

    for item in items[: max(0, args.sample)]:
        print(f"  {item.sku:<22} {item.title[:70]:<70} {item.brand:<8} "
              f"{item.price:>7} qty={item.stock:<3} {item.condition}")

    if not args.apply:
        print("\nDry run. Nothing was written. Re-run with --apply to import.")
        return 0

    app = create_app()
    with app.app_context():
        if not args.keep_existing:
            clear_catalogue()
            print("Cleared existing catalogue content (accounts kept).")
        stats = import_items(items)
        print(f"Imported {stats['created']} products "
              f"({stats['published']} published, {stats['out']} out of stock, "
              f"{stats['low']} low, {stats['in']} in stock).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
