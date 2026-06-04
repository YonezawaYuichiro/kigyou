"""v5: 3層DB構成（原本層 / 特徴量層 / ユーザー層）

Revision ID: c1d2e3f4a5b6
Revises: b1c2d3e4f5a6
Create Date: 2026-06-03

新規テーブル（原本層）:
  - job_role / office / industry_master / company_industry
  - tech_tag / company_tech / salary_record / revenue_record / company_text

新規テーブル（特徴量層）:
  - feature_definition / company_feature

新規テーブル（ユーザー層）:
  - user_preference

既存テーブル変更:
  - company: has_relocation / engineer_count カラム追加

VIEW:
  - company_completeness（NULLをペナルティ可視化）

設計判断:
  - NULLS NOT DISTINCT: PG16の機能。role_id=NULLを全社値として一意に扱う
  - BigInteger: revenue / operating_profit (Integer上限は約21.4億円でオーバーフロー)
  - CheckConstraint: source_type / method / direction のタイポをDB側で防止
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "b1c2d3e4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ----------------------------------------------------------------
    # company テーブルへのカラム追加
    # ----------------------------------------------------------------
    op.add_column("company", sa.Column("has_relocation", sa.Boolean(), nullable=True))
    op.add_column("company", sa.Column("engineer_count", sa.Integer(), nullable=True))

    # ----------------------------------------------------------------
    # 原本層: job_role
    # ----------------------------------------------------------------
    op.create_table(
        "job_role",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("role_type", sa.String(length=50), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ----------------------------------------------------------------
    # 原本層: office
    # ----------------------------------------------------------------
    op.create_table(
        "office",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column("location", sa.String(length=100), nullable=False),
        sa.Column("is_dev_site", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ----------------------------------------------------------------
    # 原本層: industry_master / company_industry
    # ----------------------------------------------------------------
    op.create_table(
        "industry_master",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "company_industry",
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("industry_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["industry_id"], ["industry_master.id"]),
        sa.PrimaryKeyConstraint("company_id", "industry_id"),
    )

    # ----------------------------------------------------------------
    # 原本層: tech_tag / company_tech
    # ----------------------------------------------------------------
    op.create_table(
        "tech_tag",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("category", sa.String(length=30), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "company_tech",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("tech_tag_id", sa.Integer(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "source_type IN ('official','review','estimated')",
            name="ck_company_tech_source_type",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["job_role.id"]),
        sa.ForeignKeyConstraint(["tech_tag_id"], ["tech_tag.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # NULLS NOT DISTINCT: PG16でrole_id=NULL（全社）行の重複を防ぐ
    op.execute(
        "CREATE UNIQUE INDEX uq_company_tech "
        "ON company_tech (company_id, tech_tag_id, role_id) NULLS NOT DISTINCT"
    )

    # ----------------------------------------------------------------
    # 原本層: salary_record
    # ----------------------------------------------------------------
    op.create_table(
        "salary_record",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=True),
        sa.Column("includes_bonus", sa.Boolean(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("is_estimated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ----------------------------------------------------------------
    # 原本層: revenue_record（BigInteger必須: Integer上限~21.4億円）
    # ----------------------------------------------------------------
    op.create_table(
        "revenue_record",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.BigInteger(), nullable=True),
        sa.Column("operating_profit", sa.BigInteger(), nullable=True),
        sa.Column("is_profitable", sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ----------------------------------------------------------------
    # 原本層: company_text（理念原文・弱み・口コミを kind で分類）
    # ----------------------------------------------------------------
    op.create_table(
        "company_text",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # ----------------------------------------------------------------
    # 特徴量層: feature_definition（全採用項目のマスタ）
    # ----------------------------------------------------------------
    op.create_table(
        "feature_definition",
        sa.Column("feature_key", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("category", sa.String(length=50), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("unit", sa.String(length=30), nullable=True),
        sa.Column("value_min", sa.Float(), nullable=True),
        sa.Column("value_max", sa.Float(), nullable=True),
        sa.Column("direction", sa.String(length=15), nullable=False, server_default="neutral"),
        sa.Column("default_weight", sa.Float(), nullable=False),
        sa.Column("has_official_actual", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("rubric", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.CheckConstraint(
            "method IN ('direct','scale5','bool','tag','onehot')",
            name="ck_feature_def_method",
        ),
        sa.CheckConstraint(
            "direction IN ('high_good','low_good','neutral')",
            name="ck_feature_def_direction",
        ),
        sa.PrimaryKeyConstraint("feature_key"),
    )

    # ----------------------------------------------------------------
    # 特徴量層: company_feature（縦持ちEAV）
    # ----------------------------------------------------------------
    op.create_table(
        "company_feature",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=True),
        sa.Column("feature_key", sa.String(length=100), nullable=False),
        sa.Column("value_numeric", sa.Float(), nullable=True),
        sa.Column("value_normalized", sa.Float(), nullable=True),
        sa.Column("value_official", sa.Float(), nullable=True),
        sa.Column("value_actual", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("source_type", sa.String(length=20), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=True),
        sa.Column("is_estimated", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source_type IN ('official','review','estimated')",
            name="ck_company_feature_source_type",
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["feature_key"], ["feature_definition.feature_key"]),
        sa.ForeignKeyConstraint(["role_id"], ["job_role.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # NULLS NOT DISTINCT: role_id=NULL（全社）行の重複を防ぐ
    op.execute(
        "CREATE UNIQUE INDEX uq_company_feature "
        "ON company_feature (company_id, role_id, feature_key) NULLS NOT DISTINCT"
    )

    # ----------------------------------------------------------------
    # ユーザー層: user_preference（CompanyFeatureと対称）
    # ----------------------------------------------------------------
    op.create_table(
        "user_preference",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_profile_id", sa.UUID(), nullable=False),
        sa.Column("feature_key", sa.String(length=100), nullable=False),
        sa.Column("desired_value", sa.Float(), nullable=True),
        sa.Column("desired_min", sa.Float(), nullable=True),
        sa.Column("desired_max", sa.Float(), nullable=True),
        sa.Column("desired_tags", sa.JSON(), nullable=True),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("is_hard_filter", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(
            ["feature_key"], ["feature_definition.feature_key"]
        ),
        sa.ForeignKeyConstraint(
            ["user_profile_id"], ["user_profile.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_profile_id", "feature_key"),
    )

    # ----------------------------------------------------------------
    # VIEW: company_completeness（NULLを0扱いしないペナルティ可視化）
    # ----------------------------------------------------------------
    op.execute("""
        CREATE VIEW company_completeness AS
        SELECT
            c.id AS company_id,
            c.name AS company_name,
            count(cf.id) FILTER (
                WHERE cf.value_numeric  IS NOT NULL
                   OR cf.value_official IS NOT NULL
                   OR cf.value_actual   IS NOT NULL
            ) AS filled,
            (SELECT count(*) FROM feature_definition WHERE default_weight > 0 AND is_active) AS applicable,
            round(
                count(cf.id) FILTER (
                    WHERE cf.value_numeric  IS NOT NULL
                       OR cf.value_official IS NOT NULL
                       OR cf.value_actual   IS NOT NULL
                )::numeric
                / NULLIF(
                    (SELECT count(*) FROM feature_definition WHERE default_weight > 0 AND is_active),
                    0
                )
            , 2) AS completeness_ratio
        FROM company c
        LEFT JOIN company_feature cf ON cf.company_id = c.id
        GROUP BY c.id, c.name
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS company_completeness")
    op.drop_table("user_preference")
    op.drop_table("company_feature")
    op.drop_table("feature_definition")
    op.drop_table("company_text")
    op.drop_table("revenue_record")
    op.drop_table("salary_record")
    op.drop_table("company_tech")
    op.drop_table("tech_tag")
    op.drop_table("company_industry")
    op.drop_table("industry_master")
    op.drop_table("office")
    op.drop_table("job_role")
    op.drop_column("company", "engineer_count")
    op.drop_column("company", "has_relocation")
