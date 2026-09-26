"""Vercel Python serverless entry point for the FastAPI backend.

Deployed from the repo root with vercel.json (see repo root).
The ASGI app is the same one used locally — 12-factor, no surprises.
"""

import os
import sys

# Vercel runtime config (parsed before app import)
os.environ.setdefault("APP_ENV", "production")

# backend/ must be on sys.path so `app` resolves from <repo>/backend/api/index.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402

# Vercel's Python builder looks for the ASGI callable named `app`
