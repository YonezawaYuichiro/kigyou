"""FastAPI用 Pydantic v2 スキーマ定義。

SQLAlchemyモデルとの橋渡し。リクエスト/レスポンスの型を定義する。
"""

from pydantic import BaseModel, Field

# ── プロフィール ──────────────────────────────────────────────────────────────


class ProfileRequest(BaseModel):
    session_id: str
    tech_skills: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)
    project_experience: str = ""
    architecture_experience: list[str] = Field(default_factory=list)
    hard_constraints: dict = Field(default_factory=dict)
    soft_preferences: dict = Field(default_factory=dict)
    # V3 拡張フィールド
    graduation_year: int | None = None
    major: str | None = None
    target_industries: list[str] | None = None
    target_roles: list[str] | None = None
    dev_phase_preference: str | None = None
    min_salary: int | None = None
    mbti: str | None = None
    eval_preference: str | None = None
    psych_safety_importance: float | None = None
    github_summary: str | None = None
    projects: list[dict] | None = None
    recompute_level: bool = True


class ProfileResponse(BaseModel):
    session_id: str
    tech_level_score: float | None
    tech_level_rationale: str | None
    dimension_weights: list[float] | None
    graduation_year: int | None
    major: str | None
    target_industries: list[str] | None
    target_roles: list[str] | None
    dev_phase_preference: str | None
    min_salary: int | None
    eval_preference: str | None
    psych_safety_importance: float | None
    projects: list[dict] | None


# ── 企業一覧 ──────────────────────────────────────────────────────────────────


class CompanyListItem(BaseModel):
    id: str
    name: str
    official_url: str | None
    hq_prefecture: str | None
    estimated_category: str | None
    tech_stack: list[str]
    openwork_score: float | None
    avg_overtime_hours: float | None
    avg_annual_salary: int | None
    employee_count: int | None
    remote_work_policy: str | None
    overall_confidence: float | None


class CompanyListResponse(BaseModel):
    total: int
    page: int
    limit: int
    items: list[CompanyListItem]


# ── 企業詳細 ──────────────────────────────────────────────────────────────────


class CompanyDimensionsSchema(BaseModel):
    overall_confidence: float | None
    psychological_safety_score: float | None
    psychological_safety_evidence: str | None
    junior_authority_score: float | None
    junior_authority_evidence: str | None
    tech_env_evidence: str | None
    tech_modernity_score: float | None
    infra_cloud_score: float | None
    cicd_maturity_score: float | None
    data_platform_score: float | None
    tech_debt_culture_score: float | None
    new_biz_policy_score: float | None
    new_biz_policy_evidence: str | None
    competitive_advantage_score: float | None
    evaluation_system_type: str | None
    skill_support_score: float | None
    skill_support_items: list[str] | None
    has_coding_test: bool | None
    interviewer_type: str | None
    megatrend_score: float | None
    megatrend_alignment: list[str] | None


class CompanyDetailResponse(BaseModel):
    id: str
    name: str
    official_url: str | None
    hq_prefecture: str | None
    estimated_category: str | None
    tech_stack: list[str]
    hiring_roles: list[str]
    # メトリクス
    openwork_score: float | None
    avg_overtime_hours: float | None
    avg_annual_salary: int | None
    employee_count: int | None
    remote_work_policy: str | None
    is_listed: bool | None
    founded_year: int | None
    average_age: float | None
    ow_score_growth: float | None
    ow_score_morale: float | None
    ow_score_openness: float | None
    openwork_url: str | None
    green_url: str | None
    # ディメンション
    dimensions: CompanyDimensionsSchema | None
    # ベクトル
    dim_scores: list[float] | None


# ── マッチング ────────────────────────────────────────────────────────────────


class MatchItem(BaseModel):
    rank: int
    company_id: str
    name: str
    official_url: str | None
    hq_prefecture: str | None
    estimated_category: str | None
    tech_stack: list[str]
    ideal_score: float
    realistic_score: float
    openwork_score: float | None
    avg_overtime_hours: float | None
    avg_annual_salary: int | None
    remote_work_policy: str | None
    overall_confidence: float | None
    dim_scores: list[float] | None


class MatchResponse(BaseModel):
    session_id: str
    tech_level: float
    dimension_weights: list[float]
    ideal: list[MatchItem]
    realistic: list[MatchItem]
