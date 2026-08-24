"""Seeing why an abstract did not get submitted.

Two authors reported being unable to submit and the audit log showed nothing,
because a failed submission wrote nothing anywhere. These cover the two ways
an admin can now find out what happened.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.extensions import db
from app.models import Abstract, Conference, User
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


@pytest.fixture
def capped(app):
    """A conference that allows exactly one abstract per author."""
    import secrets
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"capped-{tag}", title="One Each",
                       start_date=date(2027, 6, 1), end_date=date(2027, 6, 3),
                       max_abstracts_per_user=1)
        db.session.add(c)
        db.session.commit()
        return c.slug


class TestPerUserLimitSurvivesTheDraftRoute:
    """Drafts don't count towards the cap, so the cap has to be applied where
    a draft turns into a submission — on every route that can do that."""

    def _draft(self, client, slug, title, action="draft"):
        return client.post(f"/conferences/{slug}/abstract", data={
            "title": title, "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": action,
        }, follow_redirects=True)

    def _ids(self, app, *titles):
        with app.app_context():
            return [Abstract.query.filter_by(title=t)
                    .order_by(Abstract.id.desc()).first().id for t in titles]

    def test_preview_submit_cannot_exceed_the_limit(self, seeded, member_client,
                                                    app, capped):
        self._draft(member_client, capped, "First one", action="preview")
        self._draft(member_client, capped, "Second one", action="preview")
        first, second = self._ids(app, "First one", "Second one")

        member_client.post(f"/abstracts/{first}/submit", follow_redirects=True)
        resp = member_client.post(f"/abstracts/{second}/submit",
                                  follow_redirects=True)

        assert b"reached the limit" in resp.data
        with app.app_context():
            assert Abstract.query.get(first).status == "submitted"
            assert Abstract.query.get(second).status == "draft"

    def test_editing_a_draft_cannot_exceed_the_limit(self, seeded, member_client,
                                                     app, capped):
        """The form's own check was skipped whenever edit_id was set, and the
        dashboard's Edit link always sets it."""
        self._draft(member_client, capped, "Form first")
        self._draft(member_client, capped, "Form second")
        first, second = self._ids(app, "Form first", "Form second")

        member_client.post(f"/abstracts/{first}/submit", follow_redirects=True)
        resp = member_client.post(f"/conferences/{capped}/abstract", data={
            "title": "Form second", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": "submit",
            "edit_id": str(second),
        }, follow_redirects=True)

        assert b"reached the limit" in resp.data
        with app.app_context():
            assert Abstract.query.get(second).status == "draft"

    def test_an_abstract_sent_back_for_revision_can_be_resubmitted(
            self, seeded, member_client, app, capped):
        """It already occupies the author's one slot; it must not block itself."""
        self._draft(member_client, capped, "Needs work", action="preview")
        (aid,) = self._ids(app, "Needs work")
        with app.app_context():
            a = Abstract.query.get(aid)
            a.status = "revise"
            db.session.commit()

        resp = member_client.post(f"/abstracts/{aid}/submit",
                                  follow_redirects=True)

        assert b"reached the limit" not in resp.data
        with app.app_context():
            assert Abstract.query.get(aid).status == "submitted"



