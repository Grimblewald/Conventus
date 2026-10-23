"""An abstract's authors and the affiliations they belong to.

Stored as structured data in `Abstract.author_data` (see `to_data`). What the
editor posts is validated by `clean`; what is already stored is read back
without the editor's limits (`from_data`), because data converted from older
text was never held to them and must still display.

Abstracts saved before that carry only text, one author per line:
`name|numbers|affiliation`. `numbers` is usually one affiliation number, but
an author with several carries them all ("1,2") with the affiliations' text
joined by ";" in the third field. `parse` reads that format, never raising on
a malformed row, and `to_legacy` still writes it so text search and the export
manifest keep working.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from markupsafe import Markup, escape

MAX_AUTHORS = 50
MAX_AFFILIATIONS = 30
MAX_NAME = 200
MAX_AFFILIATION = 500


@dataclass(frozen=True)
class Author:
    name: str
    affiliations: tuple[int, ...]   # 1-based numbers into AuthorList.affiliations


@dataclass(frozen=True)
class AuthorList:
    authors: tuple[Author, ...]
    affiliations: tuple[str, ...]   # affiliations[0] is number 1
    presenting: int                 # index into authors

    @property
    def presenting_author(self) -> Author | None:
        return self.authors[self.presenting] if self.authors else None

    def affiliations_of(self, author: Author) -> list[str]:
        return [self.affiliations[n - 1] for n in author.affiliations]


def parse(raw: str | None, presenting: int | None = 0) -> AuthorList:
    """Read stored author text into authors and numbered affiliations.

    Affiliations come out numbered 1..n in the order of the numbers they were
    stored under, with gaps closed, so whatever was typed the numbers printed
    against names always match the list printed under them.

    Spacing is collapsed, and affiliations that differ only in case are one
    affiliation under its first spelling: the editor treats them as the same,
    so reading them as two would leave an author an error they never made.
    """
    rows: list[tuple[str, list[tuple[int | None, str | None]]]] = []
    for line in (raw or "").strip().split("\n"):
        parts = line.split("|")
        name = _tidy(parts[0])
        if not name:
            continue
        numbers = [int(n) for n in re.findall(r"\d+", parts[1] if len(parts) > 1 else "")]
        text = _tidy(parts[2]) if len(parts) > 2 else ""
        if len(numbers) > 1:
            pieces = [p.strip() for p in text.split(";") if p.strip()]
            if len(pieces) == len(numbers):
                pairs = list(zip(numbers, pieces))
            else:
                # Cannot tell which text is which number: the whole text
                # belongs to the first, and the rest must be named elsewhere.
                pairs = [(numbers[0], text or None)] + [(n, None) for n in numbers[1:]]
        elif numbers:
            pairs = [(numbers[0], text or None)]
        else:
            pairs = [(None, text)] if text else []
        rows.append((name, pairs))

    # Each stored number takes the first text seen for it.
    named: dict[int, str] = {}
    for _, pairs in rows:
        for number, text in pairs:
            if number is not None and text and number not in named:
                named[number] = text

    ordered = [named[n] for n in sorted(named)]
    for _, pairs in rows:
        for number, text in pairs:
            if number is None and text:
                ordered.append(text)
    spelling: dict[str, str] = {}
    for text in ordered:
        spelling.setdefault(text.casefold(), text)
    position = {folded: i + 1 for i, folded in enumerate(spelling)}
    affiliations = tuple(spelling.values())

    authors = []
    for name, pairs in rows:
        nums: list[int] = []
        for number, text in pairs:
            resolved = text if number is None else named.get(number)
            if resolved and position[resolved.casefold()] not in nums:
                nums.append(position[resolved.casefold()])
        authors.append(Author(name, tuple(nums)))

    index = presenting or 0
    index = max(0, min(index, len(authors) - 1)) if authors else 0
    return AuthorList(tuple(authors), affiliations, index)


def _tidy(text) -> str:
    """One line of text: a stray newline would split a legacy row in two."""
    return re.sub(r"\s+", " ", text).strip() if isinstance(text, str) else ""


def clean(raw, *, strict: bool = True) -> tuple[AuthorList | None, list[str]]:
    """Validate author data as the editor posts it.

    *raw* is the JSON text (or the dict) `{"affiliations": [text, …],
    "authors": [{"name": text, "affiliations": [n, …]}, …], "presenting": i}`,
    numbers 1-based into `affiliations`. Returns the AuthorList, or None and
    the problems in words an author can act on.

    Affiliations no author ticked are dropped and the rest renumbered, so the
    numbers printed always run 1..n.

    `strict=False` checks only that the data can be read — its shape, and
    numbers that point somewhere — and not the editor's limits on counts,
    lengths, blanks and repeats.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return None, ["The author list could not be read. Please re-enter it."]
    if not isinstance(raw, dict):
        return None, ["The author list could not be read. Please re-enter it."]

    errors: list[str] = []
    affils_in = raw.get("affiliations")
    authors_in = raw.get("authors")
    if not isinstance(affils_in, list) or not isinstance(authors_in, list):
        return None, ["The author list could not be read. Please re-enter it."]

    affils = [_tidy(a) for a in affils_in]
    if strict:
        if len(affils) > MAX_AFFILIATIONS:
            errors.append(f"At most {MAX_AFFILIATIONS} affiliations.")
        seen: dict[str, int] = {}
        for n, text in enumerate(affils, 1):
            if not text:
                errors.append(f"Affiliation {n} is empty.")
            elif len(text) > MAX_AFFILIATION:
                errors.append(f"Affiliation {n} is longer than {MAX_AFFILIATION} characters.")
            elif text.casefold() in seen:
                errors.append(f"Affiliation {n} repeats affiliation {seen[text.casefold()]}.")
            else:
                seen[text.casefold()] = n

    if not authors_in:
        errors.append("Add at least one author.")
    if strict and len(authors_in) > MAX_AUTHORS:
        errors.append(f"At most {MAX_AUTHORS} authors.")
    people: list[tuple[str, list[int]]] = []
    for i, entry in enumerate(authors_in, 1):
        entry = entry if isinstance(entry, dict) else {}
        name = _tidy(entry.get("name"))
        if strict and not name:
            errors.append(f"Author {i} needs a name.")
        elif strict and len(name) > MAX_NAME:
            errors.append(f"Author {i}'s name is longer than {MAX_NAME} characters.")
        nums: list[int] = []
        for n in entry.get("affiliations") or []:
            if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= len(affils):
                errors.append(f"Author {i} is linked to an affiliation that does not exist.")
                break
            if n not in nums:
                nums.append(n)
        people.append((name, nums))

    presenting = raw.get("presenting")
    if (not isinstance(presenting, int) or isinstance(presenting, bool)
            or not 0 <= presenting < max(len(people), 1)):
        errors.append("Choose the presenting author.")

    if errors:
        return None, errors

    used = sorted({n for _, nums in people for n in nums})
    renumber = {old: new for new, old in enumerate(used, 1)}
    return AuthorList(
        tuple(Author(name, tuple(sorted(renumber[n] for n in nums)))
              for name, nums in people),
        tuple(affils[n - 1] for n in used),
        presenting,
    ), []


