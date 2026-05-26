"""URL builder for the unified `target` scheme used by nav, footer, and
in-page link pickers.

A target is one of:

    "home"                    →  url_for("public.home")
    "conferences"             →  url_for("public.conferences")
    "committee"               →  url_for("public.committee")
    "contact"                 →  url_for("public.contact")
    "dashboard"               →  url_for("member.dashboard")
    "page:<slug>"             →  url_for("public.page", slug=<slug>)
    "url:<absolute_url>"      →  <absolute_url>

Anything else falls back to "#".
"""
from __future__ import annotations

from urllib.parse import urlparse

from flask import url_for


BUILT_IN_TARGETS = {
    "home":         lambda: url_for("public.home"),
    "conferences":  lambda: url_for("public.conferences"),
    "committee":    lambda: url_for("public.committee"),
    "contact":      lambda: url_for("public.contact"),
    "dashboard":    lambda: url_for("member.dashboard"),
}


def resolve(target: str) -> str:
    if not target:
        return "#"
    if target in BUILT_IN_TARGETS:
        return BUILT_IN_TARGETS[target]()
    if target.startswith("page:"):
        slug = target.split(":", 1)[1].strip()
        if slug:
            return url_for("public.page", slug=slug)
    if target.startswith("url:"):
        raw = target.split(":", 1)[1].strip()
        parsed = urlparse(raw)
        if parsed.scheme in ("http", "https", "mailto"):
            return raw
    return "#"


def label_choices(pages_iter) -> list[tuple[str, str]]:
    """For admin dropdowns — (target, human label)."""
    base = [
        ("home",        "Home"),
        ("conferences", "Conferences (built-in)"),
        ("committee",   "Committee (built-in)"),
        ("contact",     "Contact (built-in)"),
        ("dashboard",   "Member dashboard"),
    ]
    for p in pages_iter:
        base.append((f"page:{p.slug}", f"Page · {p.title}"))
    return base
