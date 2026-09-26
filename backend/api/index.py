"""Vercel Python serverless entry point for the FastAPI backend.

Deployed from the repo root with vercel.json (see repo root).
The ASGI app is the same one used locally — 12-factor, no surprises.
"""

import os

# Vercel runtime config (parsed before app import)
os.environ.setdefault("APP_ENV", "production")

from app.main import app  # noqa: E402

# Vercel's Python builder looks for the ASGI callable named `app`
