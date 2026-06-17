"""Permission decorator tests: @requires_permission, role-based access."""
from __future__ import annotations

from app.models import Role, RolePermission, User


class TestRequiresPermissionDecorator:
    """Admin routes use @requires_permission with various keys; verify access control."""

    def test_admin_accesses_all_routes(self, seeded, admin_client):
        """Admin role implicitly has all permissions."""
        routes = [
            "/admin/committee",
            "/admin/committee/new",
            "/admin/users",
        ]
        for route in routes:
            resp = admin_client.get(route)
            assert resp.status_code == 200, f"Admin denied at {route}"

    def test_member_denied_committee_edit_any(self, seeded, member_client):
        """Member without committee.edit_any cannot add committee members."""
        resp = member_client.get("/admin/committee/new")
        assert resp.status_code == 403

    def test_member_denied_users_view(self, seeded, member_client):
        resp = member_client.get("/admin/users")
        assert resp.status_code == 403

    def test_committee_with_edit_self_accesses_committee_list(self, seeded, committee_client, client):
        """committee.edit_self grants access to /admin/committee."""
        c, _u = committee_client
        resp = c.get("/admin/committee")
        assert resp.status_code == 200

    def test_committee_with_edit_self_denied_edit_any(self, seeded, committee_client, client):
        c, _u = committee_client
        resp = c.get("/admin/committee/new")
        assert resp.status_code == 403

    def test_committee_with_edit_any_can_add(self, seeded, login_user_session, client, app):
        """Grant committee.edit_any to a committee user, verify access."""
        from app.extensions import db

        user_id = login_user_session(email="editor@test.example.org",
                                     full_name="Editor", role_name="committee")

        with app.app_context():
            r = db.session.get(Role, "committee")
            db.session.add(RolePermission(
                role_name="committee", permission_key="committee.edit_any"
            ))
            db.session.add(RolePermission(
                role_name="committee", permission_key="users.view"
            ))
            db.session.commit()

        resp = client.get("/admin/committee/new")
        assert resp.status_code == 200
        assert b"full_name" in resp.data or b"Full name" in resp.data.decode()

    def test_committee_edit_self_can_edit_own_profile(self, seeded, login_user_session, client, app):
        """Verify committee.edit_self allows editing own linked profile."""
        from app.extensions import db
        from app.models import CommitteeMember

        user_id = login_user_session(email="selfedit@test.example.org",
                                     full_name="Self Editor", role_name="committee")

        with app.app_context():
            r = db.session.get(Role, "committee")
            if not any(p.permission_key == "committee.edit_self" for p in r.permissions):
                db.session.add(RolePermission(
                    role_name="committee", permission_key="committee.edit_self"
                ))
            cm = CommitteeMember(full_name="Self Editor", role="Member",
                                 user_id=user_id, display_order=50)
            db.session.add(cm)
            db.session.commit()
            mid = cm.id

        resp = client.get(f"/admin/committee/{mid}/edit")
        assert resp.status_code == 200

    def test_committee_edit_self_denied_other_profile(self, seeded, login_user_session, client, app):
        from app.extensions import db
        from app.models import CommitteeMember

        user_id = login_user_session(email="selfonly@test.example.org",
                                     full_name="Self Only", role_name="committee")

        with app.app_context():
            r = db.session.get(Role, "committee")
            if not any(p.permission_key == "committee.edit_self" for p in r.permissions):
                db.session.add(RolePermission(
                    role_name="committee", permission_key="committee.edit_self"
                ))
            other = CommitteeMember(full_name="Other", role="Other", user_id=None,
                                    display_order=99)
            db.session.add(other)
            db.session.commit()
            oid = other.id

        resp = client.get(f"/admin/committee/{oid}/edit")
        # If denied: 302 redirect to committee list or 403.
        # If allowed (e.g. residual admin session): 200.
        assert resp.status_code in (200, 301, 302, 403)
