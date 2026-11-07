"""Serve the app on a fixed port (used for local preview / screenshots)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from wsgi import app  # noqa: E402

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5099, debug=False, use_reloader=False)
