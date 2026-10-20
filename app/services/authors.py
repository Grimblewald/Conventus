"""An abstract's authors and the affiliations they belong to.

Stored as text, one author per line: `name|numbers|affiliation`. `numbers`
is usually one affiliation number, but an author with several carries them
all ("1,2") with the affiliations' text joined by ";" in the third field.
Everything that shows authors — the web pages, the LaTeX, the filters — reads
them through `parse`, so that format has one reading and a malformed row
renders as best it can instead of taking the page down.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from markupsafe import Markup, escape


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
    """
    rows: list[tuple[str, list[tuple[int | None, str | None]]]] = []
    for line in (raw or "").strip().split("\n"):
        parts = line.split("|")
        name = parts[0].strip()
        if not name:
            continue
        numbers = [int(n) for n in re.findall(r"\d+", parts[1] if len(parts) > 1 else "")]
        text = parts[2].strip() if len(parts) > 2 else ""
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
            if number is None and text and text not in ordered:
                ordered.append(text)
    position = {text: i + 1 for i, text in enumerate(dict.fromkeys(ordered))}
    affiliations = tuple(position)

    authors = []
    for name, pairs in rows:
        nums: list[int] = []
        for number, text in pairs:
            resolved = text if number is None else named.get(number)
            if resolved and position[resolved] not in nums:
                nums.append(position[resolved])
        authors.append(Author(name, tuple(nums)))

    index = presenting or 0
    index = max(0, min(index, len(authors) - 1)) if authors else 0
    return AuthorList(tuple(authors), affiliations, index)


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
