#!/usr/bin/env python
"""Flask CLI entry point: `python manage.py <command>`."""

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
