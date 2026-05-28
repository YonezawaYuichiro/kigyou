import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "company"
    __table_args__ = (
        CheckConstraint(
            "corporate_number IS NULL OR length(corporate_number) = 13",
            name="ck_company_corporate_number_length",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # 法人番号API連携後に埋める。NULL同士はPostgreSQLでは重複とみなさない
    corporate_number: Mapped[str | None] = mapped_column(String(13), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    official_url: Mapped[str] = mapped_column(String(2048), unique=True, nullable=False)
    hq_prefecture: Mapped[str] = mapped_column(String(10), nullable=False)
    hq_address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    estimated_category: Mapped[str] = mapped_column(String(50), nullable=False)
    tech_stack: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    hiring_roles: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    llm_confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    release_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    fields: Mapped[list["CompanyField"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    metrics: Mapped["CompanyMetrics | None"] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )
    dimensions: Mapped["CompanyDimensions | None"] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )
    vector: Mapped["CompanyVector | None"] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )
    match_results: Mapped[list["MatchResult"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class CompanyField(Base):
    __tablename__ = "company_field"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # manual / llm_inferred / official_site / houjin_scraping / github_api / edinet_api
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    raw_value: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    company: Mapped["Company"] = relationship(back_populates="fields")


class CompanyMetrics(Base):
    """外部サイト（OpenWork / Green）から取得した企業の働き方・採用指標。"""

    __tablename__ = "company_metrics"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # OpenWork データ
    openwork_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    openwork_review_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    avg_overtime_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    paid_leave_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_annual_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Green / 求人サイト データ
    new_grad_salary_min: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_grad_salary_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remote_work_policy: Mapped[str | None] = mapped_column(String(20), nullable=True)
    new_grad_headcount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_current_openings: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Green 由来: 企業基本情報
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_age: Mapped[float | None] = mapped_column(Float, nullable=True)
    capital_10k_yen: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_listed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # OpenWork サブスコア（0.0〜5.0）
    ow_score_treatment: Mapped[float | None] = mapped_column(Float, nullable=True)
    ow_score_morale: Mapped[float | None] = mapped_column(Float, nullable=True)
    ow_score_openness: Mapped[float | None] = mapped_column(Float, nullable=True)
    ow_score_growth: Mapped[float | None] = mapped_column(Float, nullable=True)

    # メタ
    openwork_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    green_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped["Company"] = relationship(back_populates="metrics")


class ProcessingLog(Base):
    __tablename__ = "processing_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # phase_1_generate / phase_2_verify / phase_3_enrich / phase_4_load
    phase: Mapped[str] = mapped_column(String(50), nullable=False)
    # success / failure / partial
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    target_company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompanyDimensions(Base):
    """Gemini検索 + Haiku抽出による★26項目。各スコアに証拠テキストを添付し正確性を担保する。"""

    __tablename__ = "company_dimensions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    # ビジョン・新規事業 (dim[1])
    new_biz_policy_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    new_biz_policy_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ビジネスモデル (dim[2])
    competitive_advantage_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    competitive_advantage_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_patent: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # 財務 (dim[3])
    rd_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    rd_ratio_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)
    capex_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 業界動向 (dim[4])
    megatrend_alignment: Mapped[list | None] = mapped_column(JSON, nullable=True)
    megatrend_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 組織カルチャー (dim[5])
    psychological_safety_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    psychological_safety_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 人事・キャリア (dim[6])
    evaluation_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 成果主義 / 年功序列 / 混在 / 不明
    evaluation_system_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    career_track_diversity: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    skill_support_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    skill_support_items: Mapped[list | None] = mapped_column(JSON, nullable=True)
    junior_authority_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    junior_authority_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 採用 (dim[8])
    has_coding_test: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # current_engineer / hr_only / mixed / unknown
    interviewer_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 採用難易度スコア（10次元外・realistic_score割引に使用）
    hiring_difficulty_score: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 開発環境 (dim[9])
    tech_modernity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    infra_cloud_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    cicd_maturity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hw_sw_integration: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    data_platform_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tech_debt_culture_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tech_env_evidence: Mapped[str | None] = mapped_column(Text, nullable=True)

    # メタ・信頼度
    extraction_model: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # 証拠テキストが揃っているほど高くなる 0.0-1.0
    overall_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    low_confidence_fields: Mapped[list | None] = mapped_column(JSON, nullable=True)
    search_sources_used: Mapped[list | None] = mapped_column(JSON, nullable=True)
    last_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    company: Mapped["Company"] = relationship(back_populates="dimensions")


class CompanyVector(Base):
    """CompanyDimensions + CompanyMetricsから算術計算した10次元スコアベクトル（pgvector）。"""

    __tablename__ = "company_vector"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    # 10次元(V4): [自社開発度, 新規事業, 技術鮮度, 安定性, 育成投資, カルチャー, キャリア, WLB, 採用評価, 開発環境]
    dim_scores = mapped_column(Vector(10), nullable=True)
    model_version: Mapped[str] = mapped_column(String(20), nullable=False, server_default="v2.0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped["Company"] = relationship(back_populates="vector")


class UserProfile(Base):
    """my_profile.jsonの移行先。Sonnet 4.6でtech_level_scoreを算出し、10次元重みベクトルでマッチング。"""

    __tablename__ = "user_profile"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

    # スキル・経験
    tech_skills: Mapped[list | None] = mapped_column(JSON, nullable=True)
    qualifications: Mapped[list | None] = mapped_column(JSON, nullable=True)
    project_experience: Mapped[str | None] = mapped_column(Text, nullable=True)
    architecture_experience: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # 製作物リスト: [{name, type, description, tech_stack, is_ai, team_size, duration, repo_url}]
    projects: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Sonnet 4.6が算出する実務力スコア
    tech_level_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tech_level_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 基本情報
    graduation_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    major: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # 志望軸
    target_industries: Mapped[list | None] = mapped_column(JSON, nullable=True)
    target_roles: Mapped[list | None] = mapped_column(JSON, nullable=True)
    dev_phase_preference: Mapped[str | None] = mapped_column(String(50), nullable=True)
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 価値観・性格
    mbti: Mapped[str | None] = mapped_column(String(10), nullable=True)
    eval_preference: Mapped[str | None] = mapped_column(String(50), nullable=True)
    psych_safety_importance: Mapped[float | None] = mapped_column(Float, nullable=True)

    # 条件設定
    hard_constraints: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    soft_preferences: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # ユーザーの条件・優先度から算出した10次元重みベクトル
    dimension_weights = mapped_column(Vector(10), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    match_results: Mapped[list["MatchResult"]] = relationship(
        back_populates="user_profile", cascade="all, delete-orphan"
    )


class MatchResult(Base):
    """マッチング結果スナップショット。ideal_scoreは理想度、realistic_scoreは合格可能性。"""

    __tablename__ = "match_result"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_profile.id", ondelete="CASCADE"), nullable=False
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("company.id", ondelete="CASCADE"), nullable=False
    )

    ideal_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    realistic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    hard_filter_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rank_ideal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rank_realistic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user_profile: Mapped["UserProfile"] = relationship(back_populates="match_results")
    company: Mapped["Company"] = relationship(back_populates="match_results")
