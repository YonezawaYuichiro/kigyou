"""Step 2: スコアリングエンジン。

my_profile.json の重み・スキル・ハードフィルターを元に
Company + CompanyMetrics の組み合わせをスコアリングする。
"""

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import Company, CompanyMetrics

logger = logging.getLogger(__name__)

PROFILE_PATH = Path(__file__).parent.parent.parent / "data" / "my_profile.json"

# 資格ボーナステーブル（tech_growth への加算値）
_QUAL_BONUS_MAP: dict[str, float] = {
    "応用情報技術者": 0.15,
    "基本情報技術者": 0.08,
    "情報処理安全確保支援士": 0.12,
    "データベーススペシャリスト": 0.10,
    "ネットワークスペシャリスト": 0.10,
    "システムアーキテクト": 0.12,
    "プロジェクトマネージャ": 0.08,
    "AWS認定": 0.08,
    "GCP認定": 0.08,
    "Azure認定": 0.08,
}
_QUAL_BONUS_MAX = 0.25  # 資格ボーナスの上限


def _qual_bonus(qualifications: list[str]) -> float:
    """保有資格リストから tech_growth への加算ボーナスを計算する（上限 0.25）。"""
    total = sum(_QUAL_BONUS_MAP.get(q.strip(), 0.05) for q in qualifications if q.strip())
    return min(_QUAL_BONUS_MAX, total)


def load_profile(path: Path = PROFILE_PATH) -> dict[str, Any]:
    """my_profile.json を読み込む。"""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _review_count_to_size_score(count: int | None) -> float:
    """口コミ件数を企業規模スコア 0.0〜1.0 に変換する。"""
    if count is None:
        return 0.3
    if count >= 1000:
        return 1.0
    if count >= 300:
        return 0.8
    if count >= 100:
        return 0.6
    if count >= 30:
        return 0.4
    return 0.2


def _to_size_score(employee_count: int | None, review_count: int | None) -> float:
    """従業員数（優先）または口コミ件数から企業規模スコアを返す。"""
    if employee_count is not None:
        if employee_count >= 5000:
            return 1.0
        if employee_count >= 1000:
            return 0.8
        if employee_count >= 300:
            return 0.6
        if employee_count >= 100:
            return 0.4
        return 0.2
    return _review_count_to_size_score(review_count)


def _passes_hard_filters(
    metrics: CompanyMetrics | None,
    filters: dict[str, Any],
) -> bool:
    """ハードフィルターを満たすか判定する。一つでも違反したら False。"""
    max_ot = filters.get("max_overtime_hours")
    min_score = filters.get("min_openwork_score")

    if metrics is None:
        # メトリクス未取得の場合はフィルターをパス（データ不足で除外しない）
        return True

    if max_ot is not None and metrics.avg_overtime_hours is not None:
        if metrics.avg_overtime_hours > max_ot:
            return False

    if min_score is not None and metrics.openwork_score is not None:
        if metrics.openwork_score < min_score:
            return False

    return True


def _calc_coverage(metrics: CompanyMetrics | None) -> int:
    """主要5項目のうちデータがある項目数（0〜5）を返す。"""
    if metrics is None:
        return 0
    fields = [
        "openwork_score",
        "avg_overtime_hours",
        "avg_annual_salary",
        "employee_count",
        "is_listed",
    ]
    return sum(1 for f in fields if getattr(metrics, f, None) is not None)


