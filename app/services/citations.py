"""CrossRef DOI metadata fetching and reference formatting."""
from __future__ import annotations

import logging
from urllib.request import Request, urlopen
from urllib.error import URLError
import json

log = logging.getLogger(__name__)

CROSSREF_API = "https://api.crossref.org/works/{doi}"
_CACHE: dict[str, dict | None] = {}


def fetch_metadata(doi: str) -> dict | None:
    """Fetch bibliographic metadata for a DOI from CrossRef.

    Returns a dict with keys: title, authors, journal, year, volume,
    pages, doi, or None if the lookup fails.

    Cache is process-local — clears on restart.  Polite to CrossRef by
    using a short-lived in-memory cache.
    """
    doi = doi.strip()
    if doi in _CACHE:
        return _CACHE[doi]

    url = CROSSREF_API.format(doi=doi)
    req = Request(url, headers={"User-Agent": "Conventus/1.0 (mailto:noreply@example.org)"})
    try:
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        msg = data.get("message")
        if not msg or data.get("status") != "ok":
            _CACHE[doi] = None
            return None
        result = _parse_crossref(msg)
        _CACHE[doi] = result
        return result
    except (URLError, json.JSONDecodeError, OSError) as e:
        log.warning("CrossRef lookup failed for %s: %s", doi, e)
        _CACHE[doi] = None
        return None


def format_reference(ref_data: dict) -> str:
    """Format a reference dict into a human-readable citation string.

    Compatible with both CrossRef metadata dicts and the stored
    ``{"key": 1, "doi": "..."}`` reference dicts.

    ==== Format of CrossRef metadata dicts returned by fetch_metadata:
    ```
    {
      "title": "...,"
      "authors": "Kucsko G, Maurer PC, Yao NY, ...",
      "journal": "Nature",
      "year": "2013",
      "volume": "500",
      "pages": "54-58",
      "doi": "10.1038/nature12373",
    }
    ```
    """
    authors = ref_data.get("authors", "")
    title = ref_data.get("title", "")
    journal = ref_data.get("journal", "")
    year = ref_data.get("year", "")
    volume = ref_data.get("volume", "")
    pages = ref_data.get("pages", "")
    doi = ref_data.get("doi", "")
    
    parts = []
    if authors:
        parts.append(authors)
    if title:
        parts.append(title)
    if journal:
        jpart = journal
        if volume:
            jpart += f" {volume}"
            if pages:
                jpart += f", {pages}"
        if year:
            jpart += f" ({year})"
        parts.append(jpart)
    elif year:
        parts.append(year)
    if doi:
        parts.append(f"DOI: {doi}")
    return ". ".join(parts)


def _parse_crossref(msg: dict) -> dict:
    authors_list = msg.get("author", [])
    author_names = []
    for a in authors_list:
        given = a.get("given", "")
        family = a.get("family", "")
        if family:
            name = family
            if given:
                # Abbreviate given names: "G K" instead of "Gustav K"
                initials = " ".join(
                    p[0] for p in given.split() if p
                )
                name = f"{family} {initials}"
            author_names.append(name)

    container = msg.get("container-title", [])
    journal = container[0] if container else ""

    issued = msg.get("issued", {}) or msg.get("published-print", {}) or msg.get("created", {})
    date_parts = issued.get("date-parts", [[None]])[0]
    year = str(date_parts[0]) if date_parts and date_parts[0] else ""

    volume = msg.get("volume", "")
    page = msg.get("page", "")
    title = msg.get("title", [""])[0] if msg.get("title") else ""
    doi = msg.get("DOI", "")

    return {
        "authors": ", ".join(author_names) if author_names else "",
        "title": title,
        "journal": journal,
        "year": year,
        "volume": volume,
        "pages": page,
        "doi": doi,
    }
