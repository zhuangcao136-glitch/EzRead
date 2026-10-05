"""Small stateless application utilities."""
from __future__ import annotations
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()