def calc_score(
    company: Company,
    metrics: CompanyMetrics | None,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """5軸スコアを計算し、軸別スコア・データ充実度を含む dict を返す。"""
    w = profile["weights"]

    # --- 技術マッチ度 ---
    required = set(profile.get("required_skills", []))
    bonus = set(profile.get("bonus_skills", []))
    stack = set(company.tech_stack or [])
    required_score = len(stack & required) / max(len(required), 1)
    bonus_score = len(stack & bonus) / max(len(bonus), 1) if bonus else 0.0
    tech_stack_score = required_score * 0.7 + bonus_score * 0.3
    # 保有資格ボーナスを加算（技術資格は tech_growth 軸の底上げに寄与）
    qual_bonus = _qual_bonus(profile.get("qualifications", []))
    tech_stack_score = min(1.0, tech_stack_score + qual_bonus)
    # OpenWork「20代成長環境」スコアをブレンド（データあり時）
    ow_growth = getattr(metrics, "ow_score_growth", None) if metrics else None
    if ow_growth is not None:
        tech_score = tech_stack_score * 0.5 + (ow_growth / 5.0) * 0.5
    else:
        tech_score = tech_stack_score

    # --- WLB ---
    if metrics is not None and metrics.avg_overtime_hours is not None:
        overtime_score = max(0.0, 1.0 - metrics.avg_overtime_hours / 60.0)
    else:
        overtime_score = 0.5
    if metrics is not None and metrics.paid_leave_rate is not None:
        leave_score = float(metrics.paid_leave_rate)
    else:
        leave_score = 0.5
    # リモート方針ボーナス（+0.15 / +0.08）
    remote = getattr(metrics, "remote_work_policy", None) if metrics else None
    remote_bonus = 0.15 if remote == "full" else (0.08 if remote == "partial" else 0.0)
    # 社員士気・風通し（OW サブスコア）をブレンド
    ow_morale = getattr(metrics, "ow_score_morale", None) if metrics else None
    ow_openness = getattr(metrics, "ow_score_openness", None) if metrics else None
    ow_culture_list = [s for s in [ow_morale, ow_openness] if s is not None]
    culture_score = sum(ow_culture_list) / len(ow_culture_list) / 5.0 if ow_culture_list else None
    if culture_score is not None:
        base_wlb = overtime_score * 0.4 + leave_score * 0.3 + culture_score * 0.3
    else:
        base_wlb = (overtime_score + leave_score) / 2.0
    wlb_score = min(1.0, base_wlb + remote_bonus)

    # --- 企業規模 ---
    review_count = metrics.openwork_review_count if metrics else None
    employee_count = getattr(metrics, "employee_count", None) if metrics else None
    size_score = _to_size_score(employee_count, review_count)

    # --- 自社開発度（4段階） ---
    _category_scores: dict[str, float] = {
        "自社開発": 1.0,
        "メーカー情報子会社": 0.5,
        "SIer": 0.3,
        "その他": 0.3,
    }
    self_dev_score = _category_scores.get(company.estimated_category, 0.3)

    # --- 立地 ---
    preferred = set(profile.get("preferred_prefectures", []))
    location_score = 1.0 if company.hq_prefecture in preferred else 0.5

    total = (
        w["tech_growth"] * tech_score
        + w["wlb"] * wlb_score
        + w["company_size"] * size_score
        + w["self_developed"] * self_dev_score
        + w["location"] * location_score
    )
    return {
        "total": round(total, 3),
        "axes": {
            "tech_growth": round(tech_score, 3),
            "wlb": round(wlb_score, 3),
            "company_size": round(size_score, 3),
            "self_developed": round(self_dev_score, 3),
            "location": round(location_score, 3),
        },
        "data_coverage": _calc_coverage(metrics),
    }


def score_all(
    session: Session,
    profile: dict[str, Any] | None = None,
    apply_hard_filters: bool = True,
) -> list[dict[str, Any]]:
    """全企業をスコアリングして降順リストを返す。

    Returns:
        [{"company": Company, "metrics": CompanyMetrics|None, "score": float}, ...]
    """
    if profile is None:
        profile = load_profile()

    companies = session.scalars(select(Company)).all()
    results: list[dict[str, Any]] = []

    for company in companies:
        metrics = company.metrics
        if apply_hard_filters and not _passes_hard_filters(
            metrics, profile.get("hard_filters", {})
        ):
            continue
        score_result = calc_score(company, metrics, profile)
        results.append({"company": company, "metrics": metrics, "score": score_result["total"]})

    results.sort(key=lambda x: x["score"], reverse=True)
    return results
