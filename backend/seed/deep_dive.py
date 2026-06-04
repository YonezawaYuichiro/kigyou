"""1社の「完璧な分析」パイプライン。

企業名を指定して全分析ステップを順番に実行し、結果をコンソールに表示する。

実行例:
  python -m backend.seed.deep_dive --name "GrapeCity"
  python -m backend.seed.deep_dive --name "GrapeCity" --skip-gemini
"""

import argparse
import logging
import sys
import time

import sqlalchemy as sa

from backend.database import get_session
from backend.models import Company, CompanyDimensions, CompanyVector
from backend.seed.blog_analyzer import analyze_company_blog
from backend.seed.github_org_analyzer import analyze_github_org

logger = logging.getLogger(__name__)

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


def _find_company(name_contains: str) -> tuple[str, str, str] | None:
    """企業名の部分一致でDB検索。(id, name, official_url) を返す。"""
    with get_session() as session:
        rows = session.execute(
            sa.select(Company.id, Company.name, Company.official_url).where(
                Company.name.contains(name_contains)
            )
        ).all()
    if not rows:
        return None
    if len(rows) > 1:
        print(f"⚠️  複数候補: {[r.name for r in rows]} → 最初の1社を使用")
    return str(rows[0].id), rows[0].name, rows[0].official_url


def _show_current_state(company_id: str, company_name: str) -> None:
    """現在のDB状態（dimensions + vector）を表示する。"""
    with get_session() as session:
        dims = session.execute(
            sa.select(CompanyDimensions).where(CompanyDimensions.company_id == company_id)
        ).scalar_one_or_none()
        vec = session.execute(
            sa.select(CompanyVector).where(CompanyVector.company_id == company_id)
        ).scalar_one_or_none()

        conf = dims.overall_confidence if dims else None
        scores = list(vec.dim_scores) if vec and vec.dim_scores is not None else None
        model_ver = vec.model_version if vec else "なし"

    print(f"\n{'─' * 60}")
    print(f"  企業: {company_name}")
    print(f"  信頼度: {conf:.3f}" if conf else "  信頼度: 未取得")
    print(f"  ベクトルバージョン: {model_ver}")
    if scores:
        print("  10次元スコア:")
        for i, (name, score) in enumerate(zip(_DIM_NAMES, scores, strict=False)):
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            flag = " ⚠️" if abs(score - 0.40) < 0.01 or abs(score - 0.50) < 0.01 else ""
            print(f"    dim[{i}] {name:<10}: {score:.3f} [{bar}]{flag}")
    else:
        print("  ベクトル: 未計算")
    print(f"{'─' * 60}")


