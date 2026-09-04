"""The two ways to submit an abstract reach the same verdict.

An abstract can be submitted from the form or from the Submit button on the
preview page. The form rebuilds its references from the boxes as it goes; the
preview page submits what is already stored. Both call one validator, so the
rules cannot drift — but for a while the two handed it differently prepared
input, and an author whose DOIs were written the way a journal prints them was
accepted by one button and refused by the other.

Nothing compared the two paths, so a route that skipped a step looked exactly
like a route that did not need it.
"""
from __future__ import annotations

import secrets
from datetime import date, timedelta

import pytest

from app.extensions import db
from app.models import Abstract, Conference

# As printed in a reference list, which is where an author copies it from.
LABELLED = "DOI: 10.1126/sciadv.ade5079"
BARE = "10.1126/sciadv.ade5079"

BODY = ("Vesicles were prepared and characterised, following the approach "
        "of earlier work [1], and evaluated in a disease model.")


@pytest.fixture
def conference(app):
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"paths-{tag}", title="Both Paths Conference",
                       start_date=date.today() + timedelta(days=120),
                       end_date=date.today() + timedelta(days=122),
                       abstract_deadline=date.today() + timedelta(days=30))
        db.session.add(c)
        db.session.commit()
        return c.slug


def _save_draft(client, slug, doi):
    """Save a draft through the form, the way the author would."""
    resp = client.post(f"/conferences/{slug}/abstract", data={
        "title": "Artificial vesicles for oral delivery",
        "authors": "Jane Doe|1|Uni",
        "body": BODY,
        "ref_doi[]": doi,
        "presenting_author_index": "0", "action": "draft",
    }, follow_redirects=True)
    assert resp.status_code == 200
    return resp


class TestBothSubmitPathsAgree:
    @pytest.mark.parametrize("doi", [BARE, LABELLED])
    def test_the_preview_button_submits_what_the_form_would_accept(
            self, seeded, member_client, app, conference, doi):
        _save_draft(member_client, conference, doi)

        with app.app_context():
            a = Abstract.query.filter_by(deleted_at=None).order_by(
                Abstract.id.desc()).first()
            assert a.status == "draft"
            aid = a.id

        resp = member_client.post(f"/abstracts/{aid}/submit",
                                  follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            assert Abstract.query.get(aid).status == "submitted", (
                "the preview page refused an abstract the form had accepted")

    def test_a_labelled_doi_is_stored_as_the_doi_itself(
            self, seeded, member_client, app, conference):
        _save_draft(member_client, conference, LABELLED)

        with app.app_context():
            a = Abstract.query.filter_by(deleted_at=None).order_by(
                Abstract.id.desc()).first()
            assert a.references == [{"key": 1, "doi": BARE}]

    def test_a_draft_stored_before_the_parser_knew_the_form_still_submits(
            self, seeded, member_client, app, conference):
        """The reason to normalise on read and not only on save.

        Drafts saved by an earlier version hold whatever it failed to strip.
        Their authors are not going to be asked to re-type them.
        """
        _save_draft(member_client, conference, BARE)

        with app.app_context():
            a = Abstract.query.filter_by(deleted_at=None).order_by(
                Abstract.id.desc()).first()
            aid = a.id
            a.references = [{"key": 1, "doi": "DOI (10.1126/sciadv.ade5079)"}]
            db.session.commit()

        resp = member_client.post(f"/abstracts/{aid}/submit",
                                  follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            assert Abstract.query.get(aid).status == "submitted"

    def test_a_reference_holding_no_doi_is_still_refused(
            self, seeded, member_client, app, conference):
        """Both paths, and for the same reason — the tolerance has a floor."""
        _save_draft(member_client, conference, "see the paper")

        with app.app_context():
            a = Abstract.query.filter_by(deleted_at=None).order_by(
                Abstract.id.desc()).first()
            aid = a.id

        resp = member_client.post(f"/abstracts/{aid}/submit",
                                  follow_redirects=True)
        body = resp.get_data(as_text=True)
        assert "no DOI found" in body

        with app.app_context():
            assert Abstract.query.get(aid).status == "draft"
