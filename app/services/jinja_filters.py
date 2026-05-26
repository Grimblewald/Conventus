"""Jinja filter for the unified `target` scheme (see services/targets.py).

Registered on the app via `@app.template_filter("target_url")` in __init__.
Returns the resolved URL, or "#" if unresolvable.
"""
from __future__ import annotations

from .targets import resolve as _resolve


def target_url(target: str) -> str:
    return _resolve(target or "")
