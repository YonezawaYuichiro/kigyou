"""FastAPI ルーター定義（V3 Phase 6）。

エンドポイント:
  POST /api/v2/profile          UserProfile 保存
  GET  /api/v2/matches          マッチング実行
  GET  /api/v2/companies        企業一覧（フィルター・ページング）
  GET  /api/v2/companies/{id}   企業詳細
  GET  /api/v2/health           ヘルスチェック
"""

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy.orm import selectinload

from backend.api.matching_engine import compute_matches
from backend.api.profile_manager import load_or_create_profile, save_profile
from backend.api.schemas import (
    CompanyDetailResponse,
    CompanyDimensionsSchema,
    CompanyListItem,
    CompanyListResponse,
    MatchItem,
    MatchResponse,
    ProfileRequest,
    ProfileResponse,
)
from backend.database import get_session
from backend.models import Company, CompanyDimensions, CompanyMetrics, CompanyVector

router = APIRouter(prefix="/api/v2")


# ── ヘルスチェック ─────────────────────────────────────────────────────────────


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


# ── プロフィール ───────────────────────────────────────────────────────────────


@router.post("/profile", response_model=ProfileResponse)
def post_profile(body: ProfileRequest) -> ProfileResponse:
    """UserProfile を保存し、tech_level_score と dimension_weights を返す。"""
    saved = save_profile(
        session_id=body.session_id,
        tech_skills=body.tech_skills,
        qualifications=body.qualifications,
        project_experience=body.project_experience,
        architecture_experience=body.architecture_experience,
        hard_constraints=body.hard_constraints,
        soft_preferences=body.soft_preferences,
        graduation_year=body.graduation_year,
        major=body.major,
        target_industries=body.target_industries,
        target_roles=body.target_roles,
        dev_phase_preference=body.dev_phase_preference,
        min_salary=body.min_salary,
        mbti=body.mbti,
        eval_preference=body.eval_preference,
        psych_safety_importance=body.psych_safety_importance,
        github_summary=body.github_summary,
        recompute_level=body.recompute_level,
    )
    return ProfileResponse(
        session_id=saved.session_id,
        tech_level_score=saved.tech_level_score,
        tech_level_rationale=saved.tech_level_rationale,
        dimension_weights=list(saved.dimension_weights) if saved.dimension_weights else None,
        graduation_year=saved.graduation_year,
        major=saved.major,
        target_industries=list(saved.target_industries) if saved.target_industries else None,
        target_roles=list(saved.target_roles) if saved.target_roles else None,
        dev_phase_preference=saved.dev_phase_preference,
        min_salary=saved.min_salary,
        eval_preference=saved.eval_preference,
        psych_safety_importance=saved.psych_safety_importance,
    )


# ── マッチング ────────────────────────────────────────────────────────────────


@router.get("/matches", response_model=MatchResponse)
def get_matches(session_id: str = Query(..., description="ブラウザセッションID")) -> MatchResponse:
    """session_id に紐づく UserProfile でマッチングを実行する。"""
    user_profile = load_or_create_profile(session_id)
    result = compute_matches(user_profile)

    def _to_item(r: dict, rank_key: str) -> MatchItem:
        return MatchItem(
            rank=r.get(rank_key, 0),
            company_id=r["company_id"],
            name=r["name"],
            official_url=r.get("official_url"),
            hq_prefecture=r.get("hq_prefecture"),
            estimated_category=r.get("estimated_category"),
            tech_stack=r.get("tech_stack", []),
            ideal_score=r["ideal_score"],
            realistic_score=r["realistic_score"],
            openwork_score=r.get("openwork_score"),
            avg_overtime_hours=r.get("avg_overtime_hours"),
            avg_annual_salary=r.get("avg_annual_salary"),
            remote_work_policy=r.get("remote_work_policy"),
            overall_confidence=r.get("overall_confidence"),
            dim_scores=r.get("dim_scores"),
        )

    return MatchResponse(
        session_id=session_id,
        tech_level=user_profile.tech_level_score or 0.5,
        dimension_weights=result.get("dimension_weights", [0.1] * 10),
        ideal=[_to_item(r, "rank_ideal") for r in result.get("ideal", [])],
        realistic=[_to_item(r, "rank_realistic") for r in result.get("realistic", [])],
    )


# ── 企業一覧 ──────────────────────────────────────────────────────────────────


