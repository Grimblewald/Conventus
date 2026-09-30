"""Which accepted abstracts a conference page shows, and oral/poster decisions.

Each speaker status is set per conference to one of three levels: listed in
the speaker grid (which implies its abstract pages are public), pages public
but unlisted, or hidden. Posters start hidden; everything else starts listed.
"""
from __future__ import annotations

import secrets
from datetime import date

import pytest

from app.extensions import db
from app.models import Abstract, Conference, ReviewAssignment, User


@pytest.fixture
def conf(app):
    """A conference with one abstract per speaker status. Titles carry a tag
    so a page can be searched for exactly these rows."""
    tag = secrets.token_hex(4)
    with app.app_context():
        c = Conference(slug=f"vis-{tag}", title="Visibility Conference",
                       start_date=date(2027, 8, 1), end_date=date(2027, 8, 3))
        db.session.add(c)
        db.session.flush()
        ids = {}
        for status in ("plenary", "keynote", "invited",
                       "accepted_oral", "accepted_poster", "rejected"):
            a = Abstract(conference_id=c.id, status=status,
                         title=f"{status} talk {tag}",
                         authors=f"{status.title()} Person|1|Uni",
                         body="Body.")
            db.session.add(a)
            db.session.flush()
            ids[status] = a.id
        db.session.commit()
        return {"id": c.id, "slug": c.slug, "tag": tag, "abstracts": ids}


def _grid(client, conf) -> str:
    resp = client.get(f"/conferences/{conf['slug']}")
    assert resp.status_code == 200
    return resp.data.decode()


def _save(client, conf, **visibility):
    """Post the conference form with the speaker section ticked as given:
    status=("grid"|"page"|"hidden"), mirroring the checkboxes."""
    data = {"slug": conf["slug"], "title": "Visibility Conference",
            "start_date": "2027-08-01", "end_date": "2027-08-03",
            "speaker_visibility_form": "1"}
    for status, level in visibility.items():
        if level == "grid":
            data[f"speaker_grid_{status}"] = "1"
            data[f"speaker_page_{status}"] = "1"
        elif level == "page":
            data[f"speaker_page_{status}"] = "1"
    resp = client.post(f"/admin/conferences/{conf['id']}/save", data=data)
    assert resp.status_code == 302


class TestDefaults:
    def test_everything_but_posters_is_listed(self, seeded, client, conf):
        page = _grid(client, conf)
        tag = conf["tag"]
        for status in ("plenary", "keynote", "invited", "accepted_oral"):
            assert f"{status} talk {tag}" in page, status
        assert f"accepted_poster talk {tag}" not in page
        assert f"rejected talk {tag}" not in page

    def test_poster_pages_are_not_public(self, seeded, client, conf):
        aid = conf["abstracts"]["accepted_poster"]
        assert client.get(f"/abstracts/{aid}").status_code == 404

    def test_rejected_pages_are_never_public(self, seeded, client, conf):
        aid = conf["abstracts"]["rejected"]
        assert client.get(f"/abstracts/{aid}").status_code == 404


