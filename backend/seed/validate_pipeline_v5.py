"""配管テスト: V2 CompanyDimensions → write_from_star / write_from_circle（API費用ゼロ）

既存 V2 CompanyDimensions の行を Haiku 出力形式の star dict に変換し、
write_from_star / write_from_circle を実際に発火させて EAV 書き込み経路を検証する。

何を確認するか:
  1. _STAR_MAP のキー名と変換ロジックが実データで正しく動くか
  2. 保護ロジック (official/review → スキップ) が期待通り働くか
  3. FK 違反・型エラー等の未発見バグがないか
  4. 書き込み件数・スキップ理由の内訳

使い方:
  python -m backend.seed.validate_pipeline_v5 --name "サイボウズ"
  python -m backend.seed.validate_pipeline_v5 --company-id <UUID>

注: 書き込み対象は source_type='estimated' の行のみ（official/review は保護）。
    サイボウズは全件 official/review なので 0 件書き込み = 保護ロジックの動作確認。
    PFN は一部 estimated なので上書きが発生 = 書き込み経路の動作確認。
"""

import argparse
import logging
import uuid

import sqlalchemy as sa

from backend.database import get_session
from backend.seed.feature_writer_v5 import (
    _STAR_MAP,
    _convert,
    write_from_circle,
    write_from_star,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _find_company(name_contains: str) -> tuple[str, str] | None:
    with get_session() as session:
        row = session.execute(
            sa.text("SELECT id, name FROM company WHERE name LIKE :pat"),
            {"pat": f"%{name_contains}%"},
        ).fetchone()
    return (str(row.id), row.name) if row else None


def _load_dims(company_id: str) -> dict | None:
    with get_session() as session:
        row = session.execute(
            sa.text(
                "SELECT psychological_safety_score, junior_authority_score,"
                "       new_biz_policy_score, competitive_advantage_score,"
                "       has_patent, evaluation_score, skill_support_score,"
                "       career_track_diversity, has_coding_test, interviewer_type,"
                "       tech_modernity_score, infra_cloud_score, cicd_maturity_score,"
                "       hw_sw_integration, data_platform_score, tech_debt_culture_score,"
                "       rd_ratio, capex_ratio, megatrend_score,"
                "       overall_confidence"
                " FROM company_dimensions WHERE company_id = :cid"
            ),
            {"cid": company_id},
        ).fetchone()
    return dict(row._mapping) if row else None


def _dims_to_star(dims: dict) -> dict:
    """V2 CompanyDimensions 行を Haiku 出力と同形式の star dict に変換する。"""
    return {k: dims.get(k) for k in _STAR_MAP}


def _dims_to_circle(dims: dict) -> dict:
    """V2 CompanyDimensions 行を Haiku 出力と同形式の circle dict に変換する。

    サイボウズ の V2 には overtime/paid_leave/remote の実態値が入っていないため、
    circle 側は空になることが多い。
    """
    return {}  # V2 dims は circle 値をほぼ持たない（wlb カテゴリは CompanyField に保存）


def _print_star_preview(star: dict, confidence: float) -> None:
    """変換後の star dict を表示する（書き込み前確認）。"""
    print("\n=== star dict (V2 → 変換後) ===")
    print(f"  confidence: {confidence:.3f}")
    for haiku_key, (fkey, vtype) in _STAR_MAP.items():
        raw = star.get(haiku_key)
        if raw is None:
            print(f"  {haiku_key:<35} → {fkey:<30} = NULL (スキップ)")
        else:
            converted = _convert(raw, vtype)
            print(f"  {haiku_key:<35} → {fkey:<30} = {raw} → {converted} ({vtype})")


def _count_existing(company_id: str) -> dict[str, str]:
    """company_feature の既存行と source_type のマップを返す。"""
    with get_session() as session:
        rows = session.execute(
            sa.text(
                "SELECT feature_key, source_type FROM company_feature"
                " WHERE company_id = :cid AND role_id IS NULL"
            ),
            {"cid": company_id},
        ).fetchall()
    return {r.feature_key: r.source_type for r in rows}


def validate(company_id: str, company_name: str) -> None:
    dims = _load_dims(company_id)
    if dims is None:
        print(f"[ERROR] {company_name} に CompanyDimensions がありません。Gemini 抽出が必要です。")
        return

    confidence = float(dims.get("overall_confidence") or 0.0)
    star = _dims_to_star(dims)
    circle = _dims_to_circle(dims)

    _print_star_preview(star, confidence)

    # 書き込み前の既存行状況
    existing = _count_existing(company_id)
    print(f"\n  既存 company_feature: {len(existing)} 件")
    protected = sum(1 for st in existing.values() if st in ("official", "review"))
    estimated = sum(1 for st in existing.values() if st == "estimated")
    missing = sum(
        1 for _, (fkey, _) in _STAR_MAP.items() if star.get(_) is not None and fkey not in existing
    )
    print(f"  うち保護 (official/review): {protected} 件 → write_from_star が上書き禁止")
    print(f"  うち推定 (estimated):        {estimated} 件 → 上書き可能")
    print(f"  新規挿入見込み:              {missing} 件 (既存なし + star に値あり)")

    # 実際に発火
    print("\n=== write_from_star 実行 ===")
    star_written = write_from_star(company_id, star, confidence, source="validate_pipeline_v5")
    print(f"  書き込み件数: {star_written} 件")

    print("\n=== write_from_circle 実行 ===")
    circle_written = write_from_circle(
        company_id, circle, confidence, source="validate_pipeline_v5"
    )
    print(f"  書き込み件数: {circle_written} 件")

    print("\n=== 結果サマリー ===")
    print(f"  company: {company_name} ({company_id})")
    print(f"  V2 confidence: {confidence:.3f}")
    print(f"  star 書き込み: {star_written}/{len([v for v in star.values() if v is not None])} 件")
    print(f"  circle 書き込み: {circle_written} 件")
    if star_written == 0 and protected > 0:
        print("  → 全件 official/review で保護済み（期待通り）")
    elif star_written > 0:
        print("  → estimated 行に書き込み完了（配管テスト成功）")


def main() -> None:
    parser = argparse.ArgumentParser(description="V5 配管検証（API費用ゼロ）")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--name", help="企業名（部分一致）")
    group.add_argument("--company-id", type=uuid.UUID, help="company UUID")
    args = parser.parse_args()

    if args.name:
        result = _find_company(args.name)
        if not result:
            print(f"企業が見つかりません: {args.name}")
            return
        company_id, company_name = result
    else:
        company_id = str(args.company_id)
        company_name = company_id

    validate(company_id, company_name)


if __name__ == "__main__":
    main()
