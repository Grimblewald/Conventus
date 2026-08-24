"""One abstract as a PDF, and the submission receipt that carries it.

Compiles for real, like tests/test_documents.py — a preview that renders
differently from the booklet is worse than no preview, so the thing under
test is the actual output.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.extensions import db
from app.models import Abstract, Conference, User


@pytest.fixture
def abstract_id(app):
    import secrets
    tag = secrets.token_hex(4)
    with app.app_context():
        u = User(email=f"presenter-{tag}@example.org", full_name="Jane Doe",
                 role_name="member")
        db.session.add(u)
        c = Conference(slug=f"pdf-conf-{tag}", title="Physics & Chemistry 2026",
                       start_date=date(2026, 9, 1), end_date=date(2026, 9, 3))
        db.session.add(c)
        db.session.flush()
        a = Abstract(
            user_id=u.id, conference_id=c.id,
            title="Imaging at 50% scale & beyond",
            authors="Jane Doe|1|Example University\nJohn Roe|2|Other Uni",
            body="First paragraph with $ and % and _ and #.\n\nSecond one [1].",
            status="submitted", presenting_author_index=0,
            references=[{"key": 1, "doi": "10.1000/example"}])
        db.session.add(a)
        db.session.commit()
        return a.id


class TestRender:
    @pytest.mark.real_latex
    def test_compiles_a_real_pdf(self, app, abstract_id):
        """Including the LaTeX metacharacters an author will inevitably type."""
        from app.services.abstract_latex import render_abstract_pdf
        with app.app_context():
            a = Abstract.query.get(abstract_id)
            pdf = render_abstract_pdf(
                a, uploads_root=Path(app.config["UPLOAD_FOLDER"]))
        assert pdf[:4] == b"%PDF"
        assert len(pdf) > 1000

    def test_download_name_is_recognisable(self, app, abstract_id):
        from app.services.abstract_latex import abstract_pdf_filename
        with app.app_context():
            name = abstract_pdf_filename(Abstract.query.get(abstract_id))
        assert name.endswith(".pdf")
        assert "jane-doe" in name
        # No characters that would confuse a download header or a filesystem.
        assert all(ch.isalnum() or ch in "-." for ch in name)

    def test_leaves_no_job_directory_behind(self, app, abstract_id):
        from app.services.abstract_latex import render_abstract_pdf
        import tempfile
        before = set(Path(tempfile.gettempdir()).glob("abstract-*"))
        with app.app_context():
            render_abstract_pdf(Abstract.query.get(abstract_id),
                                uploads_root=Path(app.config["UPLOAD_FOLDER"]))
        assert not (set(Path(tempfile.gettempdir()).glob("abstract-*")) - before)


class TestDownloadRoutes:
    def test_author_can_download_their_own(self, seeded, client, app,
                                           abstract_id):
        with app.app_context():
            uid = Abstract.query.get(abstract_id).user_id
        with client.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        resp = client.get(f"/abstracts/{abstract_id}/pdf")
        assert resp.status_code == 200
        assert resp.mimetype == "application/pdf"
        assert resp.data[:4] == b"%PDF"

    def test_another_member_cannot(self, seeded, member_client, abstract_id):
        resp = member_client.get(f"/abstracts/{abstract_id}/pdf")
        assert resp.status_code in (302, 403)

    def test_admin_route_serves_it(self, seeded, admin_client, abstract_id):
        resp = admin_client.get(f"/admin/abstracts/{abstract_id}/pdf")
        assert resp.status_code == 200
        assert resp.data[:4] == b"%PDF"

    def test_buttons_are_offered(self, seeded, admin_client, abstract_id):
        resp = admin_client.get(f"/admin/abstracts/{abstract_id}")
        assert f"/admin/abstracts/{abstract_id}/pdf".encode() in resp.data


class TestSubmissionReceipt:
    """A receipt for the submission — explicitly not a decision."""

    def _conference(self, app, receipt=True):
        import secrets
        tag = secrets.token_hex(4)
        with app.app_context():
            c = Conference(slug=f"receipt-{tag}", title="Receipt Conference",
                           start_date=date(2027, 5, 1), end_date=date(2027, 5, 3),
                           abstract_receipt_email=receipt)
            db.session.add(c)
            db.session.commit()
            return c.slug

    def _submit(self, client, slug):
        return client.post(f"/conferences/{slug}/abstract", data={
            "title": "A submitted abstract",
            "authors": "Jane Doe|1|Example University",
            "body": "A body of sufficient substance.",
            "presenting_author_index": "0",
            "action": "submit",
        }, follow_redirects=True)

    def test_sends_with_the_pdf_attached(self, seeded, member_client, app,
                                         monkeypatch):
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda **kw: sent.append(kw) or True)
        slug = self._conference(app)
        self._submit(member_client, slug)

        assert len(sent) == 1
        mail = sent[0]
        assert "Abstract received" in mail["subject"]
        assert mail["attachments"], "the PDF is the useful half of the receipt"
        name, content, mimetype = mail["attachments"][0]
        assert name.endswith(".pdf") and mimetype == "application/pdf"
        assert content[:4] == b"%PDF"
        # It must not read as an acceptance.
        assert "not a decision" in mail["body"]

    def test_off_by_conference_setting(self, seeded, member_client, app,
                                       monkeypatch):
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda **kw: sent.append(kw) or True)
        slug = self._conference(app, receipt=False)
        self._submit(member_client, slug)
        assert sent == []

    def test_drafts_do_not_send(self, seeded, member_client, app, monkeypatch):
        """Saving a draft five times must not send five emails."""
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda **kw: sent.append(kw) or True)
        slug = self._conference(app)
        for _ in range(3):
            member_client.post(f"/conferences/{slug}/abstract", data={
                "title": "Draft", "authors": "Jane Doe|1|Uni",
                "body": "Body.", "presenting_author_index": "0",
                "action": "draft",
            }, follow_redirects=True)
        assert sent == []

    def test_a_failed_render_still_sends_the_email(self, seeded, member_client,
                                                   app, monkeypatch):
        """The confirmation is the point; the attachment is the bonus."""
        from app.services.documents import RenderError
        sent = []
        monkeypatch.setattr("app.services.mail.send_mail",
                            lambda **kw: sent.append(kw) or True)

        def boom(*a, **kw):
            raise RenderError("tectonic exited 1")
        monkeypatch.setattr("app.services.abstract_latex.render_abstract_pdf",
                            boom)

        slug = self._conference(app)
        self._submit(member_client, slug)
        assert len(sent) == 1
        assert not sent[0]["attachments"]
        assert "Abstract received" in sent[0]["subject"]


class TestFigureSizing:
    """The figure takes the space left on the page, which is what keeps an
    abstract to one page — but it must never be enlarged past its own size."""

    def _fragment(self, **overrides):
        from types import SimpleNamespace

        from app.services.abstract_latex import abstract_fragment

        a = SimpleNamespace(
            title="A title", authors="Jane Doe|1|Uni", body="Some body text.",
            custom_data={}, references=None, figure_filename="fig.png",
            profile_picture_filename=None, presenting_author_index=0)
        for k, v in overrides.items():
            setattr(a, k, v)
        return abstract_fragment("001", a)

    def test_the_height_comes_from_the_space_left_on_the_page(self):
        tex = self._fragment()
        assert "\\pagegoal" in tex and "\\pagetotal" in tex
        # A fixed height would push the figure to the next page and strand the
        # remainder of this one.
        assert "0.32\\textheight" not in tex

    def test_it_is_never_enlarged_past_its_own_size(self):
        tex = self._fragment()
        assert "\\sbox0" in tex, "the natural size has to be measured"
        assert "\\ifdim\\dimen0>\\dimen2 \\dimen0=\\dimen2\\fi" in tex
        assert "\\ifdim\\dimen4>\\wd0 \\dimen4=\\wd0\\fi" in tex

    def test_it_cannot_be_given_a_height_of_zero(self):
        """A non-positive height fails the compile, taking the booklet with it."""
        assert "\\ifdim\\dimen0<12pt \\dimen0=12pt\\fi" in self._fragment()

    def test_room_is_kept_for_the_references(self):
        """Otherwise a figure expanding into the gap orphans them overleaf."""
        refs = [{"key": 1, "doi": "10.1/a"}, {"key": 2, "doi": "10.1/b"}]
        assert "-4\\baselineskip" in self._fragment(references=refs)
        assert "-0pt" in self._fragment(references=None)

    def test_references_do_not_inherit_the_body_line_spacing(self):
        refs = [{"key": 1, "doi": "10.1/a"}]
        tex = self._fragment(references=refs)
        assert "\\setstretch{1}" in tex
        assert "\\footnotesize" in tex
        # [n], matching how the body cites them.
        assert "[1]~" in tex

    def test_the_figure_comes_before_the_references(self):
        refs = [{"key": 1, "doi": "10.1/a"}]
        tex = self._fragment(references=refs)
        assert tex.index("includegraphics") < tex.index("\\textbf{References}")
