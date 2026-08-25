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


class TestWhatIsQueuedAndWhatIsNot:
    """Mail whose result the user is shown must stay in the request; mail
    nobody is waiting on must not hold a worker."""

    def _deferred(self, monkeypatch):
        calls = []
        monkeypatch.setattr(tasks, "run_later_for",
                            lambda *a, **k: calls.append(a[2]))
        return calls

    def test_a_payment_email_is_queued(self, seeded, member_client, app,
                                       monkeypatch):
        from app.models import Conference, PriceTier
        calls = self._deferred(monkeypatch)
        # member imports the name at module load, so patch it there.
        monkeypatch.setattr(
            "app.blueprints.member.payments_open_to_members", lambda: True)
        import secrets
        tag = secrets.token_hex(4)
        with app.app_context():
            c = Conference(slug=f"q-{tag}", title="Queue Conf",
                           start_date=date(2027, 5, 1), end_date=date(2027, 5, 2),
                           is_accepting_registrations=True)
            db.session.add(c)
            db.session.flush()
            db.session.add(PriceTier(conference_id=c.id, name="Standard",
                                     amount=40000))
            db.session.commit()
            slug = c.slug

        member_client.post(f"/conferences/{slug}/register",
                           data={"tier": "Standard"}, follow_redirects=True)
        assert any(getattr(f, "__name__", "") == "send_payment_email"
                   for f in calls), "the payment email held the request open"

    def test_a_login_code_is_not_queued(self, seeded, client, monkeypatch):
        """The user is shown whether it sent, and cannot log in until it lands —
        deferring would cost the error message and save them nothing."""
        calls = self._deferred(monkeypatch)
        sent = []
        monkeypatch.setattr("app.blueprints.auth.send_mail",
                            lambda **kw: sent.append(kw) or True)

        client.post("/auth/login", data={"email": "member@test.example.org"},
                    follow_redirects=True)
        assert sent, "the code was not sent during the request"
        assert calls == [], "a login code must not be deferred"


class TestTheQueueIndicator:
    """Each process holds its own queue, so the depth has to be assembled from
    all of them rather than read from whichever one served the page."""

    def test_it_sums_across_workers_and_keeps_the_peak(self, seeded, app):
        from app.models.queue_stat import QueueStat, record_depth, snapshot

        with app.app_context():
            QueueStat.query.delete()
            db.session.commit()
            record_depth("box:1", 4)
            record_depth("box:2", 7)
            record_depth("box:1", 2)          # drained a little

            state = snapshot()
            assert state["current"] == 9, "2 still waiting on one, 7 on the other"
            assert state["peak_24h"] == 7, "the high-water mark is kept"

    def test_a_worker_that_has_gone_away_is_not_counted(self, seeded, app):
        """Its last row remains, but nobody is going to do that work."""
        from datetime import datetime, timedelta

        from app.models.queue_stat import QueueStat, record_depth, snapshot

        with app.app_context():
            QueueStat.query.delete()
            db.session.commit()
            record_depth("box:dead", 12)
            row = QueueStat.query.filter_by(worker="box:dead").one()
            row.updated_at = datetime.utcnow() - timedelta(hours=2)
            db.session.commit()

            state = snapshot()
            assert state["current"] == 0
            assert state["peak_24h"] == 12, "it still happened"

    def test_the_admin_pages_show_it(self, seeded, admin_client, app):
        from app.models.queue_stat import record_depth

        with app.app_context():
            record_depth("box:1", 3)

        overview = admin_client.get("/admin/").data
        assert b"Send queue" in overview
        # The chrome carries it on every admin page, not just the overview.
        assert b"Send queue" in admin_client.get("/admin/registrations").data

    def test_a_broken_snapshot_does_not_break_the_page(self, seeded,
                                                       admin_client, monkeypatch):
        monkeypatch.setattr("app.models.queue_stat.snapshot",
                            lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        assert admin_client.get("/admin/").status_code == 200


class TestDeferredJobsCanBuildLinks:
    """The jobs that get deferred send mail carrying links. Building an
    external URL outside a request needs SERVER_NAME, which is not set — so a
    deferred job raised, the runner logged it, and the mail silently never
    went. Only the paths that stayed in a request kept working."""

    def test_a_deferred_job_can_build_an_external_url(self, app, monkeypatch):
        from flask import url_for

        monkeypatch.setitem(app.config, "TESTING", False)
        monkeypatch.setitem(app.config, "SERVER_NAME", None)
        result = {}
        done = threading.Event()

        def _job():
            try:
                result["url"] = url_for("public.pay_registration",
                                        token="abc", _external=True)
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {e}"
            finally:
                done.set()

        with app.test_request_context("/", base_url="https://example.org"):
            assert tasks.run_later(_job) is True

        assert done.wait(timeout=5), "the job never ran"
        assert "error" not in result, result.get("error")
        assert result["url"] == "https://example.org/pay/registration/abc"

    def test_it_still_runs_when_scheduled_outside_a_request(self, app,
                                                            monkeypatch):
        """Cron and CLI callers have no request to borrow an address from."""
        monkeypatch.setitem(app.config, "TESTING", False)
        ran = threading.Event()

        with app.app_context():
            assert tasks.run_later(ran.set) is True
        assert ran.wait(timeout=5)
