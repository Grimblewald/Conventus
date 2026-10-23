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


def test_case_and_spacing_variants_are_one_affiliation():
    """The editor refuses these as repeats, so reading them as two would hand
    the author an error on their next edit for something they never typed."""
    al = parse("Jane  Doe|1|Uni  of X\nJohn Roe|2|uni of x\nAmy Poe|3|Other")
    assert al.affiliations == ("Uni of X", "Other")
    assert [a.affiliations for a in al.authors] == [(1,), (1,), (2,)]
    assert al.authors[0].name == "Jane Doe"
    assert clean(to_data(al))[1] == []


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


import json  # noqa: E402

import pytest  # noqa: E402

from app.services.authors import (  # noqa: E402
    clean, editor_initial, from_data, from_form, to_data, to_legacy)


def _posted(affils, authors, presenting=0):
    return json.dumps({"affiliations": affils, "presenting": presenting,
                       "authors": [{"name": n, "affiliations": a} for n, a in authors]})


class TestClean:
    def test_valid_data_comes_back_numbered(self):
        al, errors = clean(_posted([A, B], [("Jane Doe", [1]), ("John Roe", [2, 1])], 1))
        assert errors == []
        assert al.affiliations == (A, B)
        assert [a.affiliations for a in al.authors] == [(1,), (1, 2)]
        assert al.presenting == 1

    def test_unticked_affiliations_drop_and_the_rest_renumber(self):
        al, errors = clean(_posted([A, "Unused Uni", B], [("Jane Doe", [3])]))
        assert errors == []
        assert al.affiliations == (B,)
        assert al.authors[0].affiliations == (1,)

    def test_whitespace_and_newlines_collapse(self):
        al, _ = clean(_posted([f"  {A}\n  "], [("  Jane\nDoe ", [1])]))
        assert al.authors[0].name == "Jane Doe" and al.affiliations == (A,)

    @pytest.mark.parametrize("payload,message", [
        ("not json", "could not be read"),
        ("[]", "could not be read"),
        (_posted([A], []), "Add at least one author"),
        (_posted([A], [("", [1])]), "Author 1 needs a name"),
        (_posted(["", B], [("Jane Doe", [2])]), "Affiliation 1 is empty"),
        (_posted([A, A.upper()], [("Jane Doe", [1])]), "Affiliation 2 repeats affiliation 1"),
        (_posted([A], [("Jane Doe", [2])]), "does not exist"),
        (_posted([A], [("Jane Doe", ["1"])]), "does not exist"),
        (_posted([A], [("Jane Doe", [True])]), "does not exist"),
        (_posted([A], [("Jane Doe", [1])], 3), "presenting author"),
        (_posted([A], [("x" * 201, [1])]), "longer than 200"),
        (_posted(["x" * 501], [("Jane Doe", [1])]), "longer than 500"),
    ])
    def test_refusals_say_what_to_fix(self, payload, message):
        al, errors = clean(payload)
        assert al is None
        assert any(message in e for e in errors), errors


def test_stored_data_round_trips():
    al, _ = clean(_posted([A, B], [("Jane Doe", [1, 2]), ("John Roe", [])], 1))
    assert from_data(to_data(al)) == al
    assert from_data({"nonsense": True}).authors == ()


@pytest.mark.parametrize("data", [
    {"affiliations": [A], "presenting": 0,
     "authors": [{"name": f"Author {i}", "affiliations": [1]} for i in range(51)]},
    {"affiliations": ["x" * 600], "presenting": 0,
     "authors": [{"name": "Jane Doe", "affiliations": [1]}]},
    {"affiliations": [A, A.upper()], "presenting": 0,
     "authors": [{"name": "Jane Doe", "affiliations": [1, 2]}]},
    {"affiliations": [A], "presenting": 0,
     "authors": [{"name": "y" * 300, "affiliations": [1]}]},
])
def test_stored_data_over_the_editors_limits_still_reads(data):
    """Converted from older text, which had no limits. The editor refuses it;
    the pages must still show it."""
    assert clean(data)[0] is None
    assert from_data(data).authors


