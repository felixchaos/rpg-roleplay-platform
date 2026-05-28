"""Platform API package for the RPG workspace."""
from platform_app import db as db  # noqa: F401 — re-export so `mock.patch("platform_app.db.connect")` works
