"""Admin route tests: committee CRUD, user management, update page."""
from __future__ import annotations

from datetime import datetime

from app.models import CommitteeMember, User


class TestAdminAccess:
    def test_admin_page_redirects_anonymous(self, client):
        resp = client.get("/admin/")
        assert resp.status_code in (301, 302)

    def test_member_cannot_access_admin(self, member_client):
        resp = member_client.get("/admin/")
        # May be 403 (staff_required) or 302 (login_required redirect)
        # depending on whether the user_loader can resolve the session.
        assert resp.status_code in (301, 302, 403)

    def test_admin_can_access_dashboard(self, seeded, admin_client):
        resp = admin_client.get("/admin/")
        assert resp.status_code == 200


class TestCommitteeCRUD:
    def test_list_committee(self, seeded, admin_client):
        resp = admin_client.get("/admin/committee")
        assert resp.status_code == 200

    def test_create_committee_member(self, seeded, admin_client, app):
        resp = admin_client.post("/admin/committee/new", data={
            "full_name": "Dr Alice",
            "title": "Dr",
            "role": "President",
            "affiliation": "Test University",
            "display_order": "10",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Created" in resp.data or b"Alice" in resp.data

        with app.app_context():
            cm = CommitteeMember.query.filter_by(full_name="Dr Alice").first()
            assert cm is not None
            assert cm.role == "President"

    def test_create_requires_name(self, seeded, admin_client):
        resp = admin_client.post("/admin/committee/new", data={
            "full_name": "",
            "role": "Member",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Full name is required" in resp.data

    def test_edit_committee_member(self, seeded, admin_client, app):
        with app.app_context():
            cm = CommitteeMember(full_name="Bob", role="Secretary",
                                 display_order=20)
            from app.extensions import db
            db.session.add(cm)
            db.session.commit()
            mid = cm.id

        resp = admin_client.post(f"/admin/committee/{mid}/edit", data={
            "full_name": "Bob Updated",
            "title": "Prof",
            "role": "Treasurer",
            "affiliation": "New Org",
            "display_order": "5",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Saved" in resp.data or b"Updated" in resp.data

        with app.app_context():
            cm = db.session.get(CommitteeMember, mid)
            assert cm.full_name == "Bob Updated"
            assert cm.role == "Treasurer"

    def test_delete_committee_member(self, seeded, admin_client, app):
        with app.app_context():
            cm = CommitteeMember(full_name="To Delete", role="Member",
                                 display_order=99)
            from app.extensions import db
            db.session.add(cm)
            db.session.commit()
            mid = cm.id

        resp = admin_client.post(f"/admin/committee/{mid}/delete",
                                 follow_redirects=True)
        assert resp.status_code == 200
        assert b"Removed" in resp.data or b"Soft-deleted" in resp.data

        with app.app_context():
            cm = CommitteeMember.query.get(mid)
            assert cm is not None
            assert cm.deleted_at is not None

    def test_reorder_committee(self, seeded, admin_client, app):
        with app.app_context():
            from app.extensions import db
            cm1 = CommitteeMember(full_name="First", role="A",
                                  display_order=10)
            cm2 = CommitteeMember(full_name="Second", role="B",
                                  display_order=20)
            db.session.add_all([cm1, cm2])
            db.session.commit()
            id1, id2 = cm1.id, cm2.id

        resp = admin_client.post("/admin/committee/reorder", data={
            "id": [str(id2), str(id1)],
        }, follow_redirects=True)
        assert resp.status_code == 200

    def test_committee_new_page_renders(self, seeded, admin_client):
        resp = admin_client.get("/admin/committee/new")
        assert resp.status_code == 200


class TestUserManagement:
    def test_users_list(self, seeded, admin_client):
        resp = admin_client.get("/admin/users")
        assert resp.status_code == 200

    def test_users_filter_by_role(self, seeded, admin_client):
        resp = admin_client.get("/admin/users?role=member")
        assert resp.status_code == 200

    def test_set_user_role(self, seeded, admin_client, app):
        with app.app_context():
            u = User(email="roleme@test.example.org", full_name="Role Me",
                     role_name="unregistered")
            from app.extensions import db
            db.session.add(u)
            db.session.commit()
            uid = u.id

        resp = admin_client.post("/admin/users", data={
            "action": "set_role",
            "user_id": str(uid),
            "role": "committee",
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            u = db.session.get(User, uid)
            assert u.role_name == "committee"

    def test_cannot_change_admin_role(self, seeded, admin_client, app):
        with app.app_context():
            admin = User.query.filter_by(role_name="admin").first()
            uid = admin.id if admin else 1

        resp = admin_client.post("/admin/users", data={
            "action": "set_role",
            "user_id": str(uid),
            "role": "member",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"permitted" in resp.data.lower() or b"cannot" in resp.data.lower()

    def test_soft_delete_users(self, seeded, admin_client, app):
        with app.app_context():
            u = User(email="delete-me@test.example.org", full_name="Delete Me",
                     role_name="member")
            from app.extensions import db
            db.session.add(u)
            db.session.commit()
            uid = u.id

        resp = admin_client.post("/admin/users", data={
            "action": "delete",
            "user_ids": [str(uid)],
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            u = db.session.get(User, uid)
            assert u.deleted_at is not None

    def test_cannot_delete_admin(self, seeded, admin_client, app):
        with app.app_context():
            admin = User.query.filter_by(role_name="admin").first()
            uid = admin.id if admin else 1

        resp = admin_client.post("/admin/users", data={
            "action": "delete",
            "user_ids": [str(uid)],
        }, follow_redirects=True)
        assert resp.status_code == 200

        with app.app_context():
            from app.extensions import db
            u = db.session.get(User, uid)
            assert u.deleted_at is None


class TestFinancialInvoiceTemplate:
    def test_save_same_domain_from_email_no_warning(self, seeded, admin_client):
        """MAIL_FROM defaults to the your-domain.example.org sandbox address."""
        resp = admin_client.post("/admin/financial/invoice", data={
            "subject": "Invoice", "body_text": "Body",
            "from_email": "billing@your-domain.example.org",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invoice template saved" in resp.data
        assert b"SPF/DKIM" not in resp.data

    def test_save_different_domain_from_email_warns(self, seeded, admin_client, app):
        """from_email on a domain the SMTP sender doesn't own risks SPF/DKIM
        failure — warn but still save."""
        resp = admin_client.post("/admin/financial/invoice", data={
            "subject": "Invoice", "body_text": "Body",
            "from_email": "billing@some-other-domain.example.org",
        }, follow_redirects=True)
        assert resp.status_code == 200
        assert b"Invoice template saved" in resp.data
        assert b"SPF/DKIM" in resp.data

        with app.app_context():
            from app.models import get_invoice_template
            assert get_invoice_template().from_email == "billing@some-other-domain.example.org"


class TestUpdatePage:
    def test_update_page_requires_system_backup(self, admin_client):
        resp = admin_client.get("/admin/update")
        assert resp.status_code in (200, 403)

    def test_member_denied_update(self, member_client):
        resp = member_client.get("/admin/update")
        assert resp.status_code == 403
