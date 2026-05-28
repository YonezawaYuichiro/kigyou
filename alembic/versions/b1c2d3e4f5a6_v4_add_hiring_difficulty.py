"""v4: add hiring_difficulty_score to company_dimensions

Revision ID: b1c2d3e4f5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-05-28

"""

import sqlalchemy as sa

from alembic import op

revision = "b1c2d3e4f5a6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "company_dimensions",
        sa.Column("hiring_difficulty_score", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("company_dimensions", "hiring_difficulty_score")
