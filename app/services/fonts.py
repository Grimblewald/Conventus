"""Web-safe font stacks for the heading/body/link/UI dropdowns.

We deliberately ship *no* external font loads — the CSP forbids `font-src`
beyond `'self'`. Admins pick a key here; the rendered CSS uses the value
verbatim as `font-family`.

Each entry has:
    (key, label, css_stack, sample_text)
"""
from __future__ import annotations

FONT_STACKS: dict[str, dict] = {
    "system_sans": {
        "label": "System sans-serif",
        "stack": ('system-ui, -apple-system, "Segoe UI", Roboto, '
                  '"Helvetica Neue", Arial, sans-serif'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "humanist_sans": {
        "label": "Humanist sans (Lucida-like)",
        "stack": ('"Lucida Grande", "Lucida Sans Unicode", "Lucida Sans", '
                  'Verdana, sans-serif'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "geometric_sans": {
        "label": "Geometric sans (Avant Garde / Futura)",
        "stack": ('"Avenir Next", Avenir, "Century Gothic", '
                  '"URW Gothic L", "Trebuchet MS", sans-serif'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "narrow_sans": {
        "label": "Narrow sans (Tahoma / Verdana)",
        "stack": ("Tahoma, Geneva, Verdana, sans-serif"),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "modern_serif": {
        "label": "Modern serif (Georgia)",
        "stack": ('Georgia, Cambria, "Times New Roman", Times, serif'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "transitional_serif": {
        "label": "Transitional serif (Baskerville)",
        "stack": ('"Iowan Old Style", "Palatino Linotype", Palatino, '
                  '"Book Antiqua", Georgia, serif'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "old_style_serif": {
        "label": "Old-style serif (Garamond)",
        "stack": ('"Hoefler Text", "Apple Garamond", "URW Garamond", '
                  'Garamond, Georgia, serif'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "slab_serif": {
        "label": "Slab serif (Rockwell)",
        "stack": ('Rockwell, "Rockwell Nova", "Roboto Slab", "Courier New", serif'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
    "monospace": {
        "label": "Monospace",
        "stack": ('ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, '
                  '"Liberation Mono", monospace'),
        "sample": "The quick brown fox jumps over the lazy dog.",
    },
}


def css_for(key: str) -> str:
    """Return the CSS font-family value for a key, falling back to system_sans."""
    return FONT_STACKS.get(key, FONT_STACKS["system_sans"])["stack"]


def all_choices() -> list[tuple[str, str]]:
    """List of (key, label) pairs for dropdowns."""
    return [(k, v["label"]) for k, v in FONT_STACKS.items()]
