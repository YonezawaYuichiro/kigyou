"""Phase 3d: CompanyDimensions + CompanyMetrics から 10次元スコアベクトルを算術計算し CompanyVector にupsertする。

LLM呼び出し不要。各次元は 0.0-1.0 に正規化済み。

新卒エンジニア特化の10次元定義（V4）:
  dim[0] 自社開発度         (estimated_category + competitive_advantage補正)
  dim[1] 新規事業・ビジョン  (new_biz_policy_score + competitive_advantage_score + 特許有無)
  dim[2] 使用技術の鮮度      (tech_stackキーワードのモダン/レガシー比率)
  dim[3] 企業規模・安定性    (is_listed + founded_year + employee_count)
  dim[4] エンジニア成長支援  (skill_support_score + skill_support_items数 + career_track_diversity)
  dim[5] 社風・カルチャー    (psychological_safety_score / 5.0)
  dim[6] キャリア成長        (junior_authority_score / 5.0 + コードレビュー文化)
  dim[7] 待遇・WLB           (残業 + 有休 + リモートワーク)
  dim[8] 選考の技術評価度    (has_coding_test + interviewer_type)
  dim[9] 開発環境            (cicd_maturity_score + infra_cloud_score + tech_modernity_score)

  ※ 立地はdim[0]から除外 → hard_filter（preferred_prefectures）で管理
  ※ hiring_difficulty_score（採用難易度）は10次元外・realistic_score割引専用
"""

import logging
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from backend.database import get_session
from backend.models import Company, CompanyDimensions, CompanyMetrics, CompanyVector

logger = logging.getLogger(__name__)

_MODEL_VERSION = "v4.0"

# dim[0] 自社開発度 — estimated_category スコアマップ
_CATEGORY_SCORES: dict[str, float] = {
    "自社開発": 1.00,
    "スタートアップ": 0.95,
    "メーカー情報子会社": 0.60,
    "SIer": 0.25,
    "受託開発": 0.20,
    "その他": 0.50,
}

# dim[2] 使用技術の鮮度 — キーワードセット（小文字で比較）
_MODERN_TECH_KW = frozenset(
    {
        "python",
        "go",
        "rust",
        "typescript",
        "kotlin",
        "swift",
        "dart",
        "react",
        "vue",
        "nextjs",
        "next.js",
        "nuxt",
        "svelte",
        "docker",
        "kubernetes",
        "k8s",
        "aws",
        "gcp",
        "azure",
        "terraform",
        "graphql",
        "grpc",
        "fastapi",
        "hono",
        "llm",
        "ai",
        "機械学習",
        "ml",
        "mlops",
        "大規模言語モデル",
        "dbt",
        "airflow",
        "spark",
        "flutter",
    }
)
_MID_TECH_KW = frozenset(
    {
        "java",
        "ruby",
        "php",
        "c#",
        "scala",
        "javascript",
        "rails",
        "spring",
        "mysql",
        "redis",
        "mongodb",
        "elasticsearch",
        "node.js",
        "nodejs",
        "django",
        "flask",
    }
)
_LEGACY_TECH_KW = frozenset(
    {
        "cobol",
        "vb",
        "vba",
        "delphi",
        "perl",
        "asp",
        "fortran",
        "powerbuilder",
        "pl/sql",
    }
)

# dim[8] 採用技術評価度 — 面接官スコアマップ
_INTERVIEWER_SCORES: dict[str, float] = {
    "current_engineer": 1.0,
    "mixed": 0.70,
    "hr_only": 0.25,
    "unknown": 0.45,
}


def _dim0_product_focus(company: Company, dims: CompanyDimensions | None) -> float:
    """自社開発度: SaaS/自社プロダクト中心か受託/SIer中心かを表す。"""
    cat_score = _CATEGORY_SCORES.get(company.estimated_category, 0.50)
    # 競合優位性補正（強みのある自社プロダクトを持っていれば加点）
    adv_bonus = (dims.competitive_advantage_score or 0.0) * 0.08 if dims else 0.0
    return round(min(1.0, cat_score + adv_bonus), 3)


def _dim1_vision_new_biz(dims: CompanyDimensions | None) -> float:
    """新規事業・ビジョン: 新規事業積極度・競合優位性・特許保有から算出。"""
    if dims is None:
        return 0.40
    new_biz = dims.new_biz_policy_score or 0.40
    adv = dims.competitive_advantage_score or 0.40
    patent_bonus = 0.10 if dims.has_patent else (0.0 if dims.has_patent is False else 0.05)
    return round(min(1.0, new_biz * 0.55 + adv * 0.35 + patent_bonus), 3)


def _dim2_tech_freshness(tech_stack: list) -> float:
    """使用技術の鮮度: モダン/ミッドモダン/レガシー技術の比率から算出。"""
    if not tech_stack:
        return 0.40
    techs = [str(t).lower() for t in tech_stack]
    total = max(1, len(techs))

    modern = sum(1 for t in techs if any(kw in t for kw in _MODERN_TECH_KW))
    mid = sum(1 for t in techs if any(kw in t for kw in _MID_TECH_KW))
    legacy = sum(1 for t in techs if any(kw in t for kw in _LEGACY_TECH_KW))

    score = (modern * 1.0 + mid * 0.50) / total - (legacy * 0.30 / total)
    return round(min(1.0, max(0.10, score)), 3)