class TestSaving:
    def test_listing_posters(self, seeded, admin_client, client, conf):
        _save(admin_client, conf, plenary="grid", keynote="grid",
              invited="grid", accepted_oral="grid", accepted_poster="grid")
        assert f"accepted_poster talk {conf['tag']}" in _grid(client, conf)
        aid = conf["abstracts"]["accepted_poster"]
        assert client.get(f"/abstracts/{aid}").status_code == 200

    def test_page_only_is_unlisted_but_reachable(self, seeded, admin_client,
                                                 client, conf):
        _save(admin_client, conf, plenary="grid", accepted_oral="page")
        assert f"accepted_oral talk {conf['tag']}" not in _grid(client, conf)
        aid = conf["abstracts"]["accepted_oral"]
        assert client.get(f"/abstracts/{aid}").status_code == 200

    def test_hidden_is_neither(self, seeded, admin_client, client, conf):
        _save(admin_client, conf, plenary="grid")
        assert f"keynote talk {conf['tag']}" not in _grid(client, conf)
        aid = conf["abstracts"]["keynote"]
        assert client.get(f"/abstracts/{aid}").status_code == 404

    def test_grid_without_page_still_means_grid(self, seeded, admin_client,
                                                app, conf):
        """A listed speaker links to their page, so the grid tick wins."""
        admin_client.post(f"/admin/conferences/{conf['id']}/save", data={
            "slug": conf["slug"], "title": "Visibility Conference",
            "start_date": "2027-08-01", "end_date": "2027-08-03",
            "speaker_visibility_form": "1",
            "speaker_grid_invited": "1"})
        with app.app_context():
            c = db.session.get(Conference, conf["id"])
            assert c.speaker_visibility_for("invited") == "grid"
            assert "invited" in c.page_statuses()

    def test_a_form_without_the_section_leaves_it_alone(self, seeded,
                                                        admin_client, app,
                                                        conf):
        _save(admin_client, conf, accepted_poster="grid")
        admin_client.post(f"/admin/conferences/{conf['id']}/save", data={
            "slug": conf["slug"], "title": "Visibility Conference",
            "start_date": "2027-08-01", "end_date": "2027-08-03"})
        with app.app_context():
            c = db.session.get(Conference, conf["id"])
            assert c.speaker_visibility_for("accepted_poster") == "grid"

    def test_edit_page_renders_the_section(self, seeded, admin_client, conf):
        import re

        page = admin_client.get(
            f"/admin/conferences/{conf['id']}/edit").data.decode()
        assert 'id="speaker-visibility"' in page
        assert "Accepted (poster)" in page

        def tag(name):
            m = re.search(rf'<input[^>]*name="{name}"[^>]*>', page)
            assert m, name
            return m.group(0)

        assert "checked" in tag("speaker_grid_plenary")
        assert "checked" in tag("speaker_page_plenary")
        assert "checked" not in tag("speaker_grid_accepted_poster")
        assert "checked" not in tag("speaker_page_accepted_poster")


class TestOralAndPoster:
    def test_bulk_accept_as_poster(self, seeded, admin_client, app):
        tag = secrets.token_hex(4)
        with app.app_context():
            c = Conference(slug=f"bulk-{tag}", title="Bulk",
                           start_date=date(2027, 9, 1),
                           end_date=date(2027, 9, 2))
            reviewers = [User(email=f"r{i}-{tag}@test.example.org",
                              role_name="member") for i in range(2)]
            db.session.add_all([c, *reviewers])
            db.session.flush()
            a = Abstract(conference_id=c.id, status="submitted",
                         title="Scored", authors="X|1|Uni", body="Body.")
            db.session.add(a)
            db.session.flush()
            for r in reviewers:
                db.session.add(ReviewAssignment(
                    abstract_id=a.id, reviewer_id=r.id,
                    status="completed", score=90))
            db.session.commit()
            cid, aid = c.id, a.id

        resp = admin_client.post(f"/admin/conferences/{cid}/bulk-decision",
                                 data={"threshold": "70",
                                       "action": "accept_poster"})
        assert resp.status_code == 302
        with app.app_context():
            assert db.session.get(Abstract, aid).status == "accepted_poster"

    def test_bare_accept_is_refused(self, seeded, admin_client, conf, app):
        resp = admin_client.post(
            f"/admin/conferences/{conf['id']}/bulk-decision",
            data={"threshold": "70", "action": "accept"},
            follow_redirects=True)
        assert b"Invalid action" in resp.data

    def test_decision_form_offers_both(self, seeded, admin_client, conf):
        aid = conf["abstracts"]["plenary"]
        page = admin_client.get(f"/admin/abstracts/{aid}").data
        assert b'value="accepted_oral"' in page
        assert b'value="accepted_poster"' in page
        assert b'value="accepted"' not in page

    def test_decision_rejects_an_unknown_status(self, seeded, admin_client,
                                                conf, app):
        aid = conf["abstracts"]["plenary"]
        resp = admin_client.post(f"/admin/abstracts/{aid}",
                                 data={"status": "accepted"},
                                 follow_redirects=True)
        assert b"Unknown status" in resp.data
        with app.app_context():
            assert db.session.get(Abstract, aid).status == "plenary"

    def test_labels_on_the_panel(self, seeded, admin_client, conf):
        page = admin_client.get(
            f"/admin/abstracts?status=all&conference_id={conf['id']}").data
        assert b"Accepted (oral)" in page
        assert b"Accepted (poster)" in page
