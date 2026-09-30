"""The /admin/abstracts LaTeX-bundle export (`abs.export`).

The zip must hold, for whatever the panel's filter shows: one folder per
abstract, named by its reference, holding a .tex that compiles inside that
folder plus its converted images; one compilable booklet .tex per conference
at the root; and a manifest. Unlike the booklet export, figures are sized
against the full page rather than squeezed into the space left on one.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from app.models import Abstract, Conference


def _make_image(path: Path, fmt: str, color: str) -> None:
    from PIL import Image
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (8, 8), color).save(path, fmt)


@pytest.fixture
def export_env(app, db):
    """Two conferences and three abstracts, unique per run — the app fixture
    is session-scoped, so the tag keeps other tests' rows out of searches.

    c1: an oral-accepted abstract with a PNG figure (earlier) and a submitted
    one with a WEBP profile picture (later). c2: a rejected abstract elsewhere.
    Author names carry the tag so `search=Zeb<tag>` matches exactly these.
    """
    import secrets
    tag = secrets.token_hex(4)
    uploads = Path(app.config["UPLOAD_FOLDER"])
    with app.app_context():
        c1 = Conference(slug=f"export-{tag}", title="Export Conference",
                        start_date=date(2027, 6, 1), end_date=date(2027, 6, 3))
        c2 = Conference(slug=f"export-other-{tag}", title="Other Conference",
                        start_date=date(2027, 7, 1), end_date=date(2027, 7, 3))
        db.session.add_all([c1, c2])
        db.session.flush()

        fig_rel = f"abstracts/fig_{tag}.png"
        _make_image(uploads / fig_rel, "PNG", "red")
        webp_rel = f"abstracts/pic_{tag}.webp"
        _make_image(uploads / webp_rel, "WEBP", "blue")

        t0 = datetime(2027, 1, 1)
        a1 = Abstract(conference_id=c1.id, user_id=None, status="accepted_oral",
                      title="Accepted with a figure",
                      authors=f"Zeb{tag} Doe|1|Example University",
                      body="Body text with 50% & $signs.",
                      figure_filename=fig_rel,
                      created_at=t0, updated_at=t0)
        a2 = Abstract(conference_id=c1.id, user_id=None, status="submitted",
                      title="Submitted plain",
                      authors=f"Zeb{tag} Roe|1|Other Uni",
                      body="More body.",
                      profile_picture_filename=webp_rel,
                      created_at=t0 + timedelta(hours=1), updated_at=t0)
        a3 = Abstract(conference_id=c2.id, user_id=None, status="rejected",
                      title="Elsewhere entirely",
                      authors=f"Zeb{tag} Sider|1|Far Uni",
                      body="Not in the c1 bundle.",
                      created_at=t0, updated_at=t0)
        db.session.add_all([a1, a2, a3])
        db.session.commit()
        yield {"c1": c1.id, "c2": c2.id, "slug1": c1.slug, "slug2": c2.slug,
               "tag": tag,
               "ref1": a1.reference, "ref2": a2.reference,
               "ref3": a3.reference}


def _bundle(client, **form) -> zipfile.ZipFile:
    resp = client.post("/admin/abstracts/download", data=form)
    assert resp.status_code == 200, resp.data[:400]
    assert resp.mimetype == "application/zip"
    return zipfile.ZipFile(io.BytesIO(resp.data))


class TestBundleContents:
    def test_layout_and_converted_images(self, seeded, admin_client, export_env):
        zf = _bundle(admin_client, status="all",
                     conference_id=str(export_env["c1"]))
        names = set(zf.namelist())
        ref1, ref2 = export_env["ref1"], export_env["ref2"]
        assert f"booklet-{export_env['slug1']}.tex" in names
        assert "manifest.csv" in names
        # One folder per abstract, named by reference, holding a document
        # and the fragment it inputs. Nothing per-abstract at the root.
        for ref in (ref1, ref2):
            assert f"{ref}/{ref}.tex" in names
            assert f"{ref}/abstract.tex" in names
        roots = {n for n in names if "/" not in n}
        assert roots == {f"booklet-{export_env['slug1']}.tex", "manifest.csv"}
        # Images are converted into the bundle, never shipped raw.
        assert f"{ref1}/figure.png" in names
        assert f"{ref2}/profile.png" in names, "WEBP must arrive as PNG"
        assert not any("fig_" in n or "pic_" in n for n in names)

    def test_booklet_imports_every_fragment(self, seeded, admin_client,
                                            export_env):
        zf = _bundle(admin_client, status="all",
                     conference_id=str(export_env["c1"]))
        booklet = zf.read(f"booklet-{export_env['slug1']}.tex").decode()
        assert "\\documentclass" in booklet and "\\tableofcontents" in booklet
        assert "\\usepackage{import}" in booklet
        for ref in (export_env["ref1"], export_env["ref2"]):
            assert f"\\subimport{{{ref}/}}{{abstract.tex}}" in booklet

    def test_folder_document_inputs_its_own_fragment(self, seeded,
                                                     admin_client,
                                                     export_env):
        zf = _bundle(admin_client, status="all",
                     conference_id=str(export_env["c1"]))
        ref = export_env["ref1"]
        doc = zf.read(f"{ref}/{ref}.tex").decode()
        assert "\\documentclass" in doc
        assert "\\usepackage{hyperref}" in doc, "fragments carry \\href"
        assert "\\input{abstract.tex}" in doc

    def test_figures_get_the_full_page(self, seeded, admin_client, export_env):
        zf = _bundle(admin_client, status="all",
                     conference_id=str(export_env["c1"]))
        frag = zf.read(f"{export_env['ref1']}/abstract.tex").decode()
        # Relative to the folder, so the document compiles where it stands.
        assert "]{figure.png}" in frag
        assert "height=\\textheight" in frag
        assert "keepaspectratio" in frag
        # The one-page squeeze belongs to the booklet, not to this export.
        assert "\\pagegoal" not in frag

    def test_manifest_lists_the_bundle(self, seeded, admin_client, export_env):
        zf = _bundle(admin_client, status="all",
                     conference_id=str(export_env["c1"]))
        manifest = zf.read("manifest.csv").decode()
        assert "reference" in manifest.splitlines()[0]
        assert "Accepted with a figure" in manifest
        assert "Submitted plain" in manifest
        assert "Accepted (oral)" in manifest
        assert export_env["slug1"] in manifest
        ref = export_env["ref1"]
        assert f"{ref}/{ref}.tex" in manifest


class TestFilterFidelity:
    """The bundle must be exactly what the panel shows — same query."""

    def test_status_filter(self, seeded, admin_client, export_env):
        zf = _bundle(admin_client, status="accepted_oral",
                     conference_id=str(export_env["c1"]))
        manifest = zf.read("manifest.csv").decode()
        assert "Accepted with a figure" in manifest
        assert "Submitted plain" not in manifest
        assert not any(n.startswith(export_env["ref2"])
                       for n in zf.namelist())

    def test_conference_filter(self, seeded, admin_client, export_env):
        zf = _bundle(admin_client, status="all",
                     conference_id=str(export_env["c1"]))
        manifest = zf.read("manifest.csv").decode()
        assert "Elsewhere entirely" not in manifest

    def test_search_filter_and_one_booklet_per_conference(self, seeded,
                                                          admin_client,
                                                          export_env):
        tag = export_env["tag"]
        zf = _bundle(admin_client, status="all", search=f"Zeb{tag}")
        names = set(zf.namelist())
        # The unfiltered-by-conference selection spans both conferences.
        assert f"booklet-{export_env['slug1']}.tex" in names
        assert f"booklet-{export_env['slug2']}.tex" in names
        folders = {n.split("/", 1)[0] for n in names if "/" in n}
        assert folders == {export_env["ref1"], export_env["ref2"],
                           export_env["ref3"]}
        # Each booklet carries only its own conference's fragments.
        b2 = zf.read(f"booklet-{export_env['slug2']}.tex").decode()
        b1 = zf.read(f"booklet-{export_env['slug1']}.tex").decode()
        assert b2.count("\\subimport{") == 1
        assert f"\\subimport{{{export_env['ref3']}/}}" in b2
        assert b1.count("\\subimport{") == 2


class TestEdgeCases:
    def test_empty_filter_redirects_with_a_flash(self, seeded, admin_client,
                                                 export_env):
        resp = admin_client.post("/admin/abstracts/download", data={
            "status": "rejected", "conference_id": str(export_env["c1"])})
        assert resp.status_code == 302
        assert "/admin/abstracts?" in resp.headers["Location"]
        page = admin_client.get(resp.headers["Location"])
        assert b"Nothing matches the current filter" in page.data

    def test_audit_row_written(self, seeded, admin_client, db, export_env):
        from app.models.audit import AuditLog
        _bundle(admin_client, status="all",
                conference_id=str(export_env["c1"]))
        row = (AuditLog.query
               .filter_by(action="abstract.exported")
               .order_by(AuditLog.id.desc()).first())
        assert row is not None
        assert "2 abstracts" in row.summary

    def test_button_appears_on_the_panel(self, seeded, admin_client,
                                         export_env):
        resp = admin_client.get(
            f"/admin/abstracts?status=all&conference_id={export_env['c1']}")
        assert b"/admin/abstracts/download" in resp.data
        assert b"Download selected" in resp.data


class TestPermissions:
    def test_member_is_refused(self, seeded, member_client):
        resp = member_client.post("/admin/abstracts/download",
                                  data={"status": "all"})
        assert resp.status_code == 403

    def test_committee_needs_abs_export(self, seeded, committee_client, db,
                                        export_env):
        from app.models import RolePermission
        client, _ = committee_client
        data = {"status": "all", "conference_id": str(export_env["c1"])}
        assert client.post("/admin/abstracts/download",
                           data=data).status_code == 403
        # Granted on the session the requests themselves use — export_env
        # keeps an app context open, so a nested context here would commit
        # to a different session and the request would see a stale role.
        grant = RolePermission(role_name="committee",
                               permission_key="abs.export")
        db.session.add(grant)
        db.session.commit()
        try:
            assert client.post("/admin/abstracts/download",
                               data=data).status_code == 200
        finally:
            db.session.delete(grant)
            db.session.commit()


class TestRealCompile:
    """The bundle's promise is that it compiles — check it, for real."""

    @pytest.mark.real_latex
    def test_standalone_and_booklet_compile(self, seeded, admin_client, app,
                                            export_env):
        import tempfile

        from app.services.documents import compile_prepared_dir

        zf = _bundle(admin_client, status="all",
                     conference_id=str(export_env["c1"]))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zf.extractall(root)
            with app.app_context():
                # Each folder's document compiles inside that folder alone.
                for ref in (export_env["ref1"], export_env["ref2"]):
                    pdf = compile_prepared_dir(root / ref,
                                               root / ref / f"{ref}.tex")
                    assert pdf[:4] == b"%PDF", ref
                booklet = root / f"booklet-{export_env['slug1']}.tex"
                assert compile_prepared_dir(root, booklet)[:4] == b"%PDF"