def _dim3_stability(metrics: CompanyMetrics | None) -> float:
    """企業規模・安定性: 上場区分 + 設立年 + 従業員規模。全企業で取得可能な指標のみ使用。"""
    if metrics is None:
        return 0.40

    # 上場スコア
    listing_score = 0.85 if metrics.is_listed else 0.50

    # 設立年スコア（老舗ほど安定、30年以上で満点）
    if metrics.founded_year:
        age = 2025 - metrics.founded_year
        age_score = min(1.0, age / 30.0)
    else:
        age_score = 0.40

    # 従業員規模スコア
    emp = metrics.employee_count or 0
    if emp >= 500:
        size_score = 1.00
    elif emp >= 100:
        size_score = 0.75
    elif emp >= 30:
        size_score = 0.50
    elif emp > 0:
        size_score = 0.30
    else:
        size_score = 0.40  # 不明

    return round(listing_score * 0.40 + age_score * 0.35 + size_score * 0.25, 3)


def _dim4_engineer_growth(dims: CompanyDimensions | None) -> float:
    """エンジニア成長支援: 資格補助・研修・複線キャリア・評価制度の充実度。"""
    if dims is None:
        return 0.40

    skill = dims.skill_support_score or 0.40

    # 支援制度リストの多様性（5項目で +0.20）
    items = dims.skill_support_items or []
    items_bonus = min(0.20, len(items) * 0.04)

    # 複線キャリア（専門職/管理職を選べる）
    diversity = (
        1.0
        if dims.career_track_diversity
        else (0.30 if dims.career_track_diversity is False else 0.50)
    )

    # 評価制度（成果主義 = エンジニアの貢献が評価されやすい）
    eval_bonus = {"成果主義": 0.12, "混在": 0.06, "年功序列": 0.0, "不明": 0.04}.get(
        dims.evaluation_system_type or "不明", 0.04
    )

    base = skill * 0.55 + diversity * 0.30
    return round(min(1.0, base + items_bonus + eval_bonus), 3)


def _dim5_culture(dims: CompanyDimensions | None) -> float:
    """社風・カルチャー: 心理的安全性スコアを正規化（0〜5 → 0〜1）。"""
    if dims is None:
        return 0.40
    return round((dims.psychological_safety_score or 2.0) / 5.0, 3)


def _dim6_junior_authority(dims: CompanyDimensions | None) -> float:
    """キャリア成長: 若手裁量 + コードレビュー文化 + 心理的安全性補正。"""
    if dims is None:
        return 0.40

    junior = (dims.junior_authority_score or 2.0) / 5.0

    # コードレビューの記述があればボーナス
    tech_ev = (dims.tech_env_evidence or "").lower()
    code_review_bonus = 0.10 if ("コードレビュー" in tech_ev or "code review" in tech_ev) else 0.0

    # 心理的安全性補正（発言しやすい環境 = 若手が成長しやすい）
    psych = (dims.psychological_safety_score or 2.5) / 5.0

    return round(min(1.0, junior * 0.65 + psych * 0.25 + code_review_bonus), 3)


def _dim7_wlb(metrics: CompanyMetrics | None) -> float:
    """待遇・WLB: 残業時間 + 有休取得率 + リモートワーク度。"""
    if metrics is None:
        return 0.50
    overtime = metrics.avg_overtime_hours
    ot_score = max(0.0, 1.0 - overtime / 60.0) if overtime is not None else 0.50
    leave = float(metrics.paid_leave_rate) if metrics.paid_leave_rate is not None else 0.50
    remote = metrics.remote_work_policy
    remote_bonus = 0.15 if remote == "full" else (0.08 if remote == "partial" else 0.0)
    morale = metrics.ow_score_morale
    openness = metrics.ow_score_openness
    culture_vals = [s for s in [morale, openness] if s is not None]
    if culture_vals:
        culture = sum(culture_vals) / len(culture_vals) / 5.0
        base = ot_score * 0.40 + leave * 0.30 + culture * 0.30
    else:
        base = (ot_score + leave) / 2.0
    return round(min(1.0, base + remote_bonus), 3)


def _dim8_tech_recruiting(dims: CompanyDimensions | None) -> float:
    """選考の技術評価度: コーディングテスト有無 + 面接官属性。"""
    if dims is None:
        return 0.40
    coding = 1.0 if dims.has_coding_test else (0.20 if dims.has_coding_test is False else 0.45)
    interviewer = _INTERVIEWER_SCORES.get(dims.interviewer_type or "unknown", 0.45)
    return round(coding * 0.55 + interviewer * 0.45, 3)