class TestEditingAfterSubmission:
    """Authors spot their own errors after sending, so the deadline is the
    cut-off rather than the act of submitting."""

    def _abstract(self, app, slug, status):
        with app.app_context():
            c = Conference.query.filter_by(slug=slug).first()
            u = User.query.filter_by(email="member@test.example.org").first()
            a = Abstract(user_id=u.id, conference_id=c.id, status=status,
                         title="A submitted piece of work",
                         authors="Jane Doe|1|Uni",
                         body="A body with nothing wrong with it.",
                         presenting_author_index=0)
            db.session.add(a)
            db.session.commit()
            return a.id

    def test_a_submitted_abstract_can_still_be_edited(self, seeded,
                                                      member_client, app, conference):
        aid = self._abstract(app, conference, "submitted")
        resp = member_client.get(f"/conferences/{conference}/abstract?edit={aid}")
        assert resp.status_code == 200
        assert b"already been submitted" in resp.data

    def test_saving_it_keeps_it_submitted(self, seeded, member_client, app, conference):
        aid = self._abstract(app, conference, "submitted")
        member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "A corrected piece of work", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": "submit",
            "edit_id": str(aid),
        }, follow_redirects=True)
        with app.app_context():
            a = Abstract.query.get(aid)
            assert a.status == "submitted"
            assert a.title == "A corrected piece of work"

    def test_an_accepted_abstract_cannot_be_edited(self, seeded, member_client,
                                                   app, conference):
        """Saving one used to set it back to 'submitted', discarding the
        decision while leaving decided_by pointing at whoever made it."""
        aid = self._abstract(app, conference, "accepted")

        resp = member_client.get(f"/conferences/{conference}/abstract?edit={aid}",
                                 follow_redirects=True)
        assert b"no longer be edited" in resp.data

        member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "Sneaky rewrite", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": "submit",
            "edit_id": str(aid),
        }, follow_redirects=True)
        with app.app_context():
            a = Abstract.query.get(aid)
            assert a.status == "accepted"
            assert a.title == "A submitted piece of work"

    def test_editing_closes_with_submissions(self, seeded, member_client, app,
                                             conference):
        aid = self._abstract(app, conference, "submitted")
        with app.app_context():
            c = Conference.query.filter_by(slug=conference).first()
            c.is_accepting_abstracts = False
            db.session.commit()
            assert Abstract.query.get(aid).is_editable is False

    def test_previewing_a_submitted_abstract_does_not_resubmit_it(
            self, seeded, member_client, app, conference):
        """Preview and Submit are separate actions on the same form."""
        aid = self._abstract(app, conference, "submitted")
        resp = member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "A submitted piece of work", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": "preview",
            "edit_id": str(aid),
        }, follow_redirects=False)
        assert resp.status_code == 302
        assert f"/abstracts/{aid}/preview" in resp.headers["Location"]
        with app.app_context():
            assert Abstract.query.get(aid).status == "submitted"

    def test_every_rendered_action_button_can_set_the_action(
            self, seeded, member_client, app, conference):
        """A button whose handler is missing submits under the form's default,
        so Preview would resubmit."""
        import re

        aid = self._abstract(app, conference, "submitted")
        page = member_client.get(
            f"/conferences/{conference}/abstract?edit={aid}").data.decode()
        rendered = set(re.findall(r'<button[^>]*id="(btn-[a-z]+)"', page))
        wired = set(re.findall(r'\["(btn-[a-z]+)",', page))
        assert rendered, "expected action buttons on the page"
        assert rendered <= wired, f"no handler for {rendered - wired}"

    def test_editing_keeps_the_existing_figure(self, seeded, member_client,
                                                app, conference):
        """An untouched file input posts an empty filename, and must not be
        read as "remove the figure"."""
        from pathlib import Path

        from flask import current_app

        aid = self._abstract(app, conference, "submitted")
        with app.app_context():
            folder = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "fig_keepme.png").write_bytes(b"not really a png")
            a = Abstract.query.get(aid)
            a.figure_filename = "abstracts/fig_keepme.png"
            db.session.commit()

        member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "A submitted piece of work", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": "submit",
            "edit_id": str(aid),
        }, follow_redirects=True)

        with app.app_context():
            assert Abstract.query.get(aid).figure_filename == \
                "abstracts/fig_keepme.png"
            folder = Path(current_app.config["UPLOAD_FOLDER"]) / "abstracts"
            assert (folder / "fig_keepme.png").exists(), "file deleted from disk"

    def test_the_edit_form_shows_the_figure_that_is_attached(
            self, seeded, member_client, app, conference):
        """Otherwise the author sees an empty file input and cannot tell
        whether their figure is still there."""
        aid = self._abstract(app, conference, "submitted")
        with app.app_context():
            a = Abstract.query.get(aid)
            a.figure_filename = "abstracts/fig_keepme.png"
            db.session.commit()

        page = member_client.get(
            f"/conferences/{conference}/abstract?edit={aid}").data
        assert b"A figure is attached" in page
        assert b"remove_figure" in page


class TestDiscardingADraft:
    """A draft has been sent nowhere and read by nobody, so the emailed code
    that guards a real submission is only an obstacle."""

    def _draft(self, app, conference, status="draft"):
        with app.app_context():
            c = Conference.query.filter_by(slug=conference).first()
            u = User.query.filter_by(email="member@test.example.org").first()
            a = Abstract(user_id=u.id, conference_id=c.id, status=status,
                         title="Half-written thing", authors="Jane Doe|1|Uni",
                         body="Not finished.", presenting_author_index=0)
            db.session.add(a)
            db.session.commit()
            return a.id

    def test_a_draft_goes_without_a_code(self, seeded, member_client, app,
                                         conference):
        aid = self._draft(app, conference)
        resp = member_client.post(f"/abstracts/{aid}/delete-draft",
                                  follow_redirects=True)
        assert b"Discarded draft" in resp.data
        with app.app_context():
            assert Abstract.query.get(aid).deleted_at is not None

    def test_a_submitted_abstract_still_needs_one(self, seeded, member_client,
                                                  app, conference):
        aid = self._draft(app, conference, status="submitted")
        member_client.post(f"/abstracts/{aid}/delete-draft",
                           follow_redirects=True)
        with app.app_context():
            assert Abstract.query.get(aid).deleted_at is None

    def test_it_is_not_a_way_into_someone_else_s_drafts(self, seeded, client,
                                                        app, conference):
        aid = self._draft(app, conference)
        assert client.post(
            f"/abstracts/{aid}/delete-draft").status_code in (302, 401, 403)
        with app.app_context():
            assert Abstract.query.get(aid).deleted_at is None
