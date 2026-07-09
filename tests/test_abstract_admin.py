"""Admin abstract create/edit tests: account-less abstracts, abs.edit
permission gating, website URL normalization."""
from __future__ import annotations

from datetime import date

import pytest

from app.models import Abstract, Conference


@pytest.fixture
def conference(app, db):
    c = Conference.query.filter_by(slug="test-conf-abs").first()
    if not c:
        c = Conference(slug="test-conf-abs", title="Test Conference",
                       start_date=date(2027, 1, 10), end_date=date(2027, 1, 12))
        db.session.add(c)
        db.session.commit()
    return c.id


def _form(conference_id=None, **overrides):
    data = {
        "title": "Admin-entered plenary abstract",
        "authors": "Prof Plenary|1|Example University",
        "body": "A body of sufficient substance for testing.",
        "track": "",
        "presentation_type": "Oral",
        "keywords": "testing",
        "status": "plenary",
        "presenting_author_index": "0",
        "website_url": "example-lab.org/plenary",
    }
    if conference_id is not None:
        data["conference_id"] = str(conference_id)
    data.update(overrides)
    return data


class TestAdminCreate:
    def test_new_form_renders(self, seeded, admin_client):
        resp = admin_client.get("/admin/abstracts/new")
        assert resp.status_code == 200
        assert b"admin-entered" in resp.data.lower()

    def test_create_without_author_account(self, seeded, admin_client,
                                            conference, app):
        resp = admin_client.post("/admin/abstracts/new",
                                 data=_form(conference), follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            a = Abstract.query.filter_by(
                title="Admin-entered plenary abstract").first()
            assert a is not None
            assert a.user_id is None
            assert a.status == "plenary"
            # Scheme was auto-prefixed
            assert a.website_url == "https://example-lab.org/plenary"

    def test_create_with_owner_email_attaches_account(self, seeded,
                                                      admin_client,
                                                      conference, app):
        resp = admin_client.post(
            "/admin/abstracts/new",
            data=_form(conference, title="Attached to account",
                       owner_email="speaker@test.example.org"),
            follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            from app.models import User
            a = Abstract.query.filter_by(title="Attached to account").first()
            assert a is not None and a.user_id is not None
            u = User.query.get(a.user_id)
            assert u.email == "speaker@test.example.org"
            assert u.role_name == "unregistered"

    def test_create_requires_conference(self, seeded, admin_client):
        resp = admin_client.post("/admin/abstracts/new", data=_form(),
                                 follow_redirects=True)
        assert b"Choose a conference" in resp.data

    def test_create_rejects_bad_website(self, seeded, admin_client, conference):
        resp = admin_client.post(
            "/admin/abstracts/new",
            data=_form(conference, website_url="not a url"),
            follow_redirects=True)
        assert b"valid URL" in resp.data

    def test_member_cannot_create(self, seeded, member_client):
        resp = member_client.get("/admin/abstracts/new")
        assert resp.status_code in (302, 403)


class TestAdminEdit:
    @pytest.fixture
    def abstract_id(self, app, db, conference):
        a = Abstract(user_id=None, conference_id=conference,
                     title="Editable abstract", authors="A. Author|1|Uni",
                     body="Original body.", status="submitted")
        db.session.add(a)
        db.session.commit()
        return a.id

    def test_edit_full_content(self, seeded, admin_client, abstract_id, app):
        resp = admin_client.post(
            f"/admin/abstracts/{abstract_id}/edit",
            data=_form(title="Rewritten title",
                       authors="B. Other|1|Other Uni\nC. Third|2|Third Uni",
                       track="Nanomedicine", status="keynote",
                       references="https://doi.org/10.1000/xyz123"),
            follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            a = Abstract.query.get(abstract_id)
            assert a.title == "Rewritten title"
            assert a.track == "Nanomedicine"
            assert a.status == "keynote"
            assert "C. Third" in a.authors
            assert a.references == [{"key": 1, "doi": "10.1000/xyz123"}]

    def test_edit_validation_keeps_db_unchanged(self, seeded, admin_client,
                                                abstract_id, app):
        resp = admin_client.post(
            f"/admin/abstracts/{abstract_id}/edit",
            data=_form(title=""), follow_redirects=True)
        assert b"required" in resp.data
        with app.app_context():
            a = Abstract.query.get(abstract_id)
            assert a.title == "Editable abstract"

    def test_member_cannot_edit(self, seeded, member_client, abstract_id):
        resp = member_client.post(f"/admin/abstracts/{abstract_id}/edit",
                                  data=_form())
        assert resp.status_code in (302, 403)

    def test_list_and_search_survive_accountless(self, seeded, admin_client,
                                                 abstract_id):
        resp = admin_client.get("/admin/abstracts?status=all")
        assert resp.status_code == 200
        resp = admin_client.get("/admin/abstracts?status=all&search=Editable")
        assert resp.status_code == 200
        assert b"Editable abstract" in resp.data

    def test_detail_renders_accountless(self, seeded, admin_client, abstract_id):
        resp = admin_client.get(f"/admin/abstracts/{abstract_id}")
        assert resp.status_code == 200
        assert b"admin-entered" in resp.data


class TestCleanWebsite:
    def test_blank_is_empty(self):
        assert Abstract.clean_website(None) == ""
        assert Abstract.clean_website("  ") == ""

    def test_scheme_prefixed(self):
        assert (Abstract.clean_website("lab.example.org")
                == "https://lab.example.org")

    def test_existing_scheme_kept(self):
        assert (Abstract.clean_website("http://lab.example.org")
                == "http://lab.example.org")

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            Abstract.clean_website("not a url")
        with pytest.raises(ValueError):
            Abstract.clean_website("x" * 301)
