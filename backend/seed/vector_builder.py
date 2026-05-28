"""Phase 3d: CompanyDimensions + CompanyMetrics から 10次元スコアベクトルを算術計算し CompanyVector にupsertする。

LLM呼び出し不要。各次元は 0.0-1.0 に正規化済み。

次元定義:
  dim[0] 立地スコア         (関西圏=1.0, その他主要都市=0.8, それ以外=0.5)
  dim[1] ビジョン積極度      (new_biz_policy_score)
  dim[2] ビジネスモデル堅牢性 (競合優位性 + 特許有無)
  dim[3] 財務健全性          (R&D比率 + 設備投資比率)
  dim[4] 業界トレンド適合度   (megatrend_score)
  dim[5] 組織カルチャー       (psychological_safety_score / 5.0)
  dim[6] キャリア成長支援     (評価制度 + 複線キャリア + スキル支援 + 若手裁量)
  dim[7] 待遇・WLB           (scorer.py の WLB計算を移植)
  dim[8] 採用透明度          (実技試験有無 + 面接官属性)
  dim[9] 開発環境モダン度     (5スコアの平均)
"""

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from backend.database import get_session
from backend.models import Company, CompanyDimensions, CompanyMetrics, CompanyVector

logger = logging.getLogger(__name__)

_KANSAI = {"大阪府", "京都府", "兵庫県"}
_KANSAI_ADJACENT = {"奈良県", "滋賀県", "和歌山県"}
_MAJOR_CITIES = {"東京都", "神奈川県", "愛知県", "福岡県", "宮城県", "北海道"}

_INTERVIEWER_SCORES = {
    "current_engineer": 1.0,
    "mixed": 0.7,
    "hr_only": 0.3,
    "unknown": 0.5,
}
_EVAL_TYPE_SCORES = {
    "成果主義": 0.9,
    "混在": 0.7,
    "年功序列": 0.4,
    "不明": 0.5,
}


def _dim0_location(prefecture: str | None) -> float:
    if prefecture in _KANSAI:
        return 1.0
    if prefecture in _KANSAI_ADJACENT:
        return 0.8
    if prefecture in _MAJOR_CITIES:
        return 0.7
    return 0.5


def _dim2_biz_model(dims: CompanyDimensions | None) -> float:
    if dims is None:
        return 0.5
    adv = dims.competitive_advantage_score or 0.5
    patent = 1.0 if dims.has_patent else (0.3 if dims.has_patent is False else 0.5)
    return round(adv * 0.6 + patent * 0.4, 3)


def _dim3_finance(dims: CompanyDimensions | None) -> float:
    if dims is None:
        return 0.3
    rd = dims.rd_ratio if dims.rd_ratio is not None else 0.3
    capex = dims.capex_ratio if dims.capex_ratio is not None else 0.3
    return round(min(1.0, (rd + capex) / 2.0), 3)


def _dim6_career(dims: CompanyDimensions | None) -> float:
    if dims is None:
        return 0.5
    eval_score = dims.evaluation_score or _EVAL_TYPE_SCORES.get(
        dims.evaluation_system_type or "", 0.5
    )
    diversity = (
        1.0
        if dims.career_track_diversity
        else (0.3 if dims.career_track_diversity is False else 0.5)
    )
    skill = dims.skill_support_score or 0.5
    junior = (dims.junior_authority_score or 2.5) / 5.0
    return round(eval_score * 0.3 + diversity * 0.3 + skill * 0.2 + junior * 0.2, 3)


def _dim7_wlb(metrics: CompanyMetrics | None) -> float:
    """scorer.py の WLB 計算ロジックを移植。"""
    if metrics is None:
        return 0.5
    overtime = metrics.avg_overtime_hours
    ot_score = max(0.0, 1.0 - overtime / 60.0) if overtime is not None else 0.5
    leave = float(metrics.paid_leave_rate) if metrics.paid_leave_rate is not None else 0.5
    remote = metrics.remote_work_policy
    remote_bonus = 0.15 if remote == "full" else (0.08 if remote == "partial" else 0.0)
    morale = metrics.ow_score_morale
    openness = metrics.ow_score_openness
    culture_vals = [s for s in [morale, openness] if s is not None]
    if culture_vals:
        culture = sum(culture_vals) / len(culture_vals) / 5.0
        base = ot_score * 0.4 + leave * 0.3 + culture * 0.3
    else:
        base = (ot_score + leave) / 2.0
    return round(min(1.0, base + remote_bonus), 3)


def _dim8_recruiting(dims: CompanyDimensions | None) -> float:
    if dims is None:
        return 0.5
    coding = 1.0 if dims.has_coding_test else (0.3 if dims.has_coding_test is False else 0.5)
    interviewer = _INTERVIEWER_SCORES.get(dims.interviewer_type or "unknown", 0.5)
    return round(coding * 0.5 + interviewer * 0.5, 3)


def _dim9_dev_env(dims: CompanyDimensions | None) -> float:
    if dims is None:
        return 0.5
    scores = [
        dims.tech_modernity_score,
        dims.infra_cloud_score,
        dims.cicd_maturity_score,
        dims.data_platform_score,
        dims.tech_debt_culture_score,
    ]
    valid = [s for s in scores if s is not None]
    if not valid:
        return 0.5
    hw_bonus = 0.05 if dims.hw_sw_integration else 0.0
    return round(min(1.0, sum(valid) / (5.0 * len(valid)) + hw_bonus), 3)


def build_vector(
    company: Company, dims: CompanyDimensions | None, metrics: CompanyMetrics | None
) -> list[float]:
    """10次元スコアベクトルを算術計算して返す。"""
    return [
        _dim0_location(company.hq_prefecture),
        round(dims.new_biz_policy_score or 0.5, 3) if dims else 0.5,
        _dim2_biz_model(dims),
        _dim3_finance(dims),
        round(dims.megatrend_score or 0.5, 3) if dims else 0.5,
        round((dims.psychological_safety_score or 2.5) / 5.0, 3) if dims else 0.5,
        _dim6_career(dims),
        _dim7_wlb(metrics),
        _dim8_recruiting(dims),
        _dim9_dev_env(dims),
    ]


def _upsert_vector(company_id: uuid.UUID, scores: list[float]) -> None:
    with get_session() as session:
        existing = session.execute(
            sa.select(CompanyVector).where(CompanyVector.company_id == company_id)
        ).scalar_one_or_none()
        if existing:
            existing.dim_scores = scores
            existing.model_version = "v2.0"
        else:
            session.add(
                CompanyVector(company_id=company_id, dim_scores=scores, model_version="v2.0")
            )


def build_all_vectors() -> None:
    """全企業の CompanyVector を算術計算してupsertする。"""
    with get_session() as session:
        companies: list[Company] = (
            session.execute(
                sa.select(Company).options(
                    selectinload(Company.dimensions),
                    selectinload(Company.metrics),
                )
            )
            .scalars()
            .all()
        )
        logger.info("ベクトル計算開始: %d社", len(companies))
        for i, company in enumerate(companies, 1):
            dims = company.dimensions
            metrics = company.metrics
            scores = build_vector(company, dims, metrics)
            _upsert_vector(company.id, scores)
            logger.debug("[%d] %s → %s", i, company.name, scores)

    logger.info("ベクトル計算完了: %d社", len(companies))
    print(f"[Phase 3d] {len(companies)}社のベクトル計算完了")


if __name__ == "__main__":
    import logging as _logging

    from backend.config import settings

    _logging.basicConfig(level=settings.log_level)
    build_all_vectors()
