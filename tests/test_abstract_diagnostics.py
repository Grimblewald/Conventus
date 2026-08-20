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
