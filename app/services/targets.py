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
    """For admin dropdowns — (target, human label).

    `EXTERNAL_CHOICE` comes last and is not a target in its own right: picking
    it means "use the URL box beside me", which `build_target` resolves.
    """
    base = [
        ("home",        "Home"),
        ("conferences", "Conferences (built-in)"),
        ("committee",   "Committee (built-in)"),
        ("contact",     "Contact (built-in)"),
        ("dashboard",   "Member dashboard"),
    ]
    for p in pages_iter:
        base.append((f"page:{p.slug}", f"Page · {p.title}"))
    base.append((EXTERNAL_CHOICE, "External link (enter a URL)"))
    return base


# The dropdown value that means "the URL box holds the answer". Not storable:
# `build_target` always turns it into a `url:` target or an error.
EXTERNAL_CHOICE = "__external__"

# What an external link may point at. `javascript:` and `data:` are absent
# deliberately — this text becomes an href on every page of the site, and the
# editor writing it is trusted with content, not with script.
_ALLOWED_SCHEMES = ("http", "https", "mailto")


def normalize_external(raw: str) -> str:
    """A typed URL as a storable `url:` target, or ValueError.

    A bare `example.org` is taken as https, the way people write addresses;
    anything with a scheme keeps it, provided the scheme is one we will emit.
    """
    url = (raw or "").strip()
    if not url:
        raise ValueError("Enter a URL for the external link.")
    if len(url) > 240:
        raise ValueError("That URL is too long (240 characters maximum).")
    parsed = urlparse(url)
    if not parsed.scheme:
        if url.startswith("//") or " " in url or "." not in url.split("/")[0]:
            raise ValueError(f"“{raw}” does not look like a URL.")
        url = "https://" + url
        parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise ValueError(
            f"Links may only use {', '.join(_ALLOWED_SCHEMES)} — “{raw}” does not.")
    if parsed.scheme in ("http", "https") and not parsed.netloc:
        raise ValueError(f"“{raw}” is missing a domain.")
    if parsed.scheme == "mailto" and "@" not in parsed.path:
        raise ValueError(f"“{raw}” is not a valid email address.")
    return f"url:{url}"


def build_target(choice: str, url_raw: str = "", *, fallback: str = "home") -> str:
    """The target to store from a picker's two fields.

    One function for nav and footer alike, so "how do I say where this points"
    has a single answer rather than one per editor.
    """
    choice = (choice or "").strip()
    if choice == EXTERNAL_CHOICE:
        return normalize_external(url_raw)
    if choice.startswith("url:"):
        # An unchanged external link round-tripping through the form.
        return normalize_external(choice[4:])
    return choice or fallback


def split_target(target: str) -> tuple[str, str]:
    """A stored target as (dropdown value, URL box value) for rendering."""
    target = (target or "").strip()
    if target.startswith("url:"):
        return EXTERNAL_CHOICE, target[4:]
    return target, ""
