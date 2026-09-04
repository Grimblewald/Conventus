"""CrossRef DOI metadata fetching and reference formatting."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError

log = logging.getLogger(__name__)

CROSSREF_API = "https://api.crossref.org/works/{doi}"
_CACHE: dict[str, dict | None] = {}
_CACHE_FILE: Path | None = None
_CACHE_DIRTY = False

# A DOI is a registrant prefix and a suffix, and that shape is the one thing
# every way of pasting one has in common: out of a journal's reference list
# ("DOI: 10.1126/x", "DOI (10.1126/x)"), out of the address bar
# ("https://doi.org/10.1126/x"), or out of an author's own numbered
# bibliography ("4. Smith et al. 10.1126/x").
_DOI_RE = re.compile(r"10\.\d{4,9}/\S+")

# Punctuation that ends the sentence a DOI was printed in rather than the DOI.
_TRAILING = ".,;:)]}>”’\"'"


def normalize_doi(raw: str) -> str:
    """Read the DOI out of however it was pasted.

    Searched for rather than stripped down to, because the forms a DOI arrives
    in are not a list anyone can finish: every prefix left off the list is a
    reference refused for being correct. What no DOI can be found in is handed
    back unchanged, so a caller can say what it read.
    """
    m = _DOI_RE.search(raw or "")
    if not m:
        return (raw or "").strip()
    return m.group(0).rstrip(_TRAILING)


def is_doi(raw: str) -> bool:
    """Whether *raw* holds a DOI at all — the only test for it in the codebase.

    Callers asking the question cheaply for themselves is how the validator
    came to refuse strings this module had already learned to accept.
    """
    return bool(_DOI_RE.search(raw or ""))


def _init_cache() -> Path:
    """Return path to persistent cache file, creating dir if needed."""
    global _CACHE_FILE
    if _CACHE_FILE is not None:
        return _CACHE_FILE
    try:
        from flask import current_app
        folder = Path(current_app.config["UPLOAD_FOLDER"]) / ".citation-cache"
    except RuntimeError:
        folder = Path(".citation-cache")
    folder.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE = folder / "cache.json"
    return _CACHE_FILE


def _load_cache():
    """Load persisted cache into memory on first access."""
    global _CACHE, _CACHE_DIRTY
    if _CACHE:
        return
    cache_file = _init_cache()
    if cache_file.exists():
        try:
            _CACHE = json.loads(cache_file.read_text())
            log.debug("Loaded %d cached citations", len(_CACHE))
        except (json.JSONDecodeError, OSError):
            _CACHE = {}
    _CACHE_DIRTY = False


def _save_cache():
    """Persist in-memory cache to disk if dirty."""
    global _CACHE_DIRTY
    if not _CACHE_DIRTY or _CACHE_FILE is None:
        return
    try:
        _CACHE_FILE.write_text(json.dumps(_CACHE, indent=2))
        _CACHE_DIRTY = False
    except OSError as e:
        log.warning("Failed to save citation cache: %s", e)


def fetch_metadata(doi: str) -> dict | None:
    """Fetch bibliographic metadata for a DOI from CrossRef.

    Returns a dict with keys: title, authors, journal, year, volume,
    pages, doi, or None if the lookup fails.

    Results are cached to disk and survive server restarts.
    """
    _load_cache()
    # Normalised here as well as on save, so an entry stored before this knew
    # about a prefix resolves on the next lookup instead of needing an edit.
    doi = normalize_doi(doi)
    if not doi:
        return None
    if doi in _CACHE:
        return _CACHE[doi]

    # Quoted, because the DOI reaches us as free text: a stray space or hash
    # otherwise builds a request the HTTP client refuses to send, and refuses
    # by raising. Slashes stay literal — a DOI always has one, and Crossref
    # wants it as a path separator.
    url = CROSSREF_API.format(doi=quote(doi, safe="/"))
    req = Request(url, headers={"User-Agent": "Conventus/1.0 (mailto:noreply@example.org)"})
    try:
        resp = urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        msg = data.get("message")
        if not msg or data.get("status") != "ok":
            return _remember(doi, None)
        return _remember(doi, _parse_crossref(msg))
    except HTTPError as e:
        # The DOI is wrong, not the network. Asking again gets the same answer.
        log.info("CrossRef has no record of %s (HTTP %s)", doi, e.code)
        return _remember(doi, None)
    except Exception as e:
        # A citation is an enrichment; a reference renders without one. Nothing
        # here may take down the page an author is trying to read, and the ways
        # a bad DOI can fail are not enumerable — a malformed one raises out of
        # the HTTP client rather than out of the socket.
        #
        # Not remembered: a timeout, a DNS failure or a malformed response says
        # nothing about the DOI, and caching it would make one bad minute
        # permanent for a reference that is perfectly good.
        log.warning("CrossRef lookup failed for %s: %s", doi, e)
        return None


def _remember(doi: str, result: dict | None) -> dict | None:
    """Cache a settled answer — one that asking again would not change."""
    global _CACHE_DIRTY

    _CACHE[doi] = result
    _CACHE_DIRTY = True
    _save_cache()
    return result


def _plain(text: str) -> str:
    """Crossref returns titles and journal names as HTML, so "Organic &amp;
    Biomolecular Chemistry" arrives with the entity intact and would otherwise
    be printed literally. Applied at format time rather than on fetch so
    already-cached entries are corrected too."""
    import html

    return html.unescape(text or "")


def format_reference(ref_data: dict) -> str:
    """Format a reference dict into a full human-readable citation string.

    Compatible with both CrossRef metadata dicts and the stored
    ``{"key": 1, "doi": "..."}`` reference dicts.

    Format::
      Kucsko G, Maurer PC, Yao NY, ... Nanometre-scale thermometry
      in a living cell. Nature 500, 54-58 (2013). DOI: 10.1038/nature12373
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
    return _plain(". ".join(parts))


def format_reference_compact(ref_data: dict) -> str:
    """Format a reference as a compact one-liner for LaTeX booklets.

    Format::
      Kucsko et al. Nature, 2013.
      (with the citation itself being a DOI hyperlink)

    Uses only first author surname + et al., journal name, and year.
    """
    authors = ref_data.get("authors", "")
    journal = ref_data.get("journal", "")
    year = ref_data.get("year", "")
    doi = ref_data.get("doi", "")

    first_author = ""
    if authors:
        first_author = authors.split(",")[0].strip()
        # Get surname (last word of first_author)
        parts = first_author.split()
        if parts:
            # Check if there are other authors after the first
            has_more = "," in authors
            first_author = parts[0]  # surname is first word in "FamilyName Initials"
            if has_more:
                first_author += " et al."

    parts = []
    if first_author:
        parts.append(first_author)
    if journal:
        parts.append(journal)
    if year:
        if parts:
            parts[-1] = f"{parts[-1]}, {year}"
        else:
            parts.append(year)
    if doi:
        if parts:
            parts.append(f"{doi}")
        else:
            parts.append(doi)
    return _plain(". ".join(parts))


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
