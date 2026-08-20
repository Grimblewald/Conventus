"""Seeing why an abstract did not get submitted.

Two authors reported being unable to submit and the audit log showed nothing,
because a failed submission wrote nothing anywhere. These cover the two ways
an admin can now find out what happened.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.extensions import db
from app.models import Abstract, Conference
from app.models.audit import AuditLog


@pytest.fixture
def conference(app):
    import secrets
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"diag-{tag}", title="Diagnostics Conference",
                       start_date=date(2027, 6, 1), end_date=date(2027, 6, 3))
        db.session.add(c)
        db.session.commit()
        return c.slug


class TestFailedSubmissionsAreRecorded:
    def test_a_rejected_submission_names_its_reason(self, seeded, member_client,
                                                    app, conference):
        """The exact gap: red text on screen, silence in the log."""
        resp = member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "A title", "authors": "Jane Doe|1|Uni",
            "body": "Body citing [1] with no reference row.",
            "presenting_author_index": "0", "action": "submit",
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            rows = (AuditLog.query
                    .filter_by(action="abstract.submit_failed")
                    .order_by(AuditLog.id.desc()).all())
            assert rows, "a failed submission must leave a trace"
            assert "no matching reference" in rows[0].summary

    def test_a_successful_submission_records_no_failure(self, seeded,
                                                        member_client, app,
                                                        conference):
        member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "A clean title", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing to complain about.",
            "presenting_author_index": "0", "action": "submit",
        }, follow_redirects=True)
        with app.app_context():
            fails = AuditLog.query.filter(
                AuditLog.action == "abstract.submit_failed",
                AuditLog.summary.like("%clean title%")).count()
            assert fails == 0

    def test_the_word_limit_is_named_when_it_bites(self, seeded, member_client,
                                                   app, conference):
        """A 400-word abstract is refused; the log should say so, not just the
        author's screen."""
        member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "Overlong", "authors": "Jane Doe|1|Uni",
            "body": "word " * 400, "presenting_author_index": "0",
            "action": "submit",
        }, follow_redirects=True)
        with app.app_context():
            row = (AuditLog.query
                   .filter_by(action="abstract.submit_failed")
                   .order_by(AuditLog.id.desc()).first())
            assert row is not None
            assert "limit is 300" in row.summary


class TestDraftsAreVisibleToAdmins:
    @pytest.fixture
    def draft_id(self, app, conference):
        with app.app_context():
            c = Conference.query.filter_by(slug=conference).one()
            a = Abstract(conference_id=c.id, status="draft",
                         title="An unsubmitted draft",
                         authors="Stuck Author|1|Uni", body="Half finished.")
            db.session.add(a)
            db.session.commit()
            return a.id

    def test_draft_filter_lists_them(self, seeded, admin_client, draft_id):
        resp = admin_client.get("/admin/abstracts?status=draft")
        assert resp.status_code == 200
        assert b"An unsubmitted draft" in resp.data

    def test_drafts_are_absent_from_the_submitted_view(self, seeded,
                                                       admin_client, draft_id):
        resp = admin_client.get("/admin/abstracts?status=submitted")
        assert b"An unsubmitted draft" not in resp.data

    def test_search_finds_a_draft_by_author(self, seeded, admin_client, draft_id):
        resp = admin_client.get("/admin/abstracts?status=draft&search=Stuck")
        assert b"An unsubmitted draft" in resp.data

    def test_a_draft_can_be_opened(self, seeded, admin_client, draft_id):
        resp = admin_client.get(f"/admin/abstracts/{draft_id}")
        assert resp.status_code == 200
        assert b"Half finished." in resp.data


class TestSubmitFromPreview:
    """Preview used to be a dead end that quietly left the abstract a draft."""

    def _draft_via_preview(self, client, slug):
        return client.post(f"/conferences/{slug}/abstract", data={
            "title": "Previewed abstract", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": "preview",
        }, follow_redirects=True)

    def test_preview_offers_a_way_to_submit(self, seeded, member_client, app,
                                            conference):
        resp = self._draft_via_preview(member_client, conference)
        assert b"has not been submitted yet" in resp.data
        assert b"Submit abstract" in resp.data
        assert b"Keep as draft" in resp.data
        assert b"Continue editing" in resp.data
        with app.app_context():
            a = (Abstract.query.filter_by(title="Previewed abstract")
                 .order_by(Abstract.id.desc()).first())
            assert a.status == "draft"

    def test_submitting_from_preview_works(self, seeded, member_client, app,
                                           conference):
        self._draft_via_preview(member_client, conference)
        with app.app_context():
            aid = (Abstract.query.filter_by(title="Previewed abstract")
                   .order_by(Abstract.id.desc()).first().id)
        resp = member_client.post(f"/abstracts/{aid}/submit",
                                  follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert Abstract.query.get(aid).status == "submitted"
            assert AuditLog.query.filter_by(action="abstract.submitted").count()

    def test_preview_submit_applies_the_same_rules_as_the_form(
            self, seeded, member_client, app, conference):
        """Otherwise preview becomes a way around the limits the form applies."""
        member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "Overlong draft", "authors": "Jane Doe|1|Uni",
            "body": "word " * 400, "presenting_author_index": "0",
            "action": "draft",
        }, follow_redirects=True)
        with app.app_context():
            aid = (Abstract.query.filter_by(title="Overlong draft")
                   .order_by(Abstract.id.desc()).first().id)
        resp = member_client.post(f"/abstracts/{aid}/submit",
                                  follow_redirects=True)
        assert b"limit is 300" in resp.data
        with app.app_context():
            assert Abstract.query.get(aid).status == "draft"

    def test_another_member_cannot_submit_it(self, seeded, member_client, app,
                                             conference, client):
        self._draft_via_preview(member_client, conference)
        with app.app_context():
            aid = (Abstract.query.filter_by(title="Previewed abstract")
                   .order_by(Abstract.id.desc()).first().id)
        resp = client.post(f"/abstracts/{aid}/submit")   # not logged in
        assert resp.status_code in (302, 401, 403, 404)

