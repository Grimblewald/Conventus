"""Initial schema

Revision ID: 4a1b2c3d4e5f
Revises:
Create Date: 2026-05-30

Creates all tables from the current SQLAlchemy model definitions.
Future migrations will be incremental from this baseline.
"""
from alembic import op
import sqlalchemy as sa


revision = '4a1b2c3d4e5f'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.extensions import db
    from app import create_app
    app = create_app()
    with app.app_context():
        db.create_all()


def downgrade() -> None:
    from app.extensions import db
    from app import create_app
    app = create_app()
    with app.app_context():
        db.drop_all()
