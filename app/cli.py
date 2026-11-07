"""Flask CLI commands: bootstrap, demo data, housekeeping."""

from __future__ import annotations

import random
from io import BytesIO

import click
from flask import Flask
from PIL import Image, ImageDraw

from .extensions import db
from .models import Category, Media, Product, ShareLink, User
from .utils import make_slug


def register(app: Flask) -> None:
    app.cli.add_command(init_db)
    app.cli.add_command(create_owner)
    app.cli.add_command(seed_demo)
    app.cli.add_command(stats)


@click.command("init-db")
def init_db() -> None:
    """Create all tables (first run / local development)."""
    db.create_all()
    click.echo("Tables created.")


@click.command("create-owner")
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--name", prompt=True, default="Owner")
def create_owner(email: str, password: str, name: str) -> None:
    """Create the first administrator account."""
    email = email.strip().lower()
    if db.session.query(User).filter_by(email=email).first():
        raise click.ClickException("That email already exists.")
    if len(password) < 8:
        raise click.ClickException("Password must be at least 8 characters.")
    user = User(email=email, name=name.strip()[:120], role="owner", is_active=True)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    click.echo(f"Owner created: {email}")


@click.command("stats")
def stats() -> None:
    """Print a quick inventory summary."""
    click.echo(f"products : {db.session.query(Product).count()}")
    click.echo(f"published: {db.session.query(Product).filter_by(is_published=True).count()}")
    click.echo(f"media    : {db.session.query(Media).count()}")
    click.echo(f"shares   : {db.session.query(ShareLink).count()}")


# ---------------------------------------------------------------------------
# Demo catalogue
# ---------------------------------------------------------------------------

CATEGORY_TREE = {
    "Laptops": ["Business Laptops", "Gaming Laptops"],
    "Printers": ["Laser Printers", "Inkjet Printers"],
    "Accessories": ["Toner & Ink", "Keyboards & Mice", "Bags & Cases", "Cables & Adapters"],
    "Desktops": [],
    "Monitors": [],
}