@router.get("/companies", response_model=CompanyListResponse)
def get_companies(
    category: str | None = Query(None, description="カテゴリ絞り込み"),
    prefecture: str | None = Query(None, description="都道府県絞り込み"),
    remote: str | None = Query(None, description="full/partial/none"),
    min_confidence: float = Query(0.0, ge=0.0, le=1.0, description="最低信頼度"),
    page: int = Query(1, ge=1, description="ページ番号（1始まり）"),
    limit: int = Query(20, ge=1, le=100, description="1ページの件数"),
) -> CompanyListResponse:
    """企業一覧をフィルター・ページング付きで返す。"""
    with get_session() as session:
        stmt = sa.select(Company).options(
            selectinload(Company.metrics),
            selectinload(Company.dimensions),
        )
        if category:
            stmt = stmt.where(Company.estimated_category == category)
        if prefecture:
            stmt = stmt.where(Company.hq_prefecture == prefecture)

        companies = session.execute(stmt).scalars().all()

    # Python側フィルター（pgvectorの型制約でSQLでやりにくい項目）
    items: list[CompanyListItem] = []
    for c in companies:
        m: CompanyMetrics | None = c.metrics
        d: CompanyDimensions | None = c.dimensions
        if remote and (not m or m.remote_work_policy != remote):
            continue
        conf = d.overall_confidence if d else None
        if conf is not None and conf < min_confidence:
            continue
        items.append(
            CompanyListItem(
                id=str(c.id),
                name=c.name,
                official_url=c.official_url,
                hq_prefecture=c.hq_prefecture,
                estimated_category=c.estimated_category,
                tech_stack=list(c.tech_stack or []),
                openwork_score=m.openwork_score if m else None,
                avg_overtime_hours=m.avg_overtime_hours if m else None,
                avg_annual_salary=m.avg_annual_salary if m else None,
                employee_count=m.employee_count if m else None,
                remote_work_policy=m.remote_work_policy if m else None,
                overall_confidence=conf,
            )
        )

    total = len(items)
    start = (page - 1) * limit
    return CompanyListResponse(
        total=total,
        page=page,
        limit=limit,
        items=items[start : start + limit],
    )


# ── 企業詳細 ──────────────────────────────────────────────────────────────────


@router.get("/companies/{company_id}", response_model=CompanyDetailResponse)
def get_company(company_id: str) -> CompanyDetailResponse:
    """企業IDで詳細情報を返す。"""
    with get_session() as session:
        company = session.execute(
            sa.select(Company)
            .options(
                selectinload(Company.metrics),
                selectinload(Company.dimensions),
                selectinload(Company.vector),
            )
            .where(sa.cast(Company.id, sa.String) == company_id)
        ).scalar_one_or_none()

    if not company:
        raise HTTPException(status_code=404, detail=f"企業が見つかりません: {company_id}")

    m: CompanyMetrics | None = company.metrics
    d: CompanyDimensions | None = company.dimensions
    v: CompanyVector | None = company.vector

    dims = None
    if d:
        dims = CompanyDimensionsSchema(
            overall_confidence=d.overall_confidence,
            psychological_safety_score=d.psychological_safety_score,
            psychological_safety_evidence=d.psychological_safety_evidence,
            junior_authority_score=d.junior_authority_score,
            junior_authority_evidence=d.junior_authority_evidence,
            tech_env_evidence=d.tech_env_evidence,
            tech_modernity_score=d.tech_modernity_score,
            infra_cloud_score=d.infra_cloud_score,
            cicd_maturity_score=d.cicd_maturity_score,
            data_platform_score=d.data_platform_score,
            tech_debt_culture_score=d.tech_debt_culture_score,
            new_biz_policy_score=d.new_biz_policy_score,
            new_biz_policy_evidence=d.new_biz_policy_evidence,
            competitive_advantage_score=d.competitive_advantage_score,
            evaluation_system_type=d.evaluation_system_type,
            skill_support_score=d.skill_support_score,
            skill_support_items=list(d.skill_support_items) if d.skill_support_items else None,
            has_coding_test=d.has_coding_test,
            interviewer_type=d.interviewer_type,
            megatrend_score=d.megatrend_score,
            megatrend_alignment=list(d.megatrend_alignment) if d.megatrend_alignment else None,
        )

    return CompanyDetailResponse(
        id=str(company.id),
        name=company.name,
        official_url=company.official_url,
        hq_prefecture=company.hq_prefecture,
        estimated_category=company.estimated_category,
        tech_stack=list(company.tech_stack or []),
        hiring_roles=list(company.hiring_roles or []),
        openwork_score=m.openwork_score if m else None,
        avg_overtime_hours=m.avg_overtime_hours if m else None,
        avg_annual_salary=m.avg_annual_salary if m else None,
        employee_count=m.employee_count if m else None,
        remote_work_policy=m.remote_work_policy if m else None,
        is_listed=m.is_listed if m else None,
        founded_year=m.founded_year if m else None,
        average_age=m.average_age if m else None,
        ow_score_growth=m.ow_score_growth if m else None,
        ow_score_morale=m.ow_score_morale if m else None,
        ow_score_openness=m.ow_score_openness if m else None,
        openwork_url=m.openwork_url if m else None,
        green_url=m.green_url if m else None,
        dimensions=dims,
        dim_scores=list(v.dim_scores) if v and v.dim_scores else None,
    )
