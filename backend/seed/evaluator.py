"""企業分析精度の評価モジュール。

3層の評価フレームワーク:
  Layer 1: 自動相関検証（LLM抽出スコア vs OpenWork外部データ）
  Layer 2: スコア分布分析（識別力・カテゴリ別平均）
  Layer 3: ゴールドスタンダード照合（有名企業の期待スコアとの一致率）

実行:
  python -m backend.seed.evaluator
  → 標準出力に評価レポートを出力する
"""

import json
import logging
from pathlib import Path

import scipy.stats as stats
import sqlalchemy as sa
from sqlalchemy.orm import selectinload

from backend.database import get_session
from backend.models import Company, CompanyDimensions, CompanyMetrics, CompanyVector

logger = logging.getLogger(__name__)

_BENCHMARK_PATH = Path(__file__).parent.parent.parent / "benchmarks" / "known_companies.json"

_DIM_NAMES = [
    "自社開発度",
    "新規事業",
    "使用技術",
    "企業規模",
    "育成支援",
    "カルチャー",
    "キャリア",
    "WLB",
    "選考評価",
    "開発環境",
]

# 各次元ごとのスコアが「充実」とみなす閾値
_DISCRIMINABILITY_MIN_STD = 0.12

# ゴールドスタンダード照合: 「高い」= 0.55超、「低い」= 0.45未満
_HIGH_THRESHOLD = 0.55
_LOW_THRESHOLD = 0.45


def _load_all_companies() -> list[
    tuple[Company, CompanyDimensions | None, CompanyMetrics | None, CompanyVector | None]
]:
    """全企業とDimensions/Metrics/Vectorを一括取得する。"""
    with get_session() as session:
        companies = (
            session.execute(
                sa.select(Company).options(
                    selectinload(Company.dimensions),
                    selectinload(Company.metrics),
                    selectinload(Company.vector),
                )
            )
            .scalars()
            .all()
        )
        return [(c, c.dimensions, c.metrics, c.vector) for c in companies]


def load_benchmark() -> list[dict]:
    """benchmarks/known_companies.json を読み込む。"""
    if not _BENCHMARK_PATH.exists():
        logger.warning("ゴールドスタンダードファイルが見つかりません: %s", _BENCHMARK_PATH)
        return []
    return json.loads(_BENCHMARK_PATH.read_text(encoding="utf-8"))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 1: 自動相関検証
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def check_llm_vs_openwork_correlation(
    rows: list[tuple],
) -> dict[str, dict]:
    """LLM抽出スコア vs OpenWorkサブスコアのSpearman相関を計算する。

    検証する対応:
      dim[5] カルチャー ←→ (ow_score_morale + ow_score_openness) / 2
      dim[6] キャリア   ←→ ow_score_growth
      dim[4] 育成支援   ←→ ow_score_treatment
    """
    results: dict[str, dict] = {}

    # dim[5] カルチャー vs OpenWork文化系スコア
    pairs_culture = [
        (dims.psychological_safety_score, (m.ow_score_morale + m.ow_score_openness) / 2)
        for _, dims, m, _ in rows
        if dims
        and m
        and dims.psychological_safety_score is not None
        and m.ow_score_morale is not None
        and m.ow_score_openness is not None
    ]
    results["dim5_culture"] = _spearman(pairs_culture, "心理的安全性 vs OW文化スコア")

    # dim[6] キャリア vs ow_score_growth
    pairs_career = [
        (dims.junior_authority_score, m.ow_score_growth)
        for _, dims, m, _ in rows
        if dims and m and dims.junior_authority_score is not None and m.ow_score_growth is not None
    ]
    results["dim6_career"] = _spearman(pairs_career, "若手裁量スコア vs OW成長性スコア")

    # dim[4] 育成支援 vs ow_score_treatment
    pairs_growth = [
        (dims.skill_support_score, m.ow_score_treatment)
        for _, dims, m, _ in rows
        if dims and m and dims.skill_support_score is not None and m.ow_score_treatment is not None
    ]
    results["dim4_growth"] = _spearman(pairs_growth, "スキル支援スコア vs OW待遇スコア")

    return results


