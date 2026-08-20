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


class TestSpeakerBio:
    """The bio is admin-only input (`abs.edit`), optional, and shows wherever
    it is non-empty — leaving it blank is how you hide it."""

    @pytest.fixture
    def speaker_id(self, app, db, conference):
        a = Abstract(user_id=None, conference_id=conference,
                     title="Plenary with a bio", authors="P. Speaker|1|Uni",
                     body="Plenary body.", status="plenary")
        db.session.add(a)
        db.session.commit()
        return a.id

    def test_admin_can_set_and_clear_bio(self, seeded, admin_client,
                                         speaker_id, app):
        bio = "Dr Speaker leads the imaging group.\n\nThey chair the panel."
        admin_client.post(f"/admin/abstracts/{speaker_id}/edit",
                          data=_form(title="Plenary with a bio",
                                     status="plenary", speaker_bio=bio),
                          follow_redirects=True)
        with app.app_context():
            a = Abstract.query.get(speaker_id)
            assert a.speaker_bio == bio
            assert len(a.bio_paragraphs) == 2

        admin_client.post(f"/admin/abstracts/{speaker_id}/edit",
                          data=_form(title="Plenary with a bio",
                                     status="plenary", speaker_bio=""),
                          follow_redirects=True)
        with app.app_context():
            assert Abstract.query.get(speaker_id).bio_paragraphs == []

    def test_bio_is_optional(self, seeded, admin_client, conference, app):
        """A form that never mentions the field still saves."""
        resp = admin_client.post("/admin/abstracts/new",
                                 data=_form(conference, title="No bio here"),
                                 follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            a = Abstract.query.filter_by(title="No bio here").first()
            assert a is not None
            assert a.bio_paragraphs == []

    def test_bio_shows_on_public_page_only_when_set(self, seeded, client,
                                                    speaker_id, app, db):
        resp = client.get(f"/abstracts/{speaker_id}")
        assert resp.status_code == 200
        assert b"About" not in resp.data.split(b"<h2>")[0]

        with app.app_context():
            a = Abstract.query.get(speaker_id)
            a.speaker_bio = "Runs the national imaging facility."
            db.session.commit()
        resp = client.get(f"/abstracts/{speaker_id}")
        assert b"Runs the national imaging facility." in resp.data

    def test_member_editing_own_abstract_cannot_touch_the_bio(
            self, seeded, member_client, conference, app, db):
        """The submission form has no bio field: a member posting one is
        ignored, and editing their abstract does not wipe a staff-written bio."""
        from app.models import Conference, User

        with app.app_context():
            u = User.query.filter_by(email="member@test.example.org").first()
            a = Abstract(user_id=u.id, conference_id=conference,
                         title="Member's own abstract",
                         authors="M. Member|1|Uni", body="Member body.",
                         status="submitted",
                         speaker_bio="Written by the programme chair.")
            db.session.add(a)
            db.session.commit()
            aid, slug = a.id, Conference.query.get(conference).slug

        resp = member_client.post(
            f"/conferences/{slug}/abstract?edit={aid}",
            data={"title": "Member's own abstract", "authors": "M. Member|1|Uni",
                  "body": "Body rewritten by the member.", "action": "draft",
                  "presenting_author_index": "0",
                  "speaker_bio": "I am extremely important."},
            follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            a = Abstract.query.get(aid)
            assert a.body == "Body rewritten by the member."
            assert a.speaker_bio == "Written by the programme chair."


class TestBookletCompile:
    """The booklet compiles through the shared tectonic renderer (the same
    queue/lock the invoice documents use), not a separate pdflatex toolchain.
    Compiles for real, like tests/test_documents.py."""

    @pytest.fixture
    def booklet_conference(self, app, db):
        from datetime import date
        c = Conference.query.filter_by(slug="booklet-conf").first()
        if not c:
            c = Conference(slug="booklet-conf", title="Booklet Conference",
                           start_date=date(2027, 3, 1), end_date=date(2027, 3, 3))
            db.session.add(c)
            db.session.commit()
        if not Abstract.query.filter_by(conference_id=c.id).first():
            db.session.add(Abstract(
                user_id=None, conference_id=c.id, status="plenary",
                title="Imaging at scale", authors="P. Speaker|1|Uni",
                body="First paragraph.\n\nSecond paragraph with 50% & $signs."))
            db.session.commit()
        return c.id

    def test_pdf_compiles(self, seeded, admin_client, booklet_conference):
        resp = admin_client.post(
            f"/admin/conferences/{booklet_conference}/compile-booklet",
            data={"booklet_action": "pdf"})
        assert resp.status_code == 200, resp.data[:500]
        assert resp.data[:4] == b"%PDF"

    def test_contents_entries_link_to_their_own_abstract(self, seeded,
                                                        admin_client, app, db):
        """Each contents entry must jump to its own abstract.

        `\\addcontentsline` records a title and a page but links to the most
        recent anchor, and an abstract fragment issues no sectioning command —
        so without a `\\phantomsection` the booklet holds exactly one anchor
        and every entry in the contents jumps to the same place. Asserted on
        the compiled PDF's destinations, because the LaTeX source looks
        entirely correct either way.
        """
        import re
        import zlib
        from datetime import date

        with app.app_context():
            c = Conference(slug="toc-links", title="Contents Test",
                           start_date=date(2027, 4, 1), end_date=date(2027, 4, 3))
            db.session.add(c)
            db.session.flush()
            for i in range(1, 4):
                db.session.add(Abstract(
                    conference_id=c.id, status="accepted",
                    title=f"Abstract {i}", authors=f"Author {i}|1|Uni {i}",
                    body="Body text. " * 60, presenting_author_index=0))
            db.session.commit()
            cid = c.id

        resp = admin_client.post(
            f"/admin/conferences/{cid}/compile-booklet",
            data={"booklet_action": "pdf"})
        assert resp.status_code == 200, resp.data[:400]

        blob = [resp.data]
        for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", resp.data, re.S):
            try:
                blob.append(zlib.decompress(m.group(1)))
            except Exception:
                pass
        blob = b"\n".join(blob)

        targets = re.findall(rb"/D\s*\[?\s*\(?(section\*\.\d+)", blob)
        targets += re.findall(rb"/Dest\s*\((section\*\.\d+)\)", blob)
        assert targets, "the contents entries are not linked at all"
        assert len(set(targets)) >= 3, (
            f"three abstracts share {len(set(targets))} link target(s): "
            f"{sorted(set(t.decode() for t in targets))}")

    def test_missing_tectonic_is_reported_not_crashed(self, seeded, admin_client,
                                                      booklet_conference, app):
        import shutil
        from pathlib import Path
        # A previous compile leaves a cached PDF that would be served without
        # ever reaching tectonic.
        shutil.rmtree(Path(app.config["UPLOAD_FOLDER"]) / "abstracts"
                      / ".booklet-cache", ignore_errors=True)
        app.config["TECTONIC_BIN"] = "/nonexistent/tectonic-xyz"
        try:
            resp = admin_client.post(
                f"/admin/conferences/{booklet_conference}/compile-booklet",
                data={"booklet_action": "pdf"}, follow_redirects=True)
        finally:
            app.config.pop("TECTONIC_BIN", None)
        assert resp.status_code == 200
        assert b"tectonic not found" in resp.data
