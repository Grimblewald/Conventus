"""Reading stored author text (services.authors) and showing it.

Every shape here has been seen in real submissions or is one a hand-typed row
can take. None of them may raise: a malformed row used to crash the booklet,
the abstract PDF, the export and every page that listed the authors.
"""
from __future__ import annotations

from app.services.authors import parse, to_html
from app.services.abstract_latex import latex_authors

A = "Department of Pharmacy, Example University, Adelaide"
B = "Future Industries Institute, Other University"


def test_one_affiliation_each():
    al = parse(f"Jane Doe|1|{A}\nJohn Roe|2|{B}\nAmy Poe|1|{A}", 1)
    assert al.affiliations == (A, B)
    assert [a.affiliations for a in al.authors] == [(1,), (2,), (1,)]
    assert al.presenting == 1
    assert al.presenting_author.name == "John Roe"


def test_several_affiliations_joined_by_semicolon():
    """The shape real submissions carry: "1,2" and "A; B"."""
    al = parse(f"Jane Doe|1|{A}\nJohn Roe|1,2|{A}; {B}\nAmy Poe|1|{A}")
    assert al.affiliations == (A, B)
    assert al.authors[1].affiliations == (1, 2)
    assert al.affiliations_of(al.authors[1]) == [A, B]


def test_several_numbers_named_elsewhere():
    """Text that cannot be split goes to the first number; the second takes
    its name from the row that defines it."""
    al = parse(f"Jane Doe|1,2|{A}\nJohn Roe|2|{B}")
    assert al.affiliations == (A, B)
    assert al.authors[0].affiliations == (1, 2)


def test_a_number_never_named_is_dropped():
    al = parse(f"Jane Doe|1,3|{A}")
    assert al.affiliations == (A,)
    assert al.authors[0].affiliations == (1,)


def test_gaps_close_and_order_follows_the_numbers():
    al = parse(f"Jane Doe|3|{B}\nJohn Roe|1|{A}")
    assert al.affiliations == (A, B)
    assert [a.affiliations for a in al.authors] == [(2,), (1,)]


def test_odd_index_text_does_not_raise():
    al = parse(f"Jane Doe|a|{A}\nJohn Roe| 1 ; 2 |{A}; {B}\nAmy Poe||")
    assert set(al.affiliations) == {A, B}
    assert al.authors[2].affiliations == ()


def test_the_same_affiliation_under_two_numbers_is_one():
    al = parse(f"Jane Doe|1|{A}\nJohn Roe|2|{A}")
    assert al.affiliations == (A,)
    assert [a.affiliations for a in al.authors] == [(1,), (1,)]


def test_empty_and_nameless():
    assert parse(None).authors == ()
    assert parse("").presenting_author is None
    assert [a.name for a in parse("|1|Uni\nJane Doe|1|Uni").authors] == ["Jane Doe"]


def test_presenting_is_clamped():
    assert parse("Jane Doe|1|Uni", 7).presenting == 0
    assert parse("Jane Doe|1|Uni", None).presenting == 0


def test_html_escapes_what_members_type():
    html = str(to_html(parse('<a href="https://x.example">x</a>|1|<b>Uni</b>')))
    assert "<a href" not in html and "<b>" not in html
    assert "&lt;a href=" in html and "&lt;b&gt;Uni&lt;/b&gt;" in html


def test_html_shape():
    html = str(to_html(parse(f"Jane Doe|1|{A}\nJohn Roe|1,2|{A}; {B}")))
    assert html.startswith("Jane Doe<sup>1</sup>, John Roe<sup>1,2</sup><br>")
    assert f"<sup>1</sup>{A}" in html and f"<sup>2</sup>{B}" in html
    assert str(to_html(parse(""))) == "&mdash;"


def test_latex_shape():
    names, affils = latex_authors(parse(f"Jane Doe|1|{A}\nJohn Roe|1,2|{A}; {B}", 1))
    assert names == ("Jane Doe\\textsuperscript{1}, "
                     "\\underline{John Roe\\textsuperscript{1,2}}")
    assert affils == (f"\\textsuperscript{{1}}{A}\\quad "
                      f"\\textsuperscript{{2}}{B}")


def test_presenting_author_property_lists_every_affiliation():
    from app.models import Abstract
    a = Abstract(authors=f"Jane Doe|1|{A}\nJohn Roe|1,2|{A}; {B}",
                 presenting_author_index=1)
    assert a.presenting_author == ("John Roe", f"{A}; {B}")
