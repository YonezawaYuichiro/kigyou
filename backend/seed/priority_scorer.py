"""Phase E: 深堀り分析の優先度スコアリング。

大阪50社選定のため、全341社に優先度スコアを付けてUIで表示する。
ユーザーが上位50社のリストを確認・修正してから Phase B/C の深堀りを実行する。

優先度スコアの要素:
  - 都道府県（大阪府 = 最優先、京都・兵庫 = 次点）
  - 企業カテゴリ（自社開発・スタートアップ優先）
  - 現在の信頼度（低い = 収集リターンが高い）

実行方法:
  python -m backend.seed.priority_scorer
"""

import logging

import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from backend.database import get_session
from backend.models import Company, CompanyDimensions

logger = logging.getLogger(__name__)

_OSAKA_PREF = "大阪府"
_KANSAI_PREFS = {"京都府", "兵庫県"}
_HIGH_VALUE_CATEGORIES = {"自社開発", "スタートアップ"}


def score_for_deep_analysis(company: Company, dims: CompanyDimensions | None) -> float:
    """深堀り優先度スコア（0.0〜1.0）。

    スコアが高いほど「大阪・自社開発・情報が薄い」企業。
    UIでこのスコア順に表示し、ユーザーが上位50社を確認して確定する。
    """
    # 関西圏外は除外
    if company.hq_prefecture not in (_OSAKA_PREF, *_KANSAI_PREFS):
        return 0.0

    # 都道府県スコア（大阪 = 最優先）
    pref_score = 1.0 if company.hq_prefecture == _OSAKA_PREF else 0.70

    # カテゴリスコア（自社開発・スタートアップ優先）
    cat_score = 1.0 if company.estimated_category in _HIGH_VALUE_CATEGORIES else 0.50

    # データギャップスコア（信頼度が低い = 収集リターンが高い）
    current_conf = dims.overall_confidence if dims else 0.0
    data_gap = 1.0 - (current_conf or 0.0)

    return round(pref_score * 0.30 + cat_score * 0.25 + data_gap * 0.45, 3)


def get_priority_ranking(top_n: int = 80) -> list[dict]:
    """全社を優先度スコア順に返す。top_n 件を上限とする（デフォルト80社）。

    Returns:
        List of dicts with keys: rank, name, prefecture, category, confidence, priority_score
    """
    results = []
    with get_session() as session:
        companies: list[Company] = (
            session.execute(sa.select(Company).options(selectinload(Company.dimensions)))
            .scalars()
            .all()
        )
        for company in companies:
            dims = company.dimensions
            score = score_for_deep_analysis(company, dims)
            if score == 0.0:
                continue
            results.append(
                {
                    "name": company.name,
                    "hq_prefecture": company.hq_prefecture,
                    "estimated_category": company.estimated_category,
                    "overall_confidence": dims.overall_confidence if dims else None,
                    "priority_score": score,
                    "official_url": company.official_url,
                }
            )

    results.sort(key=lambda x: x["priority_score"], reverse=True)
    results = results[:top_n]

    for i, r in enumerate(results, 1):
        r["rank"] = i

    return results


def print_priority_list(top_n: int = 60) -> None:
    """優先度上位 top_n 社を標準出力に表示する（動作確認用）。"""
    ranking = get_priority_ranking(top_n=top_n)
    print(f"\n{'=' * 70}")
    print(f"深堀り優先度ランキング（上位{top_n}社）")
    print(f"{'=' * 70}")
    print(
        f"{'#':>3}  {'企業名':<25}  {'都道府県':<8}  {'カテゴリ':<14}  {'信頼度':>5}  {'優先度':>5}"
    )
    print("-" * 70)
    for r in ranking:
        conf = f"{r['overall_confidence']:.2f}" if r["overall_confidence"] is not None else "  N/A"
        print(
            f"{r['rank']:>3}  {r['name']:<25}  {r['hq_prefecture']:<8}  "
            f"{r['estimated_category']:<14}  {conf:>5}  {r['priority_score']:.3f}"
        )
    print(f"\n関西圏合計: {len(ranking)}社表示")


if __name__ == "__main__":
    import logging as _logging

    from backend.config import settings

    _logging.basicConfig(level=settings.log_level)
    print_priority_list(top_n=60)