def to_data(authors: AuthorList) -> dict:
    """The stored form of an AuthorList (Abstract.author_data)."""
    return {
        "affiliations": list(authors.affiliations),
        "authors": [{"name": a.name, "affiliations": list(a.affiliations)}
                    for a in authors.authors],
        "presenting": authors.presenting,
    }


def from_data(data) -> AuthorList:
    """Read stored author data, without the editor's limits: a list converted
    from older text may exceed them and must still display. Data that cannot
    be read at all reads as no authors rather than raising on a page."""
    authors, _ = clean(data, strict=False)
    return authors or AuthorList((), (), 0)


def to_legacy(authors: AuthorList) -> str:
    """The pipe-delimited text form, kept for search and the export manifest.

    "|" and ";" are separators there, so inside a name or an affiliation they
    are replaced; the structured data keeps the text as typed.
    """
    def field(text: str) -> str:
        return text.replace("|", "/").replace(";", ",")

    lines = []
    for a in authors.authors:
        nums = ",".join(map(str, a.affiliations))
        texts = "; ".join(field(t) for t in authors.affiliations_of(a))
        lines.append(f"{field(a.name)}|{nums}|{texts}")
    return "\n".join(lines)


def from_form(form) -> tuple[AuthorList | None, list[str]]:
    """The authors an abstract form posted.

    The editor posts `author_data`. A page loaded before the editor existed
    posts the old `authors` text and `presenting_author_index`, read as before
    so nobody mid-edit across a deploy loses their submission.
    """
    if form.get("author_data") is not None:
        return clean(form.get("author_data"))
    text = (form.get("authors") or "").strip()
    try:
        presenting = int(form.get("presenting_author_index") or 0)
    except ValueError:
        presenting = 0
    authors = parse(text, presenting)
    return (authors, []) if authors.authors else (None, ["Add at least one author."])


def editor_initial(posted: str | None, abstract=None) -> dict:
    """What the author editor opens with: a resubmitted form's own data when
    it failed validation, else the abstract's, else one blank author."""
    if posted:
        try:
            data = json.loads(posted)
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    if abstract is not None and abstract.author_list.authors:
        return to_data(abstract.author_list)
    return {"affiliations": [""], "authors": [{"name": "", "affiliations": []}],
            "presenting": 0}


def to_html(authors: AuthorList) -> Markup:
    """Names with affiliation superscripts, then the numbered affiliations.

    Escaped: names and affiliations are typed by members.
    """
    if not authors.authors:
        return Markup("&mdash;")
    names = []
    for a in authors.authors:
        sup = (Markup("<sup>%s</sup>") % ",".join(map(str, a.affiliations))
               if a.affiliations else "")
        names.append(escape(a.name) + sup)
    out = Markup(", ").join(names)
    if authors.affiliations:
        out += Markup("<br>") + Markup(" &emsp; ").join(
            Markup("<sup>%d</sup>%s") % (i, text)
            for i, text in enumerate(authors.affiliations, 1))
    return out