def _spearman(pairs: list[tuple[float, float]], label: str) -> dict:
    """Spearman相関係数を計算する。サンプル不足なら error を返す。"""
    if len(pairs) < 5:
        return {"label": label, "error": f"サンプル不足 (n={len(pairs)})"}
    llm_vals, ow_vals = zip(*pairs, strict=False)
    r, p = stats.spearmanr(llm_vals, ow_vals)
    return {
        "label": label,
        "r": round(float(r), 3),
        "p": round(float(p), 4),
        "n": len(pairs),
        "status": "✅ OK" if abs(r) >= 0.35 else "⚠️ 相関弱い",
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 2: スコア分布分析
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def check_data_coverage(rows: list[tuple]) -> list[dict]:
    """各次元のデータカバレッジ率（スコアがNoneでない企業の割合）を計算する。"""
    total = len(rows)
    if total == 0:
        return []

    # CompanyVector から各次元のスコアを取得
    coverage: list[int] = [0] * 10
    for _, _, _, vec in rows:
        if vec and vec.dim_scores is not None:
            scores = list(vec.dim_scores)
            for i, s in enumerate(scores[:10]):
                if s is not None:
                    coverage[i] += 1

    vec_count = sum(1 for _, _, _, vec in rows if vec and vec.dim_scores is not None)
    return [
        {
            "dim": i,
            "name": _DIM_NAMES[i],
            "coverage_pct": round(coverage[i] / total * 100, 1),
            "has_vector_pct": round(vec_count / total * 100, 1),
            "status": "✅ OK" if coverage[i] / total >= 0.70 else "⚠️ 不足",
        }
        for i in range(10)
    ]


def analyze_score_distribution(rows: list[tuple]) -> list[dict]:
    """各次元のスコア分布統計（mean, std, min, max）を計算する。"""
    import statistics

    dim_scores: list[list[float]] = [[] for _ in range(10)]
    for _, _, _, vec in rows:
        if vec and vec.dim_scores is not None:
            scores = list(vec.dim_scores)
            for i, s in enumerate(scores[:10]):
                if s is not None:
                    dim_scores[i].append(float(s))

    result = []
    for i, scores in enumerate(dim_scores):
        if len(scores) < 3:
            result.append({"dim": i, "name": _DIM_NAMES[i], "error": "データ不足"})
            continue
        std = statistics.stdev(scores)
        result.append(
            {
                "dim": i,
                "name": _DIM_NAMES[i],
                "mean": round(statistics.mean(scores), 3),
                "std": round(std, 3),
                "min": round(min(scores), 3),
                "max": round(max(scores), 3),
                "n": len(scores),
                "status": "✅ OK" if std >= _DISCRIMINABILITY_MIN_STD else "⚠️ 識別力不足",
            }
        )
    return result


def check_category_discrimination(rows: list[tuple]) -> dict[str, dict]:
    """estimated_categoryごとの各次元平均スコアを計算する。

    期待パターン:
      dim[0] 自社開発度: 自社開発 > スタートアップ > SIer
      dim[3] 安定性:     SIer > 自社開発 > スタートアップ
    """
    import statistics

    category_scores: dict[str, list[list[float]]] = {}

    for company, _, _, vec in rows:
        cat = company.estimated_category
        if cat not in category_scores:
            category_scores[cat] = [[] for _ in range(10)]
        if vec and vec.dim_scores is not None:
            scores = list(vec.dim_scores)
            for i, s in enumerate(scores[:10]):
                if s is not None:
                    category_scores[cat][i].append(float(s))

    result: dict[str, dict] = {}
    for cat, dim_lists in category_scores.items():
        result[cat] = {
            "n": sum(1 for company, _, _, vec in rows if company.estimated_category == cat and vec),
            "dim_means": [
                round(statistics.mean(lst), 3) if len(lst) >= 2 else None for lst in dim_lists
            ],
        }

    # サニティチェック: dim[0]で 自社開発 > SIer か
    dim0_checks = {}
    for cat in ["自社開発", "SIer", "スタートアップ"]:
        if cat in result and result[cat]["dim_means"][0] is not None:
            dim0_checks[cat] = result[cat]["dim_means"][0]

    category_ok = False
    if "自社開発" in dim0_checks and "SIer" in dim0_checks:
        category_ok = dim0_checks["自社開発"] > dim0_checks["SIer"]

    result["_sanity_dim0"] = {
        "values": dim0_checks,
        "status": "✅ 自社開発 > SIer" if category_ok else "⚠️ 期待パターン不一致",
    }
    return result


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Layer 3: ゴールドスタンダード照合
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def check_benchmark(rows: list[tuple], benchmark: list[dict]) -> dict:
    """期待スコアと実際のスコアの一致率を計算する。"""
    passed = 0
    total = 0
    details = []

    # 企業名 → (Company, vec) のマッピング
    company_map = {
        company.name: vec for company, _, _, vec in rows if vec and vec.dim_scores is not None
    }

    for entry in benchmark:
        name_contains = entry["name_contains"]
        matched_name = next((n for n in company_map if name_contains in n), None)
        if not matched_name:
            details.append({"name": name_contains, "status": "⚪ DBに未登録"})
            continue

        scores = list(company_map[matched_name].dim_scores)
        item_results = []

        for high_dim in entry["expected"]["high"]:
            actual = scores[high_dim] if high_dim < len(scores) else None
            ok = actual is not None and actual > _HIGH_THRESHOLD
            item_results.append(
                {
                    "dim": high_dim,
                    "name": _DIM_NAMES[high_dim],
                    "expect": "high",
                    "actual": round(actual, 3) if actual is not None else None,
                    "ok": ok,
                }
            )
            total += 1
            if ok:
                passed += 1

        for low_dim in entry["expected"]["low"]:
            actual = scores[low_dim] if low_dim < len(scores) else None
            ok = actual is not None and actual < _LOW_THRESHOLD
            item_results.append(
                {
                    "dim": low_dim,
                    "name": _DIM_NAMES[low_dim],
                    "expect": "low",
                    "actual": round(actual, 3) if actual is not None else None,
                    "ok": ok,
                }
            )
            total += 1
            if ok:
                passed += 1

        pass_rate = round(sum(1 for r in item_results if r["ok"]) / max(1, len(item_results)), 3)
        details.append(
            {
                "name": matched_name,
                "pass_rate": pass_rate,
                "items": item_results,
                "notes": entry["expected"].get("notes", ""),
            }
        )

    overall_pass_rate = round(passed / max(1, total), 3)
    return {
        "pass_rate": overall_pass_rate,
        "passed": passed,
        "total": total,
        "status": "✅ OK" if overall_pass_rate >= 0.70 else "⚠️ 一致率不足",
        "details": details,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# メイン: 全評価を実行してレポートを出力
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def run_all() -> dict:
    """全評価を実行してレポートを返す。"""
    rows = _load_all_companies()
    benchmark = load_benchmark()
    return {
        "total_companies": len(rows),
        "coverage": check_data_coverage(rows),
        "distribution": analyze_score_distribution(rows),
        "correlation": check_llm_vs_openwork_correlation(rows),
        "category": check_category_discrimination(rows),
        "benchmark": check_benchmark(rows, benchmark),
    }


def _print_report(report: dict) -> None:
    """評価レポートをコンソールに整形出力する。"""
    print(f"\n{'=' * 70}")
    print(f"GradMatch-AI 企業分析精度評価レポート  (対象: {report['total_companies']}社)")
    print(f"{'=' * 70}")

    print("\n▶ Layer 2: データカバレッジ")
    for item in report["coverage"]:
        if "error" in item:
            continue
        print(
            f"  dim[{item['dim']}] {item['name']:<12}: {item['coverage_pct']:5.1f}%  {item['status']}"
        )

    print("\n▶ Layer 2: スコア識別力（std）")
    for item in report["distribution"]:
        if "error" in item:
            print(f"  dim[{item['dim']}] {item['name']:<12}: {item['error']}")
            continue
        print(
            f"  dim[{item['dim']}] {item['name']:<12}: "
            f"mean={item['mean']:.3f}  std={item['std']:.3f}  {item['status']}"
        )

    print("\n▶ Layer 1: LLM vs OpenWork 相関")
    for _key, result in report["correlation"].items():
        if "error" in result:
            print(f"  {result['label']}: {result['error']}")
        else:
            print(
                f"  {result['label']}: r={result['r']:.3f} (p={result['p']:.4f}, n={result['n']})  {result['status']}"
            )

    print("\n▶ Layer 2: カテゴリ別サニティチェック")
    sanity = report["category"].get("_sanity_dim0", {})
    print(f"  {sanity.get('status', '—')}  {sanity.get('values', {})}")

    print("\n▶ Layer 3: ゴールドスタンダード照合")
    bm = report["benchmark"]
    print(f"  総合一致率: {bm['pass_rate']:.1%} ({bm['passed']}/{bm['total']})  {bm['status']}")
    for d in bm["details"]:
        if d.get("pass_rate") is not None:
            print(f"    {d['name']}: {d['pass_rate']:.1%}")
        else:
            print(f"    {d.get('name')}: {d.get('status')}")

    print(f"\n{'=' * 70}\n")


if __name__ == "__main__":
    import logging as _logging

    from backend.config import settings

    _logging.basicConfig(level=settings.log_level)
    report = run_all()
    _print_report(report)
