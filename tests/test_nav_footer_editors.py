"""Navigation and footer editing: external links, and removing things.

Both editors drive the same link-target control, so the behaviour is asserted
against both rather than trusting that they stayed in step.
"""
from __future__ import annotations

import pytest

from app.extensions import db
from app.models import FooterColumn, FooterLink, NavItem
from app.services.targets import (EXTERNAL_CHOICE, build_target,
                                  normalize_external, split_target)


class TestTargetScheme:
    """The one place that decides what a link may point at."""

    def test_round_trips_an_external_url(self):
        target = build_target(EXTERNAL_CHOICE, "https://example.org/x")
        assert target == "url:https://example.org/x"
        assert split_target(target) == (EXTERNAL_CHOICE, "https://example.org/x")

    def test_bare_domain_becomes_https(self):
        assert build_target(EXTERNAL_CHOICE, "example.org") == "url:https://example.org"

    def test_mailto_is_kept(self):
        assert (build_target(EXTERNAL_CHOICE, "mailto:a@b.org")
                == "url:mailto:a@b.org")

    def test_internal_choices_pass_through(self):
        assert build_target("page:about") == "page:about"
        assert split_target("page:about") == ("page:about", "")

    @pytest.mark.parametrize("bad", [
        "javascript:alert(1)",          # the reason schemes are whitelisted
        "data:text/html,<script>x</script>",
        "",
        "not a url",
        "https://" + "x" * 250,
    ])
    def test_refuses_what_should_not_become_an_href(self, bad):
        with pytest.raises(ValueError):
            normalize_external(bad)


