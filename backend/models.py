import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
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
    has_relocation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    engineer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    listing_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # 上場/未上場/グループ/東証プライム/東証スタンダード/東証グロース
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    target_market: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # BtoB / BtoC / BtoBtoC / 製造 / Web 等（簡易1列版。Phase 5でタグ化予定）
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


# ============================================================
# V5 新規テーブル: 3層DB構成（原本層 / 特徴量層 / ユーザー層）
# ============================================================

# --- 原本層 (Source of Truth) ---


class JobRole(Base):
    """company × role 粒度の受け皿。v1はrole_id=NULL（全社）中心。"""

    __tablename__ = "job_role"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    role_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # backend / frontend / ml / infra / embedded / data / qa
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)


class Office(Base):
    __tablename__ = "office"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    is_dev_site: Mapped[bool] = mapped_column(Boolean, default=False)


class IndustryMaster(Base):
    __tablename__ = "industry_master"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)


class CompanyIndustry(Base):
    __tablename__ = "company_industry"
    __table_args__ = (PrimaryKeyConstraint("company_id", "industry_id"),)

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    industry_id: Mapped[int] = mapped_column(ForeignKey("industry_master.id"))


class TechTag(Base):
    __tablename__ = "tech_tag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    # language / framework / cloud / mlops / data / devops / hardware / database


class CompanyTech(Base):
    __tablename__ = "company_tech"
    __table_args__ = (
        # PG16: NULLS NOT DISTINCT で role_id=NULL（全社）行の重複を防ぐ
        UniqueConstraint(
            "company_id",
            "tech_tag_id",
            "role_id",
            postgresql_nulls_not_distinct=True,
            name="uq_company_tech",
        ),
        CheckConstraint(
            "source_type IN ('official','review','estimated')",
            name="ck_company_tech_source_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    tech_tag_id: Mapped[int] = mapped_column(ForeignKey("tech_tag.id"))
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_role.id"), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class SalaryRecord(Base):
    __tablename__ = "salary_record"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # 初任給_学部 / 初任給_院 / 30歳平均 / 3年後 / 5年後 / 賞与込想定_30歳
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)
    includes_bonus: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RevenueRecord(Base):
    __tablename__ = "revenue_record"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # BigInteger必須: Integer上限は約21.4億円。中堅企業でもオーバーフローする
    revenue: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    operating_profit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_profitable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class CompanyText(Base):
    """理念原文・弱み・求める人物像・口コミをkindで分類して一テーブルに統合。"""

    __tablename__ = "company_text"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # "values" / "weakness" / "persona" / "review"
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)


# --- 特徴量層 (Feature Store) ---


class FeatureDefinition(Base):
    """全採用項目のマスタ。method / direction のCheckConstraintでDB側バリデーション。"""

    __tablename__ = "feature_definition"
    __table_args__ = (
        CheckConstraint(
            "method IN ('direct','scale5','bool','tag','onehot')",
            name="ck_feature_def_method",
        ),
        CheckConstraint(
            "direction IN ('high_good','low_good','neutral')",
            name="ck_feature_def_direction",
        ),
    )

    feature_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    # "direct" / "scale5" / "bool" / "tag" / "onehot"
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    value_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction: Mapped[str] = mapped_column(String(15), default="neutral")
    # "high_good" / "low_good" / "neutral"
    default_weight: Mapped[float] = mapped_column(Float, nullable=False)
    # ★=1.0 / 〇=0.5 / 再考✕=0.2
    has_official_actual: Mapped[bool] = mapped_column(Boolean, default=False)
    # True=company_featureにvalue_official+value_actualの両列で格納する
    rubric: Mapped[str | None] = mapped_column(Text, nullable=True)
    # scale5必須: "5=... | 3=... | 1=..." 形式。空欄は未完扱い
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class CompanyFeature(Base):
    """縦持ちEAV。UNIQUE(company_id, role_id, feature_key)。role_id=NULLは全社値。"""

    __tablename__ = "company_feature"
    __table_args__ = (
        # PG16: NULLS NOT DISTINCT で role_id=NULL（全社）行の重複を防ぐ
        UniqueConstraint(
            "company_id",
            "role_id",
            "feature_key",
            postgresql_nulls_not_distinct=True,
            name="uq_company_feature",
        ),
        CheckConstraint(
            "source_type IN ('official','review','estimated')",
            name="ck_company_feature_source_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_role.id"), nullable=True)
    feature_key: Mapped[str] = mapped_column(
        ForeignKey("feature_definition.feature_key"), nullable=False
    )

    # 値スロット（NULL=不明。0=ゼロ値。絶対に混同しない）
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 直接値 / scale5の生値
    # v1はfeature_definition.value_min/maxによる固定正規化のみ。相対正規化はPhase 5以降
    value_normalized: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 0-1正規化後（マッチング用）
    value_official: Mapped[float | None] = mapped_column(Float, nullable=True)
    # has_official_actual=TRUEの項目: 制度・公称値
    value_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    # has_official_actual=TRUEの項目: 実態（口コミ/推定）

    # メタ（全値共通）
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # "official" / "review" / "estimated"
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# --- ユーザー層 ---


class UserPreference(Base):
    """企業側CompanyFeatureと対称な希望値。weight=NULLならdefault_weightを使用。"""

    __tablename__ = "user_preference"
    __table_args__ = (UniqueConstraint("user_profile_id", "feature_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_profile.id", ondelete="CASCADE")
    )
    feature_key: Mapped[str] = mapped_column(
        ForeignKey("feature_definition.feature_key"), nullable=False
    )
    desired_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    desired_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    desired_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    # tag系feature用: 希望するtech_tag.idのリスト。FKが効かないのでアプリ側で実在チェック必須
    desired_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # NULL → feature_definition.default_weight を使用
    is_hard_filter: Mapped[bool] = mapped_column(Boolean, default=False)
