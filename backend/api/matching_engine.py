"""V2 マッチングエンジン。

企業の10次元ベクトル（CompanyVector）とユーザーの重みベクトル（UserProfile.dimension_weights）を
pgvector コサイン類似度でマッチングし、理想スコアと現実的スコアを算出する。

処理フロー:
  Step 1: ハードフィルタ（残業・リモート・地域）で企業を絞り込む
  Step 2: pgvector コサイン類似度で ideal_score を算出（TOP 50）
  Step 3: tech_level_score と開発環境次元（dim[9]）のギャップで realistic_score を割引
"""

import logging
import re
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from backend.api.scorer import _passes_hard_filters
from backend.database import get_session
from backend.models import Company, CompanyDimensions, CompanyMetrics, CompanyVector, UserProfile

logger = logging.getLogger(__name__)

_TOP_K = 50


def _normalize_pref(p: str) -> str:
    """都/道/府/県 サフィックスを除去して正規化する（「大阪府」→「大阪」）。"""
    return re.sub(r"[都道府県]$", "", p.strip())


def _build_filter_set(hard_constraints: dict[str, Any]) -> set[str]:
    """ハードフィルタを通過した企業IDの集合を返す（str(UUID)）。"""
    max_ot = hard_constraints.get("max_overtime_hours")
    min_score = hard_constraints.get("min_openwork_score")
    remote_ok = hard_constraints.get("remote_work", [])
    prefectures = [_normalize_pref(p) for p in hard_constraints.get("preferred_prefectures", [])]
    categories = hard_constraints.get("preferred_categories", [])

    passed: set[str] = set()
    with get_session() as session:
        companies = (
            session.execute(sa.select(Company).options(selectinload(Company.metrics)))
            .scalars()
            .all()
        )
        for company in companies:
            m = company.metrics
            if not _passes_hard_filters(
                m, {"max_overtime_hours": max_ot, "min_openwork_score": min_score}
            ):
                continue
            if remote_ok and m and m.remote_work_policy and m.remote_work_policy not in remote_ok:
                continue
            if prefectures and _normalize_pref(company.hq_prefecture or "") not in prefectures:
                continue
            if categories and company.estimated_category not in categories:
                continue
            passed.add(str(company.id))
    return passed


def _query_cosine_matches(weights: list[float], filtered_ids: set[str]) -> list[dict[str, Any]]:
    """pgvector でコサイン類似度 TOP_K を取得する。"""
    if not filtered_ids:
        return []

    vec_str = "[" + ",".join(str(round(w, 6)) for w in weights) + "]"
    sql = sa.text("""
        SELECT
            cv.company_id::text AS company_id,
            1 - (cv.dim_scores <=> :vec ::vector) AS ideal_score,
            (cv.dim_scores::real[])[10] AS tech_demand_raw
        FROM company_vector cv
        WHERE cv.company_id::text = ANY(:ids)
          AND cv.dim_scores IS NOT NULL
        ORDER BY cv.dim_scores <=> :vec ::vector
        LIMIT :limit
    """)

    with get_session() as session:
        rows = session.execute(
            sql,
            {"vec": vec_str, "ids": list(filtered_ids), "limit": _TOP_K},
        ).fetchall()

    return [
        {
            "company_id": row.company_id,
            "ideal_score": float(row.ideal_score),
            "tech_demand": float(row.tech_demand_raw or 0.5),
        }
        for row in rows
    ]


def _compute_realistic(ideal_score: float, tech_demand: float, tech_level: float) -> float:
    """合格可能性スコアを算出する。tech_demand が高く tech_level が低いほど割引。"""
    gap = max(0.0, tech_demand - tech_level)
    penalty = gap * 0.5
    return round(max(0.0, ideal_score * (1.0 - penalty)), 4)


