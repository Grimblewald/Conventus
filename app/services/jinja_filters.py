"""Jinja filter for the unified `target` scheme (see services/targets.py).

Registered on the app via `@app.template_filter("target_url")` in __init__.
Returns the resolved URL, or "#" if unresolvable.
"""
from __future__ import annotations

from .targets import resolve as _resolve


def target_url(target: str) -> str:
    return _resolve(target or "")


def format_amount(cents: int) -> str:
    """Render a minor-unit amount as decimal (e.g. 5000 -> '50.00')."""
    try:
        return f"{int(cents) / 100:.2f}"
    except (TypeError, ValueError):
        return "0.00"


def parse_cents(value: str) -> int:
    """Accept human input ('50' or '50.00') and return cents (5000)."""
    stripped = (value or "").strip()
    if not stripped:
        return 0
    try:
        dollars = float(stripped)
        return round(dollars * 100)
    except ValueError:
        try:
            return int(stripped)
        except ValueError:
            return 0
