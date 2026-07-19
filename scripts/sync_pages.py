"""Sync markdown files from content/pages/ to the live database.

Reads every *.md file in content/pages/, maps the filename (minus .md) to
a Page slug, and updates the corresponding Page in the database via the
admin page-edit endpoint.  If a slug does not exist yet, the page is
created.

Usage:
    uv run python -m app syncpages
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

from app import create_app
from app.extensions import db
from app.models import User, Page


PAGES_DIR = Path(__file__).resolve().parent.parent / "content" / "pages"


def main():
    app = create_app()
    app.config["WTF_CSRF_ENABLED"] = False
    app.config["TESTING"] = True
    app.config["PREFERRED_URL_SCHEME"] = "https"

    md_files = sorted(PAGES_DIR.glob("*.md"))
    if not md_files:
        print("No .md files found in content/pages/ — nothing to sync.")
        return

    with app.test_client() as client:
        with app.app_context():
            admin = User.query.filter(
                User.deleted_at.is_(None), User.role.has(name="admin")
            ).first()
            if not admin:
                print("ERROR: no admin user found — aborting.")
                return

            with client.session_transaction() as sess:
                sess["_user_id"] = str(admin.id)

            for md_path in md_files:
                slug = md_path.stem
                body = md_path.read_text()
                title = slug.replace("-", " ").title()
                p = Page.query.filter_by(slug=slug).first()

                if p:
                    resp = client.post(
                        f"/admin/pages/{p.id}/edit",
                        data={"title": title, "slug": slug, "body": body, "published": "1"},
                        follow_redirects=True,
                    )
                    verb = "updated"
                else:
                    resp = client.post(
                        "/admin/pages/new",
                        data={"title": title, "slug": slug, "body": body, "published": "1"},
                        follow_redirects=True,
                    )
                    verb = "created"

                status = "OK" if resp.status_code == 200 else "FAILED"
                print(f"  {slug}: {status} → {verb}")

    print("\nDone.")


if __name__ == "__main__":
    main()
