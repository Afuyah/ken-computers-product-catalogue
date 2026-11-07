# Ken Computers MSA — Product Catalogue

A fast, public product catalogue for laptops, printers and accessories, with a
staff back office for uploading products, photos and short videos.

- **Flask + SQLAlchemy + PostgreSQL** (SQLite for local dev)
- **Tailwind CSS**, built at deploy time — no runtime CSS framework
- **Cloudflare R2 + CDN** for images and video (zero egress fees)
- **Flipbook-free**: every photo is pre-sized to 400 / 800 / 1600 px in WebP
- **Public catalogue** with SEO, OpenGraph previews and WhatsApp ordering
- **Share links** for hand-picked selections, with expiry, PIN and revocation
- **Downloads**: single asset or a ZIP of every photo for a product

---

## 1. What is in the box

```
app/
  __init__.py        app factory, error pages, template filters
  config.py          every setting comes from the environment
  models.py          Product, Category, Media, ShareLink, User, DownloadEvent
  public.py          home, category, product, search, sitemap, robots
  admin.py           staff CRUD for products, media, categories, users, shares
  share.py           /s/<token> share pages and /d/<id> downloads
  storage.py         LocalStorage (dev) and R2Storage (production)
  imaging.py         Pillow + ffmpeg pipelines
  media_service.py   upload orchestration: bytes → variants → database
  cli.py             init-db, create-owner, seed-demo, stats
  templates/         Jinja templates (public + admin)
  static/css/        Tailwind input and built stylesheet
scripts/
  ingest.py          bulk import from a folder of CSV + photos
Dockerfile           multi-stage build (Node for CSS, Python for runtime)
fly.toml             Fly.io configuration (Johannesburg)
railway.json         Railway configuration
```

---

## 2. Run it locally

Requires Python 3.11+ and Node 18+ (Node is only used to build the CSS).

```powershell
cd kencomputersmsa

# 1. Python environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Configuration
Copy-Item .env.example .env
# edit .env if you like; the defaults work for local development

# 3. Database + demo catalogue
python -m flask --app wsgi init-db
python -m flask --app wsgi create-owner      # your email + password
python -m flask --app wsgi seed-demo         # 24 sample products with placeholder photos

# (or, no CLI needed at all: creates tables, owner and demo data in one go)
python scripts/setup_local.py

# 4. Stylesheet
npm install
npm run css:build

# 5. Run
python -m flask --app wsgi run --debug
```

Open <http://127.0.0.1:5000> for the catalogue and
<http://127.0.0.1:5000/admin> for the back office.

> `python -m flask …` works even when the `flask` command is not on your PATH.
> If you activated a virtualenv, plain `flask --app wsgi run` is equivalent.
> `gunicorn` is only used inside the Docker image — it does not run on Windows,
> so always use `flask run` locally.

The seed script creates a demo owner account so you can look around
immediately: **admin@kencomputersmsa.com / ChangeMe123!** — change it
before the site goes live (`Users` → set a new password).

Check everything still renders after a change:

```powershell
python scripts\smoke.py
```

> If PowerShell blocks `npm`, use `npm.cmd install` and `npm.cmd run css:build`.
> While developing CSS, run `npm run css:watch` in a second terminal.

Local media is written to `./media` and served by Flask at `/media/...`.
That path is **development only**; production uses the CDN.

---

## 3. Day-to-day in the admin

| Task | Where |
|---|---|
| Add a product | Products → **+ Add product** |
| Upload photos / clips | Open the product → **Photos & video** → Upload |
| Choose the cover | Photos grid → **Cover** (or move with ↑ ↓) |
| Mark a product “a few pieces left” | Price & stock → **Stock status** |
| Unpublish without deleting | Visibility → untick **Published** |
| Add a category | Categories → left-hand form |
| Add a staff account | Users (owner only) |
| Send a customer a selection | Products → tick items → **Create share link from selected** |

Stock statuses map to Google’s structured data automatically:

| Status | Badge | schema.org |
|---|---|---|
| `in_stock` | In stock | `InStock` |
| `low_stock` | A few pieces left | `LimitedAvailability` |
| `out_of_stock` | Out of stock | `OutOfStock` |

Deleted products remove their files from storage. Old product URLs keep working
via a 301 redirect (`product_slugs` table) so links already sent to customers
never break.

---

## 4. Media storage: Cloudflare R2

R2 is S3-compatible, has **no egress fees**, and sits behind Cloudflare’s edge
(including Nairobi). At ~2,000 products × 6 photos you will store roughly
30–35 GB and pay about **$0.30/month**.

### One-time setup

1. Cloudflare dashboard → **R2** → *Create bucket* → `kencomputers-media`.
2. **Manage R2 API Tokens** → *Create API token* → permission **Object Read & Write**
   → scope it to that bucket. Copy the Access Key ID and Secret Access Key.
3. Bucket → **Settings** → **Custom Domains** → connect `cdn.kencomputersmsa.com`.
4. Put the values in `.env` (and in your host’s secrets):

```env
STORAGE_BACKEND=r2
R2_ACCOUNT_ID=xxxxxxxxxxxxxxxx
R2_ACCESS_KEY_ID=xxxxxxxxxxxxxxxx
R2_SECRET_ACCESS_KEY=xxxxxxxxxxxxxxxx
R2_BUCKET=kencomputers-media
CDN_BASE_URL=https://cdn.kencomputersmsa.com
```

Uploads are written with `Cache-Control: public, max-age=31536000, immutable`
and content-hashed paths, so the CDN can cache them forever.

**Downloads** are not proxied through Flask. `/d/<media_id>` checks the record,
logs the event and `302`s to a short-lived R2 presigned URL carrying
`Content-Disposition: attachment`, so the browser saves the file with a proper
name.

---

## 5. Deploying

### Option A — everything on Railway (recommended for one small team)

Railway runs the app and Postgres in the same private network (≈1 ms between
them) in `europe-west4` (Amsterdam), and Cloudflare caches the public pages.

1. Push this folder to a GitHub repository.
2. Railway → **New Project** → *Deploy from GitHub repo*.
3. Add **PostgreSQL** to the same project. Railway injects `DATABASE_URL`.
4. On the web service set:
   - `SECRET_KEY` (generate with `python -c "import secrets;print(secrets.token_hex(32))"`)
   - `STORAGE_BACKEND=r2` plus the five `R2_*` / `CDN_BASE_URL` values
   - `SITE_URL=https://kencomputersmsa.com`
   - `SESSION_COOKIE_SECURE=1`
   - `CONTACT_WHATSAPP=2547xxxxxxxx`
5. Deploy. The start command runs `init-db` then gunicorn.
6. Create your owner account once, from the Railway shell:

```bash
flask --app wsgi create-owner
```

### Option B — Fly.io (Johannesburg)

Fly’s `jnb` region is the closest to Kenya, but **Managed Postgres is not offered
in `jnb`**. If you choose Fly, run Postgres yourself on a second Machine with a
volume in `jnb`, and point `DATABASE_URL` at `<app>.internal`.

```bash
fly launch --no-deploy
fly secrets set SECRET_KEY=... STORAGE_BACKEND=r2 R2_ACCOUNT_ID=... \
  R2_ACCESS_KEY_ID=... R2_SECRET_ACCESS_KEY=... R2_BUCKET=... \
  CDN_BASE_URL=https://cdn.kencomputersmsa.com
fly deploy
fly ssh console -C "flask --app wsgi create-owner"
```

> Do not run Flask on Fly and Postgres on Railway. Railway has no African
> region, so every query would cross the ocean (~150 ms each). Pick one
> platform per environment.

### Cloudflare

1. Add `kencomputersmsa.com` to Cloudflare and change the nameservers at your
   registrar.
2. DNS:
   - `kencomputersmsa.com` → CNAME to your Railway/Fly hostname, **Proxied**
   - `www` → CNAME to `kencomputersmsa.com`, **Proxied**
   - `cdn` → created automatically by R2