def run_deep_dive(name_contains: str, skip_gemini: bool = False) -> None:
    """GrapeCity 等1社の完璧な分析を実行する。"""
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

    # ── 企業検索 ──────────────────────────────────────────────────────────
    result = _find_company(name_contains)
    if not result:
        print(f"❌ 企業が見つかりません: '{name_contains}'")
        return
    company_id, company_name, official_url = result
    print(f"\n🎯 対象企業: {company_name}")
    print(f"   URL: {official_url}")

    # ── Step 1: 現状確認 ──────────────────────────────────────────────────
    print("\n📊 Step 1: 現在のDB状態を確認")
    _show_current_state(company_id, company_name)

    # ── Step 2: GitHub Org解析（無料） ────────────────────────────────────
    print("\n🐙 Step 2: GitHub Organization 解析")
    gh_stats = analyze_github_org(company_name, official_url)
    if gh_stats.fetch_error:
        print(f"  結果: 未検出 ({gh_stats.fetch_error})")
    else:
        print(f"  org  : {gh_stats.org_url}")
        print(
            f"  repos: {gh_stats.public_repo_count}件（アクティブ: {gh_stats.active_repo_count}件）"
        )
        print(f"  言語 : {', '.join(gh_stats.top_languages[:5])}")
        print(f"  ⭐   : {gh_stats.total_stars}")
        print(f"  スコア: {gh_stats.compute_github_score():.3f}")
        ev = gh_stats.to_evidence_text()
        if ev:
            print(f"  証拠 : {ev}")
        # V5: github_activity_score を company_feature に書き込む
        try:
            from backend.seed.feature_writer_v5 import write_github_stats

            write_github_stats(company_id, gh_stats.compute_github_score())
            print("  [V5] github_activity_score 更新")
        except Exception as e:
            print(f"  [V5] GitHub書き込み失敗: {e}")
    time.sleep(0.5)

    # ── Step 3: ブログ解析 ────────────────────────────────────────────────
    print("\n📝 Step 3: 技術ブログ解析")
    blog_stats = analyze_company_blog(company_name, official_url)
    if blog_stats.fetch_error:
        print(f"  結果: 未検出 ({blog_stats.fetch_error})")
        if blog_stats.fetch_error == "ブログ未検出" and not skip_gemini:
            print("  ※ Gemini検索が有効なら再度試みられます")
    else:
        print(f"  platform : {blog_stats.platform}")
        print(f"  url      : {blog_stats.blog_url}")
        print(f"  記事/年  : {blog_stats.article_count_1yr}件")
        print(f"  月平均   : {blog_stats.post_frequency_per_month:.1f}件/月")
        print(f"  著者数   : {blog_stats.unique_authors}名")
        print(f"  技術タグ : {', '.join(blog_stats.tech_tags[:6])}")
        print(f"  スコア   : {blog_stats.compute_blog_score():.3f}")
        # V5: oss_blog_freq を company_feature に書き込む
        try:
            from backend.seed.feature_writer_v5 import write_blog_stats

            write_blog_stats(company_id, blog_stats.post_frequency_per_month)
            print("  [V5] oss_blog_freq 更新")
        except Exception as e:
            print(f"  [V5] ブログ書き込み失敗: {e}")
    time.sleep(0.5)

    # ── Step 4: Dimensions抽出（Gemini必要） ──────────────────────────────
    if skip_gemini:
        print("\n🤖 Step 4: Dimensions抽出 → スキップ（--skip-gemini）")
        print("  ※ Gemini APIクレジット補充後に再実行: ")
        print(f'     python -m backend.seed.deep_dive --name "{name_contains}"')
    else:
        print("\n🤖 Step 4: Dimensions抽出（Gemini + Haiku）")
        try:
            from backend.seed.dimensions_extractor import extract_single_company

            extract_single_company(name_contains)
            print("  ✅ 抽出完了")
        except Exception as e:
            print(f"  ❌ 抽出失敗: {e}")

    # ── Step 5: ベクトル再計算（無料） ───────────────────────────────────
    # V5: value_normalized を最新状態に更新してからベクトルを再計算する
    try:
        import uuid as _uuid

        from backend.seed.normalizer import normalize_all

        normalize_all(_uuid.UUID(company_id))
        print("\n[V5] value_normalized 更新完了")
    except Exception as e:
        print(f"\n[V5] 正規化失敗: {e}")

    print("\n🔢 Step 5: 10次元ベクトル再計算")
    try:
        from sqlalchemy.orm import selectinload

        from backend.seed.vector_builder import (
            _upsert_hiring_difficulty,
            _upsert_vector,
            build_vector,
            compute_hiring_difficulty,
        )

        with get_session() as session:
            company = session.execute(
                sa.select(Company)
                .options(selectinload(Company.dimensions), selectinload(Company.metrics))
                .where(Company.id == company_id)
            ).scalar_one_or_none()
            if company:
                dims = company.dimensions
                metrics = company.metrics
                scores = build_vector(company, dims, metrics)
                difficulty = compute_hiring_difficulty(dims, metrics) if dims else None

        import uuid

        cid_uuid = uuid.UUID(company_id)
        _upsert_vector(cid_uuid, scores)
        if difficulty is not None:
            _upsert_hiring_difficulty(cid_uuid, difficulty)
        print(f"  ✅ スコア: {[round(s, 3) for s in scores]}")
        print(f"  採用難易度: {difficulty:.3f}" if difficulty else "  採用難易度: N/A")
    except Exception as e:
        print(f"  ❌ 再計算失敗: {e}")

    # ── Step 6: 最終状態表示 ─────────────────────────────────────────────
    print("\n📊 Step 6: 最終状態")
    _show_current_state(company_id, company_name)

    # 「完璧」チェック
    with get_session() as session:
        dims = session.execute(
            sa.select(CompanyDimensions).where(CompanyDimensions.company_id == company_id)
        ).scalar_one_or_none()
        conf = dims.overall_confidence if dims else None

    print("\n🏁 完璧度チェック:")
    items = [
        ("信頼度 ≥ 0.70", conf is not None and conf >= 0.70),
        ("GitHub検出", gh_stats.public_repo_count > 0),
        ("ブログ検出", blog_stats.article_count_1yr > 0),
        ("Dimensions抽出済み", not skip_gemini),
    ]
    all_ok = all(ok for _, ok in items)
    for label, ok in items:
        print(f"  {'✅' if ok else '❌'} {label}")

    if all_ok:
        print("\n🎉 完璧！次は同じ要領で20社、そして340社に展開できます。")
    else:
        missing = [label for label, ok in items if not ok]
        print(f"\n⚠️  残課題: {', '.join(missing)}")
        if skip_gemini:
            print("   → Gemini APIクレジット補充後に --skip-gemini なしで再実行")


if __name__ == "__main__":
    logging.basicConfig(level="WARNING")

    parser = argparse.ArgumentParser(description="1社の完璧な分析パイプライン")
    parser.add_argument("--name", required=True, help="企業名（部分一致）")
    parser.add_argument(
        "--skip-gemini", action="store_true", help="Gemini API不要のステップのみ実行"
    )
    args = parser.parse_args()

    run_deep_dive(args.name, skip_gemini=args.skip_gemini)
