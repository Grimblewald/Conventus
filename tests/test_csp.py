"""CSP inline-script hash coverage.

The CSP header allows inline scripts by SHA256 hash, computed at startup
from the raw template files. That only works if (a) every executable
inline script is actually hashed, and (b) the rendered script bytes are
identical to the template bytes — i.e. no Jinja inside script blocks.
A violation of either is invisible until someone clicks the affected
button, so these tests guard both invariants.
"""
from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from app import _inline_script_hashes

TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates"
SCRIPT_RE = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.DOTALL)


def _executable_inline_scripts():
    """Yield (template, body) for every hashable inline script."""
    for f in sorted(TEMPLATES.rglob("*.html")):
        for m in SCRIPT_RE.finditer(f.read_text()):
            attrs, body = m.group(1), m.group(2)
            if re.search(r"\bsrc\s*=", attrs):
                continue
            if re.search(r"""\btype\s*=\s*["']?application/json""", attrs):
                continue
            if body.strip():
                yield f.relative_to(TEMPLATES), body


def test_inline_scripts_contain_no_jinja():
    offenders = []
    for tpl, body in _executable_inline_scripts():
        for expr in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", body, re.DOTALL):
            offenders.append(f"{tpl}: {expr.strip()[:70]}")
    assert not offenders, (
        "Jinja inside an inline <script> breaks its CSP hash "
        "(template bytes != rendered bytes). Move dynamic values to "
        "data-* attributes:\n" + "\n".join(offenders))


def test_every_executable_inline_script_is_hashed():
    hashes = set(_inline_script_hashes())
    missing = []
    for tpl, body in _executable_inline_scripts():
        digest = hashlib.sha256(body.encode()).digest()
        token = f"'sha256-{base64.b64encode(digest).decode()}'"
        if token not in hashes:
            missing.append(str(tpl))
    assert not missing, (
        "Inline scripts missing from CSP hashes (the browser will "
        "refuse to run them): " + ", ".join(missing))


def test_script_mentioning_json_in_body_is_still_hashed():
    # Regression: the hasher once skipped any script whose *body*
    # mentioned application/json (meant to exclude only
    # <script type="application/json"> data blocks) — which silently
    # broke the backup page.
    backup = (TEMPLATES / "admin" / "backup.html").read_text()
    m = SCRIPT_RE.search(backup)
    assert m and "application/json" in m.group(2), \
        "expected backup.html's script to mention application/json"
    digest = hashlib.sha256(m.group(2).encode()).digest()
    token = f"'sha256-{base64.b64encode(digest).decode()}'"
    assert token in set(_inline_script_hashes())