3. If Railway/Fly needs to issue a TLS certificate, set the record to
   **DNS only** until the certificate is active, then switch back to **Proxied**.
4. **Cache Rules**:
   - `/admin/*` and `/s/*` → **Bypass cache**
   - `/static/*` → cache everything, Edge TTL 1 year
   - `/p/*` and `/c/*` → cache eligible, Edge TTL 1 hour, Browser TTL 5 minutes

---

## 6. Importing the existing catalogue

Photos on the laptop, 2,000 items — do not click a web form 2,000 times.

```
data/
  products.csv
  media/
    KCM-1001/01.jpg
    KCM-1001/02.jpg
    KCM-1001/clip.mp4
```

`products.csv` header:

```csv
sku,title,brand,category,price_kes,price_was_kes,stock_status,summary,description,specs,condition,featured,published
KCM-1001,HP EliteBook 840 G8,HP,Business Laptops,78500,92000,in_stock,,,Processor=Intel i5-1135G7; Memory=16 GB; Storage=512 GB,New,no,yes
```

Then:

```powershell
# validate first — nothing is written
python scripts/ingest.py .\data --dry-run

# import everything
python scripts/ingest.py .\data

# or a first batch of 50 to check the result
python scripts/ingest.py .\data --limit 50
```

The importer is idempotent: re-running updates the row and only uploads files
whose checksum is new. Categories are created from the `category` column.
Videos are transcoded when `ffmpeg` is installed locally (recommended — keeps
your server small); otherwise upload an already-compressed `.mp4`.

---

## 7. Speed checklist (already implemented)

- Right-sized WebP variants, never the original, in grids
- `srcset`-ready `card` (800 px) and `thumb` (400 px) files
- `loading="lazy"` + `decoding="async"` below the fold, `fetchpriority="high"` on the first image
- Fixed `aspect-[4/3]` image boxes → no layout shift
- Inline WebP LQIP placeholder stored per asset (`media.lqip`)
- `Cache-Control: immutable` on every media object
- Cloudflare edge cache in Nairobi
- 24 products per page
- 1–3 SQL queries per page, `selectinload` on media

---

## 8. Backups

Postgres is the only thing you cannot rebuild from files.

```bash
# nightly, to your R2 bucket
pg_dump "$DATABASE_URL" | gzip > dump.sql.gz
```

Run it from a scheduled GitHub Action, a Railway cron service, or a Fly
scheduled Machine, then upload to `s3://kencomputers-media/backups/`.
Media in R2 is already durable; enable **Object Versioning** on the bucket for
an extra safety net against accidental deletes.

---

## 9. Going-live checklist

- [ ] `SECRET_KEY` is a long random value, not the default
- [ ] `SITE_URL=https://kencomputersmsa.com`
- [ ] `SESSION_COOKIE_SECURE=1`
- [ ] `CONTACT_WHATSAPP` is the real number (digits only, e.g. `254712345678`)
- [ ] Owner account created, demo data removed (`flask seed-demo` is opt-in only)
- [ ] R2 custom domain resolves and serves an image over HTTPS
- [ ] Cloudflare cache rules bypass `/admin/*` and `/s/*`
- [ ] `/sitemap.xml` and `/robots.txt` load
- [ ] A product URL previews correctly in WhatsApp
- [ ] Nightly `pg_dump` scheduled and restored once to prove it works
- [ ] `python -m flask --app wsgi stats` shows the expected counts

---

## 10. Deliberate limits (and the upgrade path)

| Now | Later, if needed |
|---|---|
| `db.create_all()` via `init-db` | Adopt Alembic (`flask db init`) before the first schema change |
| Search via `ILIKE` | Postgres `tsvector` + GIN index — fine well past 2,000 rows |
| No stock quantities | Add `stock_qty` and decrement on order |
| No cart/checkout | Add orders, M-Pesa STK push, or link to an existing POS |
| Images processed in-request | Move uploads to a job table + worker if uploads get heavy |

The data model already leaves room for all of these.
