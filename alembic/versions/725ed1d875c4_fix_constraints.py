"""fix constraints

Revision ID: 725ed1d875c4
Revises: c1d2e3f4a5b6
Create Date: 2026-06-03 16:06:09.746064

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '725ed1d875c4'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # company_tech
    op.execute("DROP INDEX IF EXISTS uq_company_tech")
    op.execute("ALTER TABLE company_tech ADD CONSTRAINT uq_company_tech UNIQUE NULLS NOT DISTINCT (company_id, tech_tag_id, role_id)")

    # company_feature
    op.execute("DROP INDEX IF EXISTS uq_company_feature")
    op.execute("ALTER TABLE company_feature ADD CONSTRAINT uq_company_feature UNIQUE NULLS NOT DISTINCT (company_id, role_id, feature_key)")


def downgrade() -> None:
    # company_feature
    op.execute("ALTER TABLE company_feature DROP CONSTRAINT IF EXISTS uq_company_feature")
    op.execute("CREATE UNIQUE INDEX uq_company_feature ON company_feature (company_id, role_id, feature_key) NULLS NOT DISTINCT")

    # company_tech
    op.execute("ALTER TABLE company_tech DROP CONSTRAINT IF EXISTS uq_company_tech")
    op.execute("CREATE UNIQUE INDEX uq_company_tech ON company_tech (company_id, tech_tag_id, role_id) NULLS NOT DISTINCT")
