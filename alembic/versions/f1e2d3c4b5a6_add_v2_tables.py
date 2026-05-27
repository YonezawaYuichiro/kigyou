"""add_v2_tables

Revision ID: f1e2d3c4b5a6
Revises: ad2fa147311f
Create Date: 2026-05-27 00:00:00.000000

V2で追加する4テーブル:
  - company_dimensions: Gemini+Haiku抽出の★26項目 + 証拠テキスト
  - company_vector: 10次元スコアベクトル (pgvector)
  - user_profile: ユーザープロフィールDB版
  - match_result: マッチング結果スナップショット

VECTOR型はSQLAlchemy autogenerateで検出されないため手動記述。
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1e2d3c4b5a6"
down_revision: str | None = "ad2fa147311f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector拡張を有効化（既存の場合はスキップ）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # company_dimensions
    op.create_table(
        "company_dimensions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        # ビジョン・新規事業 (dim[1])
        sa.Column("new_biz_policy_score", sa.Float(), nullable=True),
        sa.Column("new_biz_policy_evidence", sa.Text(), nullable=True),
        # ビジネスモデル (dim[2])
        sa.Column("competitive_advantage_score", sa.Float(), nullable=True),
        sa.Column("competitive_advantage_evidence", sa.Text(), nullable=True),
        sa.Column("has_patent", sa.Boolean(), nullable=True),
        # 財務 (dim[3])
        sa.Column("rd_ratio", sa.Float(), nullable=True),
        sa.Column("rd_ratio_evidence", sa.Text(), nullable=True),
        sa.Column("capex_ratio", sa.Float(), nullable=True),
        # 業界動向 (dim[4])
        sa.Column("megatrend_alignment", sa.JSON(), nullable=True),
        sa.Column("megatrend_score", sa.Float(), nullable=True),
        # 組織カルチャー (dim[5])
        sa.Column("psychological_safety_score", sa.Float(), nullable=True),
        sa.Column("psychological_safety_evidence", sa.Text(), nullable=True),
        # 人事・キャリア (dim[6])
        sa.Column("evaluation_score", sa.Float(), nullable=True),
        sa.Column("evaluation_system_type", sa.String(length=20), nullable=True),
        sa.Column("career_track_diversity", sa.Boolean(), nullable=True),
        sa.Column("skill_support_score", sa.Float(), nullable=True),
        sa.Column("skill_support_items", sa.JSON(), nullable=True),
        sa.Column("junior_authority_score", sa.Float(), nullable=True),
        sa.Column("junior_authority_evidence", sa.Text(), nullable=True),
        # 採用 (dim[8])
        sa.Column("has_coding_test", sa.Boolean(), nullable=True),
        sa.Column("interviewer_type", sa.String(length=30), nullable=True),
        # 開発環境 (dim[9])
        sa.Column("tech_modernity_score", sa.Float(), nullable=True),
        sa.Column("infra_cloud_score", sa.Float(), nullable=True),
        sa.Column("cicd_maturity_score", sa.Float(), nullable=True),
        sa.Column("hw_sw_integration", sa.Boolean(), nullable=True),
        sa.Column("data_platform_score", sa.Float(), nullable=True),
        sa.Column("tech_debt_culture_score", sa.Float(), nullable=True),
        sa.Column("tech_env_evidence", sa.Text(), nullable=True),
        # メタ・信頼度
        sa.Column("extraction_model", sa.String(length=50), nullable=True),
        sa.Column("overall_confidence", sa.Float(), nullable=True),
        sa.Column("low_confidence_fields", sa.JSON(), nullable=True),
        sa.Column("search_sources_used", sa.JSON(), nullable=True),
        sa.Column("last_extracted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )

    # company_vector: 通常カラムのみ作成し、VECTOR列はexecuteで追加
    op.create_table(
        "company_vector",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column(
            "model_version",
            sa.String(length=20),
            server_default="v2.0",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id"),
    )
    # VECTOR型はop.create_tableでは定義できないため別途追加
    op.execute(
        "ALTER TABLE company_vector ADD COLUMN dim_scores vector(10)"
    )

    # user_profile
    op.create_table(
        "user_profile",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("session_id", sa.String(length=100), nullable=False),
        sa.Column("tech_skills", sa.JSON(), nullable=True),
        sa.Column("qualifications", sa.JSON(), nullable=True),
        sa.Column("project_experience", sa.Text(), nullable=True),
        sa.Column("architecture_experience", sa.JSON(), nullable=True),
        sa.Column("tech_level_score", sa.Float(), nullable=True),
        sa.Column("tech_level_rationale", sa.Text(), nullable=True),
        sa.Column("hard_constraints", sa.JSON(), nullable=True),
        sa.Column("soft_preferences", sa.JSON(), nullable=True),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id"),
    )
    op.execute(
        "ALTER TABLE user_profile ADD COLUMN dimension_weights vector(10)"
    )

    # match_result
    op.create_table(
        "match_result",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_profile_id", sa.UUID(), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("ideal_score", sa.Float(), nullable=True),
        sa.Column("realistic_score", sa.Float(), nullable=True),
        sa.Column("hard_filter_passed", sa.Boolean(), nullable=False),
        sa.Column("rank_ideal", sa.Integer(), nullable=True),
        sa.Column("rank_realistic", sa.Integer(), nullable=True),
        sa.Column("score_breakdown", sa.JSON(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_profile_id"], ["user_profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("match_result")
    op.drop_table("user_profile")
    op.drop_table("company_vector")
    op.drop_table("company_dimensions")
    op.execute("DROP EXTENSION IF EXISTS vector")
