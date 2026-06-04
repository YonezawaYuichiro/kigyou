"""v5: company テーブルに listing_type / founded_year / target_market を追加

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-04

未実装ギャップ対応:
  - listing_type : 上場区分（東証プライム/スタンダード/グロース/未上場/グループ等）
  - founded_year : 設立年（CompanyMetrics に重複あり → company 直下に正規化）
  - target_market: ターゲット市場（BtoB/BtoC/BtoBtoC等、将来タグ化も可）
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: str | None = "725ed1d875c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("company", sa.Column("listing_type", sa.String(length=30), nullable=True))
    op.add_column("company", sa.Column("founded_year", sa.Integer(), nullable=True))
    op.add_column("company", sa.Column("target_market", sa.String(length=50), nullable=True))


def downgrade() -> None:
    op.drop_column("company", "target_market")
    op.drop_column("company", "founded_year")
    op.drop_column("company", "listing_type")