def _dim9_cicd_devops(dims: CompanyDimensions | None) -> float:
    """開発環境: CI/CD・クラウド活用・技術的負債への向き合い方。"""
    if dims is None:
        return 0.40
    scores_weighted = [
        (dims.cicd_maturity_score, 0.40),
        (dims.infra_cloud_score, 0.25),
        (dims.tech_modernity_score, 0.25),
        (dims.tech_debt_culture_score, 0.10),
    ]
    valid = [(s, w) for s, w in scores_weighted if s is not None]
    if not valid:
        return 0.40
    total_w = sum(w for _, w in valid)
    score = sum((s / 5.0) * (w / total_w) for s, w in valid)
    return round(min(1.0, score), 3)


def compute_hiring_difficulty(dims: CompanyDimensions, metrics: CompanyMetrics | None) -> float:
    """採用難易度スコア（10次元外）: realistic_scoreの割引計算に使用。

    高いほど入社が難しい（技術選考が厳しい・知名度高い・大企業）。
    """
    score = 0.0

    # コーディングテスト → 技術選考が厳しい
    if dims.has_coding_test is True:
        score += 0.30
    elif dims.has_coding_test is None:
        score += 0.10

    # エンジニア面接官 → 技術基準が高い
    if dims.interviewer_type == "current_engineer":
        score += 0.15
    elif dims.interviewer_type == "mixed":
        score += 0.08

    # CI/CD成熟度が高い → 品質基準が高い企業
    if dims.cicd_maturity_score is not None:
        score += (dims.cicd_maturity_score / 5.0) * 0.15

    if metrics:
        # 従業員規模 → 応募者が多い
        emp = metrics.employee_count or 0
        if emp >= 1000:
            score += 0.20
        elif emp >= 300:
            score += 0.10

        # OpenWork口コミ件数 → 知名度高い = 競争率高い
        review_count = metrics.openwork_review_count or 0
        score += min(0.20, review_count / 1000 * 0.20)

    return round(min(1.0, score), 3)


def build_vector(
    company: Company, dims: CompanyDimensions | None, metrics: CompanyMetrics | None
) -> list[float]:
    """10次元スコアベクトルを算術計算して返す（V4 新卒エンジニア特化）。"""
    return [
        _dim0_product_focus(company, dims),  # dim[0] 自社開発度
        _dim1_vision_new_biz(dims),  # dim[1] 新規事業・ビジョン
        _dim2_tech_freshness(list(company.tech_stack or [])),  # dim[2] 使用技術の鮮度
        _dim3_stability(metrics),  # dim[3] 企業規模・安定性
        _dim4_engineer_growth(dims),  # dim[4] エンジニア成長支援
        _dim5_culture(dims),  # dim[5] 社風・カルチャー
        _dim6_junior_authority(dims),  # dim[6] キャリア成長
        _dim7_wlb(metrics),  # dim[7] WLB
        _dim8_tech_recruiting(dims),  # dim[8] 選考の技術評価度
        _dim9_cicd_devops(dims),  # dim[9] 開発環境
    ]


def _upsert_vector(company_id: uuid.UUID, scores: list[float]) -> None:
    with get_session() as session:
        existing = session.execute(
            sa.select(CompanyVector).where(CompanyVector.company_id == company_id)
        ).scalar_one_or_none()
        if existing:
            existing.dim_scores = scores
            existing.model_version = _MODEL_VERSION
        else:
            session.add(
                CompanyVector(
                    company_id=company_id, dim_scores=scores, model_version=_MODEL_VERSION
                )
            )


def _upsert_hiring_difficulty(company_id: uuid.UUID, difficulty: float) -> None:
    """CompanyDimensions.hiring_difficulty_score をupsertする。"""
    with get_session() as session:
        dims = session.execute(
            sa.select(CompanyDimensions).where(CompanyDimensions.company_id == company_id)
        ).scalar_one_or_none()
        if dims:
            dims.hiring_difficulty_score = difficulty


def build_all_vectors() -> None:
    """全企業の CompanyVector と hiring_difficulty_score を算術計算してupsertする。

    セッション内でスコアを計算してPythonネイティブ値に変換し、
    セッション外でupsertする（DetachedInstanceError回避）。
    """
    # (company_id, scores, hiring_difficulty) のリスト
    computed: list[tuple[uuid.UUID, list[float], float | None]] = []

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
        for company in companies:
            dims = company.dimensions
            metrics = company.metrics
            scores = build_vector(company, dims, metrics)
            difficulty = compute_hiring_difficulty(dims, metrics) if dims else None
            computed.append((company.id, scores, difficulty))
            logger.debug("%s → %s", company.name, scores)

    # セッション外でupsert（各関数が独立したセッションを開く）
    for cid, scores, difficulty in computed:
        _upsert_vector(cid, scores)
        if difficulty is not None:
            _upsert_hiring_difficulty(cid, difficulty)

    logger.info("ベクトル計算完了: %d社", len(computed))
    print(f"[Phase 3d] {len(computed)}社のベクトル計算完了（モデルバージョン: {_MODEL_VERSION}）")


if __name__ == "__main__":
    import logging as _logging

    from backend.config import settings

    _logging.basicConfig(level=settings.log_level)
    build_all_vectors()