def _enrich_results(matches: list[dict[str, Any]], tech_level: float) -> list[dict[str, Any]]:
    """company_id から会社情報・ディメンション情報を結合して返す。"""
    if not matches:
        return []

    ids = [m["company_id"] for m in matches]
    enriched: list[dict[str, Any]] = []
    with get_session() as session:
        companies = {
            str(c.id): c
            for c in session.execute(
                sa.select(Company)
                .options(
                    selectinload(Company.metrics),
                    selectinload(Company.dimensions),
                    selectinload(Company.vector),
                )
                .where(sa.cast(Company.id, sa.String).in_(ids))
            )
            .scalars()
            .all()
        }
        for m in matches:
            company = companies.get(m["company_id"])
            if not company:
                continue
            metrics: CompanyMetrics | None = company.metrics
            dims: CompanyDimensions | None = company.dimensions
            vec: CompanyVector | None = company.vector
            realistic = _compute_realistic(m["ideal_score"], m["tech_demand"], tech_level)
            enriched.append(
                {
                    "company_id": m["company_id"],
                    "name": company.name,
                    "official_url": company.official_url,
                    "hq_prefecture": company.hq_prefecture,
                    "estimated_category": company.estimated_category,
                    "tech_stack": list(company.tech_stack or []),
                    "ideal_score": round(m["ideal_score"], 4),
                    "realistic_score": realistic,
                    "tech_demand": m["tech_demand"],
                    # CompanyMetrics
                    "openwork_score": metrics.openwork_score if metrics else None,
                    "avg_overtime_hours": metrics.avg_overtime_hours if metrics else None,
                    "avg_annual_salary": metrics.avg_annual_salary if metrics else None,
                    "employee_count": metrics.employee_count if metrics else None,
                    "remote_work_policy": metrics.remote_work_policy if metrics else None,
                    "openwork_url": metrics.openwork_url if metrics else None,
                    "green_url": metrics.green_url if metrics else None,
                    # CompanyDimensions
                    "overall_confidence": dims.overall_confidence if dims else None,
                    "tech_env_evidence": dims.tech_env_evidence if dims else None,
                    "psychological_safety_score": dims.psychological_safety_score if dims else None,
                    "psychological_safety_evidence": (
                        dims.psychological_safety_evidence if dims else None
                    ),
                    "junior_authority_score": dims.junior_authority_score if dims else None,
                    "junior_authority_evidence": (dims.junior_authority_evidence if dims else None),
                    "new_biz_policy_evidence": dims.new_biz_policy_evidence if dims else None,
                    "has_coding_test": dims.has_coding_test if dims else None,
                    # CompanyVector: 10次元スコア
                    "dim_scores": list(vec.dim_scores)
                    if vec and vec.dim_scores is not None
                    else None,
                }
            )
    return enriched


def _count_companies_with_vector(filtered_ids: set[str]) -> int:
    """filtered_ids のうち company_vector が存在する企業数を返す。"""
    if not filtered_ids:
        return 0
    sql = sa.text("""
        SELECT COUNT(*) FROM company_vector cv
        WHERE cv.company_id::text = ANY(:ids) AND cv.dim_scores IS NOT NULL
    """)
    with get_session() as session:
        return int(session.execute(sql, {"ids": list(filtered_ids)}).scalar() or 0)


def _count_all_companies() -> int:
    """DB上の全企業数を返す。"""
    with get_session() as session:
        return int(session.execute(sa.select(sa.func.count()).select_from(Company)).scalar() or 0)


def compute_matches(user_profile: UserProfile) -> dict[str, Any]:
    """マッチングを実行し、ideal/realistic ランキングを返す。

    Returns:
        {"ideal": [...], "realistic": [...], "dimension_weights": [...], "filter_stats": {...}}
    """
    hard = user_profile.hard_constraints or {}
    weights: list[float] = (
        list(user_profile.dimension_weights)
        if user_profile.dimension_weights is not None
        else [0.1] * 10
    )
    tech_level = user_profile.tech_level_score or 0.5

    logger.info("マッチング開始: tech_level=%.2f", tech_level)

    total_companies = _count_all_companies()
    filtered_ids = _build_filter_set(hard)
    has_vector = _count_companies_with_vector(filtered_ids)
    logger.info(
        "ハードフィルタ通過: %d/%d社 / ベクトルあり: %d社",
        len(filtered_ids),
        total_companies,
        has_vector,
    )

    filter_stats = {
        "total_companies": total_companies,
        "passed_hard_filter": len(filtered_ids),
        "has_vector": has_vector,
    }

    raw_matches = _query_cosine_matches(weights, filtered_ids)
    logger.info("ベクトル類似度取得: %d社", len(raw_matches))

    enriched = _enrich_results(raw_matches, tech_level)

    ideal = sorted(enriched, key=lambda x: x["ideal_score"], reverse=True)
    realistic = sorted(enriched, key=lambda x: x["realistic_score"], reverse=True)

    for i, r in enumerate(ideal, 1):
        r["rank_ideal"] = i
    for i, r in enumerate(realistic, 1):
        r["rank_realistic"] = i

    return {
        "ideal": ideal,
        "realistic": realistic,
        "dimension_weights": weights,
        "filter_stats": filter_stats,
    }