def test_unreadable_structure_falls_back_to_the_text():
    from app.models import Abstract
    a = Abstract(authors=f"Jane Doe|1|{A}", author_data={"nonsense": True})
    assert [x.name for x in a.author_list.authors] == ["Jane Doe"]


def test_editor_opens_with_stored_authors_over_the_limits():
    """Not blank: a member editing an old abstract keeps what is there and is
    told what to change when they save."""
    from app.models import Abstract
    a = Abstract(authors="", author_data={
        "affiliations": [A, A.upper()], "presenting": 0,
        "authors": [{"name": "Jane Doe", "affiliations": [1, 2]}]})
    assert editor_initial(None, a)["authors"] == [{"name": "Jane Doe",
                                                    "affiliations": [1, 2]}]


def test_legacy_text_reads_back_the_same():
    al, _ = clean(_posted([A, B], [("Jane Doe", [1, 2]), ("John Roe", [2])], 1))
    text = to_legacy(al)
    assert text == f"Jane Doe|1,2|{A}; {B}\nJohn Roe|2|{B}"
    assert parse(text, 1) == al


def test_legacy_text_never_carries_a_separator_from_the_data():
    al, _ = clean(_posted(["Uni A; Campus B", "C|D"], [("J|D", [1, 2])]))
    assert to_legacy(al) == "J/D|1,2|Uni A, Campus B; C/D"
    assert al.affiliations == ("Uni A; Campus B", "C|D"), "the data keeps it as typed"


def test_from_form_prefers_the_editor_and_falls_back_to_text():
    from werkzeug.datastructures import MultiDict
    al, errors = from_form(MultiDict({"author_data": _posted([A], [("Jane Doe", [1])])}))
    assert errors == [] and al.authors[0].name == "Jane Doe"
    al, errors = from_form(MultiDict({"authors": f"Jane Doe|1|{A}\nJohn Roe|1|{A}",
                                      "presenting_author_index": "1"}))
    assert errors == [] and al.presenting == 1
    assert from_form(MultiDict({}))[1] == ["Add at least one author."]


def test_editor_opens_with_what_failed_then_what_is_stored():
    from app.models import Abstract
    stored = Abstract(authors=f"Jane Doe|1|{A}")
    posted = _posted([B], [("John Roe", [1])])
    assert editor_initial(posted, stored)["authors"][0]["name"] == "John Roe"
    assert editor_initial(None, stored)["authors"][0]["name"] == "Jane Doe"
    assert editor_initial("not json", None)["authors"] == [{"name": "", "affiliations": []}]


def test_set_authors_keeps_every_column_in_step():
    from app.models import Abstract
    al, _ = clean(_posted([A, B], [("Jane Doe", [1]), ("John Roe", [1, 2])], 1))
    a = Abstract(authors="old|1|old")
    a.set_authors(al)
    assert a.author_list == al
    assert a.authors == to_legacy(al)
    assert a.presenting_author_index == 1


def test_presenting_author_property_lists_every_affiliation():
    from app.models import Abstract
    a = Abstract(authors=f"Jane Doe|1|{A}\nJohn Roe|1,2|{A}; {B}",
                 presenting_author_index=1)
    assert a.presenting_author == ("John Roe", f"{A}; {B}")


# --- the forms --------------------------------------------------------------

@pytest.fixture
def open_conference(app):
    import secrets
    from datetime import date, timedelta

    from app.extensions import db
    from app.models import Conference
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"authors-{tag}", title="Authors Conference",
                       start_date=date.today() + timedelta(days=120),
                       end_date=date.today() + timedelta(days=122),
                       abstract_deadline=date.today() + timedelta(days=30))
        db.session.add(c)
        db.session.commit()
        return c.id, c.slug


def _latest(app):
    from app.models import Abstract
    with app.app_context():
        return Abstract.query.order_by(Abstract.id.desc()).first()