DEMO_PRODUCTS = [
    ("HP EliteBook 840 G8", "HP", "Business Laptops", 78500, 92000, "in_stock",
     {"Processor": "Intel Core i5-1135G7", "Memory": "16 GB DDR4", "Storage": "512 GB NVMe SSD",
      "Display": '14" FHD IPS', "Graphics": "Intel Iris Xe", "OS": "Windows 11 Pro"}),
    ("Lenovo ThinkPad T14 Gen 3", "Lenovo", "Business Laptops", 82000, None, "in_stock",
     {"Processor": "AMD Ryzen 5 PRO 6650U", "Memory": "16 GB DDR4", "Storage": "512 GB SSD",
      "Display": '14" FHD', "OS": "Windows 11 Pro"}),
    ("Dell Latitude 5430", "Dell", "Business Laptops", 74900, 85000, "low_stock",
     {"Processor": "Intel Core i5-1235U", "Memory": "8 GB DDR4", "Storage": "256 GB SSD",
      "Display": '14" FHD', "OS": "Windows 11 Pro"}),
    ("Apple MacBook Air M2", "Apple", "Business Laptops", 168000, None, "in_stock",
     {"Chip": "Apple M2", "Memory": "8 GB unified", "Storage": "256 GB SSD",
      "Display": '13.6" Liquid Retina', "OS": "macOS Sonoma"}),
    ("Asus ROG Strix G16", "Asus", "Gaming Laptops", 195000, 210000, "low_stock",
     {"Processor": "Intel Core i7-13650HX", "Memory": "16 GB DDR5", "Storage": "1 TB SSD",
      "Display": '16" FHD+ 165Hz', "Graphics": "NVIDIA RTX 4060 8GB"}),
    ("Acer Nitro V 15", "Acer", "Gaming Laptops", 112000, None, "in_stock",
     {"Processor": "Intel Core i5-13420H", "Memory": "16 GB DDR5", "Storage": "512 GB SSD",
      "Display": '15.6" FHD 144Hz', "Graphics": "NVIDIA RTX 3050 6GB"}),
    ("HP LaserJet Pro M404dn", "HP", "Laser Printers", 42500, 47000, "in_stock",
     {"Type": "Monochrome laser", "Speed": "40 ppm", "Duplex": "Yes", "Network": "Ethernet"}),
    ("Canon imageRUNNER 2630i", "Canon", "Laser Printers", 132000, None, "low_stock",
     {"Type": "A3 monochrome", "Speed": "30 ppm", "Duplex": "Yes", "Network": "Ethernet, Wi-Fi"}),
    ("Epson EcoTank L3250", "Epson", "Inkjet Printers", 28500, 32000, "in_stock",
     {"Type": "Inkjet all-in-one", "Print": "Colour", "Scan/Copy": "Yes", "Network": "Wi-Fi"}),
    ("Brother DCP-T520W", "Brother", "Inkjet Printers", 31500, None, "out_of_stock",
     {"Type": "Ink tank all-in-one", "Print": "Colour", "Network": "Wi-Fi"}),
    ("HP 85A Toner Cartridge", "HP", "Toner & Ink", 9800, None, "in_stock",
     {"Yield": "1,600 pages", "Colour": "Black", "Compatible": "LaserJet P1102"}),
    ("Canon 057 Toner", "Canon", "Toner & Ink", 11500, 12800, "in_stock",
     {"Yield": "3,100 pages", "Colour": "Black", "Compatible": "imageCLASS MF4400"}),
    ("Epson 003 Ink Bottle Set", "Epson", "Toner & Ink", 3800, None, "in_stock",
     {"Contents": "Black, Cyan, Magenta, Yellow", "Compatible": "EcoTank L3110/L3250"}),
    ("Logitech MK270 Wireless Combo", "Logitech", "Keyboards & Mice", 4200, 4800, "in_stock",
     {"Type": "Keyboard + mouse", "Connection": "2.4 GHz wireless", "Battery": "24 months"}),
    ("Dell KB216 Wired Keyboard", "Dell", "Keyboards & Mice", 1850, None, "in_stock",
     {"Type": "Wired keyboard", "Layout": "US QWERTY", "Connection": "USB"}),
    ("HP X500 Wired Mouse", "HP", "Keyboards & Mice", 1200, None, "in_stock",
     {"Type": "Optical mouse", "DPI": "1600", "Connection": "USB"}),
    ("Targus 15.6\" Laptop Backpack", "Targus", "Bags & Cases", 4500, 5200, "in_stock",
     {"Fits": "Up to 15.6 inch", "Material": "Polyester", "Padded sleeve": "Yes"}),
    ("HP 15.6\" Essential Topload Bag", "HP", "Bags & Cases", 3200, None, "low_stock",
     {"Fits": "Up to 15.6 inch", "Compartments": "2"}),
    ("USB-C to HDMI Adapter", "Generic", "Cables & Adapters", 1500, None, "in_stock",
     {"Input": "USB-C", "Output": "HDMI 4K@30Hz", "Length": "15 cm"}),
    ("HDMI 2.1 Cable 2m", "Generic", "Cables & Adapters", 850, None, "in_stock",
     {"Length": "2 m", "Resolution": "8K@60Hz", "Connector": "Gold plated"}),
    ("Dell OptiPlex 7010 SFF", "Dell", "Desktops", 68000, 74000, "in_stock",
     {"Processor": "Intel Core i5-13500", "Memory": "16 GB DDR4", "Storage": "512 GB SSD",
      "OS": "Windows 11 Pro"}),
    ("HP ProDesk 400 G9 MT", "HP", "Desktops", 72500, None, "low_stock",
     {"Processor": "Intel Core i5-12500", "Memory": "8 GB DDR4", "Storage": "256 GB SSD",
      "OS": "Windows 11 Pro"}),
    ("Dell 24\" P2422H Monitor", "Dell", "Monitors", 24500, 27000, "in_stock",
     {"Size": '23.8"', "Panel": "IPS", "Resolution": "1920 x 1080", "Ports": "HDMI, DP, VGA"}),
    ("HP 22\" V22v Monitor", "HP", "Monitors", 15500, None, "out_of_stock",
     {"Size": '21.5"', "Panel": "VA", "Resolution": "1920 x 1080", "Ports": "HDMI, VGA"}),
]

