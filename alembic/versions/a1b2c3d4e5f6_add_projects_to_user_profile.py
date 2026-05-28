"""add projects to user_profile

Revision ID: a1b2c3d4e5f6
Revises: 5b55de823f24
Create Date: 2026-05-28

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSON

revision = "a1b2c3d4e5f6"
down_revision = "5b55de823f24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("user_profile", sa.Column("projects", JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("user_profile", "projects")