class TestNavEditor:
    def test_page_offers_the_external_option(self, seeded, admin_client):
        resp = admin_client.get("/admin/nav")
        assert resp.status_code == 200
        assert EXTERNAL_CHOICE.encode() in resp.data
        assert b"data-target-url" in resp.data

    def test_add_an_external_item(self, seeded, admin_client, app):
        admin_client.post("/admin/nav", data={
            "action": "add", "label": "Society journal",
            "target": EXTERNAL_CHOICE,
            "target_url": "https://journal.example.org",
        }, follow_redirects=True)
        with app.app_context():
            n = NavItem.query.filter_by(label="Society journal").one()
            assert n.target == "url:https://journal.example.org"

    def test_external_item_renders_as_that_href(self, seeded, admin_client,
                                                client, app):
        admin_client.post("/admin/nav", data={
            "action": "add", "label": "Journal",
            "target": EXTERNAL_CHOICE, "target_url": "https://j.example.org",
        }, follow_redirects=True)
        resp = client.get("/")
        assert b'href="https://j.example.org"' in resp.data

    def test_editing_an_item_to_an_external_url(self, seeded, admin_client, app):
        with app.app_context():
            item = NavItem(label="Temp", target="home", display_order=90)
            db.session.add(item)
            db.session.commit()
            iid = item.id
        admin_client.post("/admin/nav", data={
            "action": "save", "id": str(iid),
            f"label_{iid}": "Temp", f"target_{iid}": EXTERNAL_CHOICE,
            f"target_url_{iid}": "example.net/docs",
            f"order_{iid}": "90", f"visible_{iid}": "on",
        }, follow_redirects=True)
        with app.app_context():
            assert NavItem.query.get(iid).target == "url:https://example.net/docs"

    def test_a_bad_url_keeps_the_old_target_and_says_so(self, seeded,
                                                        admin_client, app):
        with app.app_context():
            item = NavItem(label="Keep", target="page:about", display_order=91)
            db.session.add(item)
            db.session.commit()
            iid = item.id
        resp = admin_client.post("/admin/nav", data={
            "action": "save", "id": str(iid),
            f"label_{iid}": "Keep", f"target_{iid}": EXTERNAL_CHOICE,
            f"target_url_{iid}": "javascript:alert(1)",
            f"order_{iid}": "91",
        }, follow_redirects=True)
        assert b"may only use" in resp.data
        with app.app_context():
            assert NavItem.query.get(iid).target == "page:about"

    def test_delete_removes_only_that_item(self, seeded, admin_client, app):
        with app.app_context():
            a = NavItem(label="Doomed", target="home", display_order=92)
            b = NavItem(label="Survivor", target="home", display_order=93)
            db.session.add_all([a, b])
            db.session.commit()
            doomed, survivor = a.id, b.id
        resp = admin_client.post(f"/admin/nav/{doomed}/delete",
                                 follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert NavItem.query.get(doomed) is None
            assert NavItem.query.get(survivor) is not None

    def test_member_cannot_delete(self, seeded, member_client, app):
        with app.app_context():
            n = NavItem(label="Protected", target="home", display_order=94)
            db.session.add(n)
            db.session.commit()
            nid = n.id
        resp = member_client.post(f"/admin/nav/{nid}/delete")
        assert resp.status_code in (302, 403)
        with app.app_context():
            assert NavItem.query.get(nid) is not None


class TestFooterEditor:
    @pytest.fixture
    def column_with_link(self, app):
        with app.app_context():
            col = FooterColumn(title="Resources", display_order=50)
            db.session.add(col)
            db.session.flush()
            ln = FooterLink(column_id=col.id, label="Handbook",
                            target="home", display_order=10)
            db.session.add(ln)
            db.session.commit()
            return col.id, ln.id

    def test_page_offers_the_external_option(self, seeded, admin_client):
        resp = admin_client.get("/admin/footer")
        assert resp.status_code == 200
        assert EXTERNAL_CHOICE.encode() in resp.data
        assert b"data-target-url" in resp.data

    def test_add_an_external_link(self, seeded, admin_client, app,
                                  column_with_link):
        col_id, _ = column_with_link
        admin_client.post("/admin/footer", data={
            "action": "add_link", "column_id": str(col_id),
            "label": "Funder", "target": EXTERNAL_CHOICE,
            "target_url": "https://funder.example.org",
        }, follow_redirects=True)
        with app.app_context():
            ln = FooterLink.query.filter_by(label="Funder").one()
            assert ln.target == "url:https://funder.example.org"

    def test_editing_a_link_to_an_external_url(self, seeded, admin_client, app,
                                               column_with_link):
        col_id, link_id = column_with_link
        admin_client.post("/admin/footer", data={
            "action": "save",
            f"col_title_{col_id}": "Resources", f"col_order_{col_id}": "50",
            f"link_label_{link_id}": "Handbook",
            f"link_target_{link_id}": EXTERNAL_CHOICE,
            f"link_target_url_{link_id}": "https://handbook.example.org",
            f"link_order_{link_id}": "10",
        }, follow_redirects=True)
        with app.app_context():
            assert (FooterLink.query.get(link_id).target
                    == "url:https://handbook.example.org")

    def test_a_bad_url_keeps_the_old_target_and_says_so(self, seeded,
                                                        admin_client, app,
                                                        column_with_link):
        col_id, link_id = column_with_link
        resp = admin_client.post("/admin/footer", data={
            "action": "save",
            f"col_title_{col_id}": "Resources", f"col_order_{col_id}": "50",
            f"link_label_{link_id}": "Handbook",
            f"link_target_{link_id}": EXTERNAL_CHOICE,
            f"link_target_url_{link_id}": "javascript:alert(1)",
            f"link_order_{link_id}": "10",
        }, follow_redirects=True)
        assert b"may only use" in resp.data
        with app.app_context():
            assert FooterLink.query.get(link_id).target == "home"

    def test_delete_a_link_leaves_the_column(self, seeded, admin_client, app,
                                             column_with_link):
        col_id, link_id = column_with_link
        resp = admin_client.post(f"/admin/footer/link/{link_id}/delete",
                                 follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert FooterLink.query.get(link_id) is None
            assert FooterColumn.query.get(col_id) is not None

    def test_delete_a_column_takes_its_links(self, seeded, admin_client, app,
                                             column_with_link):
        col_id, link_id = column_with_link
        resp = admin_client.post(f"/admin/footer/column/{col_id}/delete",
                                 follow_redirects=True)
        assert resp.status_code == 200
        with app.app_context():
            assert FooterColumn.query.get(col_id) is None
            # Orphaned links would otherwise sit in the table forever, and the
            # footer renderer would still try to draw them.
            assert FooterLink.query.get(link_id) is None

    def test_member_cannot_delete_a_column(self, seeded, member_client, app,
                                           column_with_link):
        col_id, _ = column_with_link
        resp = member_client.post(f"/admin/footer/column/{col_id}/delete")
        assert resp.status_code in (302, 403)
        with app.app_context():
            assert FooterColumn.query.get(col_id) is not None
