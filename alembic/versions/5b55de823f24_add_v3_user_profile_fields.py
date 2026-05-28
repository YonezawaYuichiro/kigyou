"""add_v3_user_profile_fields

Revision ID: 5b55de823f24
Revises: f1e2d3c4b5a6
Create Date: 2026-05-28 09:39:03.913673

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b55de823f24'
down_revision: Union[str, None] = 'f1e2d3c4b5a6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("user_profile", sa.Column("graduation_year", sa.Integer(), nullable=True))
    op.add_column("user_profile", sa.Column("major", sa.String(200), nullable=True))
    op.add_column("user_profile", sa.Column("target_industries", sa.JSON(), nullable=True))
    op.add_column("user_profile", sa.Column("target_roles", sa.JSON(), nullable=True))
    op.add_column("user_profile", sa.Column("dev_phase_preference", sa.String(50), nullable=True))
    op.add_column("user_profile", sa.Column("min_salary", sa.Integer(), nullable=True))
    op.add_column("user_profile", sa.Column("mbti", sa.String(10), nullable=True))
    op.add_column("user_profile", sa.Column("eval_preference", sa.String(50), nullable=True))
    op.add_column(
        "user_profile", sa.Column("psych_safety_importance", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user_profile", "psych_safety_importance")
    op.drop_column("user_profile", "eval_preference")
    op.drop_column("user_profile", "mbti")
    op.drop_column("user_profile", "min_salary")
    op.drop_column("user_profile", "dev_phase_preference")
    op.drop_column("user_profile", "target_roles")
    op.drop_column("user_profile", "target_industries")
    op.drop_column("user_profile", "major")
    op.drop_column("user_profile", "graduation_year")
