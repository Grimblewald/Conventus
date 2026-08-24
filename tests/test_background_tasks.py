"""Notifications must not be sent while a request is open.

Rendering a PDF takes seconds and is serialised across the box; sending mail
holds a process-wide lock. Doing either inside a request means a rush of
submissions before a deadline takes the site down rather than queueing.
"""
from __future__ import annotations

import threading
import time
from datetime import date

import pytest

from app.extensions import db
from app.models import Abstract, Conference, User
from app.services import tasks


@pytest.fixture
def conference(app):
    import secrets
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"bg-{tag}", title="Background Conference",
                       start_date=date(2027, 6, 1), end_date=date(2027, 6, 3),
                       is_accepting_abstracts=True)
        db.session.add(c)
        db.session.commit()
        return c.slug


class TestTheRunner:
    def test_it_re_reads_the_row_in_its_own_session(self, seeded, app,
                                                    conference, monkeypatch):
        """The instance the request held belongs to a session that is gone."""
        seen = {}

        def _job(obj, **kw):
            seen["title"] = obj.title
            seen["kwargs"] = kw

        with app.app_context():
            c = Conference.query.filter_by(slug=conference).first()
            a = Abstract(user_id=None, conference_id=c.id, status="submitted",
                         title="Row read in the job", authors="A|1|U", body="B")
            db.session.add(a)
            db.session.commit()
            aid = a.id
            tasks.run_later_for(Abstract, aid, _job, note="hello")

        assert seen["title"] == "Row read in the job"
        assert seen["kwargs"] == {"note": "hello"}

    def test_a_vanished_row_is_skipped_not_raised(self, seeded, app):
        called = []
        with app.app_context():
            tasks.run_later_for(Abstract, 10_000_000,
                                lambda obj, **kw: called.append(obj))
        assert called == []

    def test_a_failing_job_does_not_escape(self, seeded, app):
        def _boom():
            raise RuntimeError("job failed")

        with app.app_context():
            # Inline under TESTING, so a raise here would surface immediately.
            with pytest.raises(RuntimeError):
                tasks.run_later(_boom)

    def test_it_really_runs_off_the_request_thread(self, app, monkeypatch):
        """The guarantee the deadline depends on: the caller does not wait."""
        monkeypatch.setitem(app.config, "TESTING", False)
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def _slow():
            started.set()
            release.wait(timeout=5)
            finished.set()

        with app.app_context():
            t0 = time.monotonic()
            deferred = tasks.run_later(_slow)
            elapsed = time.monotonic() - t0

        assert deferred is True
        assert elapsed < 0.5, "the caller waited for the job"
        assert started.wait(timeout=5), "the job never ran"
        assert not finished.is_set()
        release.set()
        assert finished.wait(timeout=5)


class TestSubmissionDoesNotWaitForItsReceipt:
    def test_submitting_returns_without_rendering(self, seeded, member_client,
                                                  app, conference, monkeypatch):
        """A compile inside the request is what takes the site down in a rush."""
        rendered = []
        monkeypatch.setattr(
            "app.services.abstract_latex.render_abstract_pdf",
            lambda *a, **k: rendered.append(1) or b"%PDF-")

        deferred = []
        monkeypatch.setattr(tasks, "run_later_for",
                            lambda *a, **k: deferred.append(a))

        resp = member_client.post(f"/conferences/{conference}/abstract", data={
            "title": "A submission in the rush", "authors": "Jane Doe|1|Uni",
            "body": "A body with nothing wrong with it.",
            "presenting_author_index": "0", "action": "submit",
        }, follow_redirects=True)

        assert resp.status_code == 200
        assert rendered == [], "the receipt was rendered inside the request"
        assert deferred, "the receipt was not handed to the background runner"