class TestMemberForm:
    def test_the_editor_is_on_the_page(self, seeded, member_client, open_conference):
        page = member_client.get(f"/conferences/{open_conference[1]}/abstract")
        assert b"data-author-editor" in page.data
        assert b"author-editor.js" in page.data
        assert b'name="authors"' not in page.data

    def test_several_affiliations_are_stored_as_ticked(self, seeded, member_client,
                                                       app, open_conference):
        member_client.post(f"/conferences/{open_conference[1]}/abstract", data={
            "title": "Structured authors", "body": "Body.", "action": "draft",
            "author_data": _posted([A, B], [("Jane Doe", [1]), ("John Roe", [1, 2])], 1),
        }, follow_redirects=True)
        a = _latest(app)
        assert a.title == "Structured authors"
        assert a.author_data["authors"][1] == {"name": "John Roe", "affiliations": [1, 2]}
        assert a.presenting_author_index == 1
        assert a.authors == f"Jane Doe|1|{A}\nJohn Roe|1,2|{A}; {B}"

    def test_a_refusal_keeps_what_was_entered(self, seeded, member_client, app,
                                              open_conference):
        posted = _posted([A, ""], [("Jane Doe", [1, 2])])
        page = member_client.post(f"/conferences/{open_conference[1]}/abstract", data={
            "title": "Will be refused", "body": "Body.", "action": "draft",
            "author_data": posted}, follow_redirects=True)
        assert b"Affiliation 2 is empty." in page.data
        # The editor reopens with the author's own entry, not a blank one.
        assert b"Jane Doe" in page.data
        assert _latest(app) is None or _latest(app).title != "Will be refused"

    def test_editing_a_draft_opens_with_its_authors(self, seeded, member_client, app,
                                                    open_conference):
        member_client.post(f"/conferences/{open_conference[1]}/abstract", data={
            "title": "Reopen me", "body": "Body.", "action": "draft",
            "author_data": _posted([A], [("Jane Reopen", [1])]),
        }, follow_redirects=True)
        a = _latest(app)
        page = member_client.get(f"/conferences/{open_conference[1]}/abstract?edit={a.id}")
        initial = page.data.split(b"data-author-initial>")[1].split(b"</script>")[0]
        assert json.loads(initial)["authors"] == [{"name": "Jane Reopen", "affiliations": [1]}]

    def test_names_cannot_break_out_of_the_initial_data(self, seeded, member_client,
                                                        app, open_conference):
        member_client.post(f"/conferences/{open_conference[1]}/abstract", data={
            "title": "Escape attempt", "body": "Body.", "action": "draft",
            "author_data": _posted([A], [("</script><b>x</b>", [1])]),
        }, follow_redirects=True)
        a = _latest(app)
        page = member_client.get(f"/conferences/{open_conference[1]}/abstract?edit={a.id}")
        assert b"</script><b>x</b>" not in page.data


class TestAdminForm:
    def test_admin_edit_uses_the_editor(self, seeded, admin_client, app,
                                        open_conference):
        from app.extensions import db
        from app.models import Abstract
        with app.app_context():
            a = Abstract(conference_id=open_conference[0], status="submitted",
                         title="Admin edit", body="Body.",
                         authors=f"Jane Doe|1,2|{A}; {B}")
            db.session.add(a)
            db.session.commit()
            aid = a.id

        page = admin_client.get(f"/admin/abstracts/{aid}/edit")
        assert b"data-author-editor" in page.data
        assert b'name="authors"' not in page.data

        admin_client.post(f"/admin/abstracts/{aid}/edit", data={
            "title": "Admin edit", "body": "Body.", "status": "submitted",
            "author_data": _posted([A, B], [("Jane Doe", [2]), ("New Person", [1])], 1),
        }, follow_redirects=True)
        with app.app_context():
            stored = db.session.get(Abstract, aid)
            assert [a.name for a in stored.author_list.authors] == ["Jane Doe", "New Person"]
            assert stored.presenting_author == ("New Person", A)