PALETTE = [
    (37, 99, 235), (5, 150, 105), (217, 119, 6), (124, 58, 237),
    (219, 39, 119), (15, 118, 110), (234, 88, 12), (71, 85, 105),
]


def _placeholder_image(title: str, index: int) -> bytes:
    """Generate a clean product-ish placeholder so the demo looks real."""
    width, height = 1200, 900
    base = PALETTE[index % len(PALETTE)]
    image = Image.new("RGB", (width, height), (248, 250, 252))
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 12], fill=base)
    draw.rounded_rectangle([120, 170, 1080, 700], radius=28, fill=base)
    draw.rounded_rectangle([150, 200, 1050, 670], radius=20, fill=(255, 255, 255))
    words = title.split()
    for line_no in range(0, len(words), 3):
        text = " ".join(words[line_no : line_no + 3])
        draw.text((190, 260 + line_no * 55), text, fill=(15, 23, 42))
    draw.text((190, 600), f"DEMO IMAGE {index + 1}", fill=(100, 116, 139))
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=88)
    return buffer.getvalue()


@click.command("seed-demo")
@click.option("--products", default=len(DEMO_PRODUCTS), help="How many demo products to create.")
@click.option("--images", default=3, help="Placeholder images per product.")
def seed_demo(products: int, images: int) -> None:
    """Create categories and a small demo catalogue (safe to re-run)."""
    from .media_service import add_image

    created_categories: dict[str, Category] = {}
    for order, (parent_name, children) in enumerate(CATEGORY_TREE.items()):
        parent = db.session.query(Category).filter_by(slug=make_slug(parent_name)).first()
        if parent is None:
            parent = Category(name=parent_name, slug=make_slug(parent_name), sort_order=order)
            db.session.add(parent)
            db.session.flush()
        created_categories[parent_name] = parent
        for child_order, child_name in enumerate(children):
            slug = make_slug(child_name)
            if db.session.query(Category).filter_by(slug=slug).first() is None:
                db.session.add(
                    Category(name=child_name, slug=slug, parent_id=parent.id, sort_order=child_order)
                )
    db.session.commit()

    categories = {
        c.name: c for c in db.session.query(Category).all()
    }

    made = 0
    for index, (title, brand, category_name, price, was, stock, specs) in enumerate(
        DEMO_PRODUCTS[:products]
    ):
        sku = f"KCM-{1000 + index}"
        if db.session.query(Product).filter_by(sku=sku).first() is not None:
            continue
        category = categories.get(category_name)
        product = Product(
            sku=sku,
            title=title,
            slug=make_slug(f"{title}-{sku}"),
            brand=brand,
            category_id=category.id if category else None,
            condition="New",
            summary=f"{title} available at Ken Computers MSA, Mombasa.",
            description=(
                f"{title} supplied by Ken Computers MSA. Genuine unit with warranty. "
                "Contact us on WhatsApp for availability, delivery and payment options."
            ),
            specs=specs,
            price_kes=price,
            price_was_kes=was,
            stock_status=stock,
            is_published=True,
            is_featured=index < 8,
        )
        db.session.add(product)
        db.session.flush()
        for image_index in range(images):
            add_image(
                product,
                _placeholder_image(title, index * images + image_index),
                alt=f"{title} photo {image_index + 1}",
            )
        made += 1

    db.session.commit()
    click.echo(f"Seeded {made} product(s) with {images} image(s) each.")


__all__ = ["register"]
