"""Slug sanitisation. Shared between admin forms and seed loader."""
from __future__ import annotations

import re


def slugify(raw: str) -> str:
    s = (raw or "").strip().lower()
    s = re.sub(r"\s+", "-", s)
    s = re.sub(r"[^a-z0-9-]", "", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s
