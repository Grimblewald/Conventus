"""Tiny safe Markdown renderer.

We don't ship a Markdown dependency to keep the requirements set lean and
the CSP tight. This handles: headings (#–####), bold/italic, code spans,
links, lists, blockquotes, horizontal rules, and paragraphs. HTML in the
source is escaped — Markdown only, no raw HTML passthrough.
"""
from __future__ import annotations

import html
import re


_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    text = html.escape(text)
    text = _LINK_RE.sub(lambda m: _link(m.group(1), m.group(2)), text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    return text


def _link(label: str, target: str) -> str:
    # Only allow http(s), mailto, and root-relative links.
    safe = target if re.match(r"^(https?://|mailto:|/)", target) else "#"
    rel = ' rel="noopener noreferrer"' if safe.startswith("http") else ""
    return f'<a href="{html.escape(safe)}"{rel}>{label}</a>'


def render(src: str) -> str:
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    lines = src.split("\n")
    out: list[str] = []
    i = 0
    in_ul = False
    in_ol = False
    in_quote = False

    def close_lists():
        nonlocal in_ul, in_ol
        if in_ul:
            out.append("</ul>")
            in_ul = False
        if in_ol:
            out.append("</ol>")
            in_ol = False

    def close_quote():
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    para: list[str] = []

    def flush_para():
        if para:
            out.append("<p>" + " ".join(_inline(token) for token in para) + "</p>")
            para.clear()

    while i < len(lines):
        ln = lines[i]
        stripped = ln.strip()

        # blank line: flush paragraph and lists
        if not stripped:
            flush_para()
            close_lists()
            close_quote()
            i += 1
            continue

        # horizontal rule
        if re.match(r"^-{3,}$", stripped):
            flush_para()
            close_lists()
            close_quote()
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"^(#{1,4})\s+(.+)$", stripped)
        if m:
            flush_para()
            close_lists()
            close_quote()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue

        # blockquote
        if stripped.startswith(">"):
            flush_para()
            close_lists()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append("<p>" + _inline(stripped.lstrip("> ").rstrip()) + "</p>")
            i += 1
            continue
        else:
            close_quote()

        # unordered list
        if re.match(r"^[-*]\s+", stripped):
            flush_para()
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if not in_ul:
                out.append("<ul>")
                in_ul = True
            out.append("<li>" + _inline(re.sub(r"^[-*]\s+", "", stripped)) + "</li>")
            i += 1
            continue

        # ordered list
        if re.match(r"^\d+\.\s+", stripped):
            flush_para()
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if not in_ol:
                out.append("<ol>")
                in_ol = True
            out.append("<li>" + _inline(re.sub(r"^\d+\.\s+", "", stripped)) + "</li>")
            i += 1
            continue

        # otherwise — paragraph text
        close_lists()
        para.append(stripped)
        i += 1

    flush_para()
    close_lists()
    close_quote()
    return "\n".join(out)
