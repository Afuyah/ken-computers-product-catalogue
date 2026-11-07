# --- build the Tailwind stylesheet ----------------------------------------
FROM node:22-alpine AS css
WORKDIR /src
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY tailwind.config.js ./
COPY app/templates ./app/templates
COPY app/static/css/input.css ./app/static/css/input.css
RUN npx tailwindcss -i ./app/static/css/input.css -o ./app/static/css/app.css --minify

# --- runtime ---------------------------------------------------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg powers short-video transcoding; drop this line if you never upload video.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=css /src/app/static/css/app.css ./app/static/css/app.css

RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

EXPOSE 8080
# Create missing tables on boot, but never let a database problem stop the
# process from starting (health checks and static files still work, and the
# warning shows up in the deploy logs).
CMD ["sh", "-c", "flask --app wsgi init-db || echo 'WARNING: database init failed - check DATABASE_URL'; exec gunicorn -c gunicorn.conf.py wsgi:app"]
