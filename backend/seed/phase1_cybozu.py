"""Phase 1: サイボウズ 原本層データ手動入力スクリプト

使用データ: 公開情報（有価証券報告書2023・技術ブログ・求人票・OpenWork）
実行前提: alembic upgrade head 完了済み / master_data.py 実行済み

実行: python -m backend.seed.phase1_cybozu
"""

import logging
import uuid
from datetime import date

import sqlalchemy as sa

from backend.database import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ============================================================
# サイボウズ 公開データ（出典付き）
# ============================================================

# --- 基本情報 ---
COMPANY_NAME = "株式会社サイボウズ"
OFFICIAL_URL = "https://cybozu.co.jp"
AS_OF = date(2024, 3, 31)  # 2023年度有価証券報告書基準日

# --- 業界 ---
INDUSTRIES = [
    "自社サービス（BtoB SaaS）",
    "クラウド・データセンター",
]

# --- 技術スタック（公式ブログ・GitHub・求人票より） ---
# (tag_name, source, confidence)
TECH_TAGS = [
    ("Go", "official", 0.95),
    ("TypeScript", "official", 0.95),
    ("JavaScript", "official", 0.90),
    ("Python", "official", 0.85),
    ("Kotlin", "official", 0.80),
    ("React", "official", 0.90),
    ("Vue.js", "official", 0.85),
    ("Kubernetes", "official", 0.95),
    ("Docker", "official", 0.95),
    ("GCP", "official", 0.90),
    ("AWS", "official", 0.80),
    ("PostgreSQL", "official", 0.85),
    ("MySQL", "official", 0.85),
    ("GitHub Actions", "official", 0.90),
    ("ArgoCD", "official", 0.85),
    ("Prometheus", "official", 0.80),
    ("Grafana", "official", 0.80),
    ("dbt", "review", 0.60),
]

# --- 給与データ（有価証券報告書2023 + 求人票） ---
# (category, amount_yen, includes_bonus, year, source, source_type, confidence)
SALARY_RECORDS = [
    (
        "初任給_院",
        240_000,
        False,
        2024,
        "https://cybozu.co.jp/recruit/fresh/salary/",
        "official",
        0.95,
    ),
    (
        "初任給_学部",
        220_000,
        False,
        2024,
        "https://cybozu.co.jp/recruit/fresh/salary/",
        "official",
        0.95,
    ),
    (
        "30歳平均",
        7_200_000,
        True,
        2023,
        "有価証券報告書2023 / OpenWork平均",
        "review",
        0.75,
    ),
]

# --- 売上データ（有価証券報告書2023） ---
# (year, revenue_yen, operating_profit_yen, is_profitable)
REVENUE_RECORDS = [
    (2023, 25_200_000_000, 4_000_000_000, True),
    (2022, 22_900_000_000, 3_500_000_000, True),
    (2021, 19_800_000_000, 3_100_000_000, True),
]

# --- 理念テキスト ---
COMPANY_TEXTS = [
    (
        "values",
        "チームワークあふれる社会を創る。"
        "サイボウズは、チームのパフォーマンスを最大化するソフトウェアを提供し、"
        "社会全体のチームワーク向上に貢献します。",
        "https://cybozu.co.jp/company/",
        "official",
    ),
    (
        "weakness",
        "グループウェア市場において国内シェアは高いが、"
        "グローバル展開は米国・中国・東南アジアで発展途上。"
        "Slackや Microsoft 365 との競合が激しい領域がある。",
        "有価証券報告書2023 リスク情報",
        "official",
    ),
    (
        "review",
        "心理的安全性が高く、失敗を責める文化がない。"
        "「100人100通りの働き方」を実践しており、リモートワークや副業も認められている。"
        "若手が早い段階でプロダクトに影響を与えられる機会が多い。",
        "OpenWork 2024",
        "review",
    ),
]

# --- 特徴量データ ---
# (feature_key, value_numeric, value_official, value_actual,
#  confidence, source, source_type, is_estimated)
#
# has_official_actual=True の項目は value_official と value_actual の両方を入れる
# それ以外は value_numeric のみ
FEATURE_VALUES = [
    # ===== 給与 (★, direct) =====
    (
        "starting_salary_master",
        240_000.0,
        None,
        None,
        0.95,
        "https://cybozu.co.jp/recruit/fresh/salary/",
        "official",
        False,
    ),
    (
        "salary_age30",
        7_200_000.0,
        None,
        None,
        0.75,
        "有価証券報告書2023 / OpenWork",
        "review",
        False,
    ),
    # ===== 2. ビジョン (★, scale5) =====
    (
        "f:new_biz_activeness",
        4.0,
        None,
        None,
        0.80,
        "blog.cybozu.io / 有価証券報告書2023",
        "official",
        False,
    ),
    # ===== 3. ビジネスモデル (★, scale5) =====
    (
        "f:competitive_advantage",
        4.0,
        None,
        None,
        0.80,
        "有価証券報告書2023 競合比較",
        "official",
        False,
    ),
    (
        "f:tech_barrier",
        3.0,
        None,
        None,
        0.70,
        "有価証券報告書2023 / IDC市場調査",
        "estimated",
        True,
    ),
    # ===== 4. 財務 (★, direct) =====
    (
        "rnd_ratio",
        25.0,
        None,
        None,
        0.85,
        "有価証券報告書2023 研究開発費",
        "official",
        False,
    ),
    (
        "capex_ratio",
        4.5,
        None,
        None,
        0.80,
        "有価証券報告書2023 設備投資",
        "official",
        False,
    ),
    # ===== 5. 業界動向 (★, scale5) =====
    (
        "f:trend_fit",
        3.0,
        None,
        None,
        0.70,
        "DX・グループウェア市場分析",
        "estimated",
        True,
    ),
    (
        "f:market_growth",
        3.0,
        None,
        None,
        0.70,
        "IDC グループウェア市場予測2023",
        "estimated",
        True,
    ),
    (
        "f:regulation_impact",
        3.0,
        None,
        None,
        0.65,
        "業界分析",
        "estimated",
        True,
    ),
    # ===== 6. 組織・カルチャー (★, scale5) =====
    (
        "f:psychological_safety",
        5.0,
        None,
        None,
        0.90,
        "OpenWork 2024 / 社員インタビュー記事多数",
        "review",
        False,
    ),
    (
        "f:bottom_up_degree",
        5.0,
        None,
        None,
        0.85,
        "「100人100通りの働き方」制度 / 社員ブログ",
        "official",
        False,
    ),
    (
        "f:text_comm_culture",
        5.0,
        None,
        None,
        0.85,
        "サイボウズ式 / 社内kintone活用事例",
        "official",
        False,
    ),
    (
        "f:exec_field_distance",
        2.0,
        None,
        None,
        0.80,
        "青野代表の社員全体向け発信 / 全社総会",
        "official",
        False,
    ),
    (
        "avg_age",
        33.4,
        None,
        None,
        0.95,
        "有価証券報告書2023",
        "official",
        False,
    ),
    (
        "diversity",
        4.0,
        None,
        None,
        0.80,
        "女性管理職比率・外国籍社員の割合（有価証券報告書2023）",
        "official",
        False,
    ),
    # ===== 7. 人事・評価・キャリア (★, scale5 / bool / direct) =====
    (
        "f:young_autonomy",
        4.0,
        None,
        None,
        0.85,
        "OpenWork / エンジニア採用サイト",
        "review",
        False,
    ),
    (
        "has_specialist_track",
        1.0,
        None,
        None,
        0.90,
        "https://cybozu.co.jp/recruit/mid-career/",
        "official",
        False,
    ),
    (
        "f:skill_support_quality",
        5.0,
        None,
        None,
        0.85,
        "書籍補助・カンファレンス支援制度（公式採用ページ）",
        "official",
        False,
    ),
    (
        "f:evaluation_system",
        4.0,
        None,
        None,
        0.80,
        "成果主義・評価制度の透明性（社員ブログ / OpenWork）",
        "review",
        False,
    ),
    (
        "review_freq_per_year",
        2.0,
        None,
        None,
        0.75,
        "求人票・社員ブログ",
        "review",
        False,
    ),
    (
        "f:unwanted_rotation_risk",
        2.0,
        None,
        None,
        0.80,
        "職種変更は本人希望尊重（採用FAQ・社員口コミ）",
        "review",
        False,
    ),
    (
        "f:training_quality",
        4.0,
        None,
        None,
        0.80,
        "新入社員研修制度（公式採用ページ）",
        "official",
        False,
    ),
    (
        "has_mentor",
        1.0,
        None,
        None,
        0.85,
        "新卒採用ページ onboarding 説明",
        "official",
        False,
    ),
    (
        "turnover_3yr",
        10.0,
        None,
        None,
        0.75,
        "OpenWork / 業界推定",
        "review",
        True,
    ),
    (
        "avg_tenure",
        8.0,
        None,
        None,
        0.85,
        "有価証券報告書2023 平均勤続年数",
        "official",
        False,
    ),
    # ===== 8. 待遇（official/actual 2スロット） =====
    # overtime_hours: has_official_actual=True → value_official=公称, value_actual=口コミ
    (
        "overtime_hours",
        None,
        15.0,
        12.0,
        0.80,
        "求人票（公称） / OpenWork残業実態",
        "review",
        False,
    ),
    # paid_leave_usage_pct
    (
        "paid_leave_usage_pct",
        None,
        80.0,
        78.0,
        0.80,
        "有価証券報告書2023（公称） / OpenWork",
        "review",
        False,
    ),
    # remote_rate
    (
        "remote_rate",
        None,
        100.0,
        85.0,
        0.85,
        "公式ワークスタイル方針（公称） / OpenWork実態",
        "review",
        False,
    ),
    (
        "has_flextime",
        1.0,
        None,
        None,
        0.95,
        "https://cybozu.co.jp/recruit/fresh/environment/",
        "official",
        False,
    ),
    (
        "f:leave_ease",
        5.0,
        None,
        None,
        0.90,
        "「有給取りやすい」口コミが多数 / OpenWork",
        "review",
        False,
    ),
    (
        "annual_holiday_days",
        125.0,
        None,
        None,
        0.95,
        "有価証券報告書2023 / 採用ページ",
        "official",
        False,
    ),
    (
        "has_housing_support",
        0.0,
        None,
        None,
        0.90,
        "採用FAQ（住宅補助なし）",
        "official",
        False,
    ),
    # ===== 9. 採用・選考 =====
    (
        "has_coding_test",
        1.0,
        None,
        None,
        0.90,
        "採用選考フロー（公式）",
        "official",
        False,
    ),
    (
        "field_engineer_joins",
        1.0,
        None,
        None,
        0.90,
        "技術面接に現場エンジニア同席（採用FAQ）",
        "official",
        False,
    ),
    (
        "interview_rounds",
        4.0,
        None,
        None,
        0.85,
        "採用選考フロー（公式）",
        "official",
        False,
    ),
    (
        "has_early_route",
        1.0,
        None,
        None,
        0.85,
        "インターンシップ → 早期選考ルートあり（採用ページ）",
        "official",
        False,
    ),
    (
        "salary_disclosed",
        1.0,
        None,
        None,
        0.85,
        "オファー面談で年収明示（口コミ）",
        "review",
        False,
    ),
    (
        "f:reverse_q_sincerity",
        4.0,
        None,
        None,
        0.75,
        "選考体験談（口コミサイト）",
        "review",
        False,
    ),
    # ===== 10. 開発環境 (★, scale5 / bool) =====
    (
        "has_data_lake",
        1.0,
        None,
        None,
        0.80,
        "blog.cybozu.io データ基盤記事",
        "official",
        False,
    ),
    (
        "f:mlops_maturity",
        4.0,
        None,
        None,
        0.80,
        "blog.cybozu.io CI/CD・SRE記事",
        "official",
        False,
    ),
    (
        "f:hw_sw_integration",
        1.0,
        None,
        None,
        0.70,
        "純SaaS企業のためハードへの関与なし",
        "official",
        False,
    ),
    (
        "f:data_platform_maturity",
        4.0,
        None,
        None,
        0.75,
        "blog.cybozu.io BigQuery・dbt活用記事",
        "official",
        False,
    ),
    (
        "f:tech_debt_culture",
        4.0,
        None,
        None,
        0.80,
        "blog.cybozu.io リファクタリング記事 / OSS貢献",
        "official",
        False,
    ),
    (
        "f:dev_process_maturity",
        4.0,
        None,
        None,
        0.80,
        "blog.cybozu.io スクラム・アジャイル記事",
        "official",
        False,
    ),
    (
        "f:dev_experience_quality",
        4.0,
        None,
        None,
        0.80,
        "高スペックPC支給・ツール選択自由（採用ページ・口コミ）",
        "review",
        False,
    ),
    (
        "oss_blog_freq",
        8.0,
        None,
        None,
        0.90,
        "blog.cybozu.io 月間投稿数（2024年1月-3月平均）",
        "official",
        False,
    ),
    # ===== 2. ビジョン 〇 =====
    (
        "f:tech_investment_direction",
        4.0,
        None,
        None,
        0.80,
        "中期経営計画 AI機能追加・クラウドネイティブ化",
        "official",
        False,
    ),
    # ===== 再考✕ (weight=0.2) =====
    (
        "stability",
        4.0,
        None,
        None,
        0.85,
        "有価証券報告書2023 自己資本比率・継続黒字",
        "official",
        False,
    ),
    # ===== gap 対応追加 =====
    # 3. ビジネスモデル ★
    (
        "patent_count",
        20.0,
        None,
        None,
        0.60,
        "J-PlatPat 特許検索 2024（推定）",
        "estimated",
        True,
    ),
    # 6. 組織
    (
        "organization_type",
        4.0,
        None,
        None,
        0.80,
        "サイボウズ式 / 事業部から独立した開発チーム構成",
        "official",
        False,
    ),
    # 8. 待遇 — みなし残業
    (
        "has_fixed_ot",
        1.0,
        None,
        None,
        0.85,
        "求人票（固定残業代30時間分含む）",
        "official",
        False,
    ),
    (
        "fixed_ot_hours",
        30.0,
        None,
        None,
        0.80,
        "求人票 固定残業時間数",
        "official",
        False,
    ),
    # 9. 採用
    (
        "placement_guaranteed",
        0.0,
        None,
        None,
        0.85,
        "配属確約制度なし（採用FAQ）",
        "official",
        False,
    ),
    # 2. ビジョン
    (
        "f:global_expansion",
        3.0,
        None,
        None,
        0.80,
        "米国・中国・東南アジア拠点あり / 開発は国内中心",
        "official",
        False,
    ),
]


# ============================================================
# 実行ロジック
# ============================================================


def run() -> None:
    with get_session() as session:
        company_id = _find_company(session)
        if company_id is None:
            log.error(
                "サイボウズが company テーブルに見つかりません。seed 済みか確認してください。"
            )
            return

        log.info("サイボウズ company_id = %s", company_id)

        _update_company(session, company_id)
        _insert_industries(session, company_id)
        _insert_tech_tags(session, company_id)
        _insert_salary_records(session, company_id)
        _insert_revenue_records(session, company_id)
        _insert_company_texts(session, company_id)
        _insert_company_features(session, company_id)

        log.info("=== 投入完了 ===")
        _show_completeness(session, company_id)


def _find_company(session) -> uuid.UUID | None:
    result = session.execute(
        sa.text("SELECT id FROM company WHERE name LIKE :name LIMIT 1"),
        {"name": "%サイボウズ%"},
    ).fetchone()
    return result[0] if result else None


def _update_company(session, company_id: uuid.UUID) -> None:
    session.execute(
        sa.text(
            "UPDATE company SET"
            "  has_relocation = FALSE,"
            "  engineer_count = 600,"
            "  listing_type   = '東証プライム',"
            "  founded_year   = 1997,"
            "  target_market  = 'BtoB'"
            " WHERE id = :cid"
        ),
        {"cid": str(company_id)},
    )
    log.info(
        "company: has_relocation / engineer_count / listing_type / founded_year / target_market 更新"
    )


def _insert_industries(session, company_id: uuid.UUID) -> None:
    for name in INDUSTRIES:
        row = session.execute(
            sa.text("SELECT id FROM industry_master WHERE name = :name"),
            {"name": name},
        ).fetchone()
        if row is None:
            log.warning(
                "industry_master に '%s' が見つかりません。master_data.py を実行済みか確認", name
            )
            continue
        session.execute(
            sa.text(
                "INSERT INTO company_industry (company_id, industry_id)"
                " VALUES (:cid, :iid) ON CONFLICT DO NOTHING"
            ),
            {"cid": str(company_id), "iid": row[0]},
        )
    log.info("company_industry: %d 件", len(INDUSTRIES))


def _insert_tech_tags(session, company_id: uuid.UUID) -> None:
    count = 0
    for tag_name, source_type, confidence in TECH_TAGS:
        tag = session.execute(
            sa.text("SELECT id FROM tech_tag WHERE name = :name"),
            {"name": tag_name},
        ).fetchone()
        if tag is None:
            log.warning("tech_tag '%s' が見つかりません", tag_name)
            continue
        session.execute(
            sa.text(
                "INSERT INTO company_tech"
                " (id, company_id, tech_tag_id, role_id, source, source_type,"
                "  confidence, as_of_date)"
                " VALUES (:id, :cid, :tid, NULL, :src, :st, :conf, :aod)"
                " ON CONFLICT ON CONSTRAINT uq_company_tech DO NOTHING"
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": str(company_id),
                "tid": tag[0],
                "src": "blog.cybozu.io / github.com/cybozu / 求人票",
                "st": source_type,
                "conf": confidence,
                "aod": AS_OF,
            },
        )
        count += 1
    log.info("company_tech: %d 件", count)


def _insert_salary_records(session, company_id: uuid.UUID) -> None:
    for category, amount, includes_bonus, year, source, source_type, conf in SALARY_RECORDS:
        session.execute(
            sa.text(
                "INSERT INTO salary_record"
                " (id, company_id, category, amount, includes_bonus, year,"
                "  source, source_type, confidence, as_of_date)"
                " VALUES (:id, :cid, :cat, :amt, :ib, :yr, :src, :st, :conf, :aod)"
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": str(company_id),
                "cat": category,
                "amt": amount,
                "ib": includes_bonus,
                "yr": year,
                "src": source,
                "st": source_type,
                "conf": conf,
                "aod": AS_OF,
            },
        )
    log.info("salary_record: %d 件", len(SALARY_RECORDS))


def _insert_revenue_records(session, company_id: uuid.UUID) -> None:
    for year, revenue, op_profit, is_profitable in REVENUE_RECORDS:
        session.execute(
            sa.text(
                "INSERT INTO revenue_record"
                " (id, company_id, year, revenue, operating_profit, is_profitable)"
                " VALUES (:id, :cid, :yr, :rev, :op, :ip)"
                " ON CONFLICT DO NOTHING"
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": str(company_id),
                "yr": year,
                "rev": revenue,
                "op": op_profit,
                "ip": is_profitable,
            },
        )
    log.info("revenue_record: %d 件", len(REVENUE_RECORDS))


def _insert_company_texts(session, company_id: uuid.UUID) -> None:
    for kind, body, source, source_type in COMPANY_TEXTS:
        session.execute(
            sa.text(
                "INSERT INTO company_text (id, company_id, kind, body, source, source_type, as_of_date)"
                " VALUES (:id, :cid, :kind, :body, :src, :st, :aod)"
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": str(company_id),
                "kind": kind,
                "body": body,
                "src": source,
                "st": source_type,
                "aod": AS_OF,
            },
        )
    log.info("company_text: %d 件", len(COMPANY_TEXTS))


def _insert_company_features(session, company_id: uuid.UUID) -> None:
    count = 0
    for (
        fkey,
        value_numeric,
        value_official,
        value_actual,
        confidence,
        source,
        source_type,
        is_estimated,
    ) in FEATURE_VALUES:
        # feature_key の存在確認
        exists = session.execute(
            sa.text("SELECT 1 FROM feature_definition WHERE feature_key = :fkey"),
            {"fkey": fkey},
        ).fetchone()
        if exists is None:
            log.warning("feature_definition に '%s' が見つかりません", fkey)
            continue

        session.execute(
            sa.text(
                "INSERT INTO company_feature"
                " (id, company_id, role_id, feature_key,"
                "  value_numeric, value_official, value_actual,"
                "  confidence, source, source_type, as_of_date, is_estimated)"
                " VALUES"
                " (:id, :cid, NULL, :fkey,"
                "  :vn, :vo, :va,"
                "  :conf, :src, :st, :aod, :est)"
                " ON CONFLICT ON CONSTRAINT uq_company_feature DO UPDATE SET"
                "  value_numeric  = EXCLUDED.value_numeric,"
                "  value_official = EXCLUDED.value_official,"
                "  value_actual   = EXCLUDED.value_actual,"
                "  confidence     = EXCLUDED.confidence,"
                "  source         = EXCLUDED.source,"
                "  source_type    = EXCLUDED.source_type,"
                "  is_estimated   = EXCLUDED.is_estimated,"
                "  updated_at     = now()"
            ),
            {
                "id": str(uuid.uuid4()),
                "cid": str(company_id),
                "fkey": fkey,
                "vn": value_numeric,
                "vo": value_official,
                "va": value_actual,
                "conf": confidence,
                "src": source,
                "st": source_type,
                "aod": AS_OF,
                "est": is_estimated,
            },
        )
        count += 1
    log.info("company_feature: %d 件投入", count)


def _show_completeness(session, company_id: uuid.UUID) -> None:
    result = session.execute(
        sa.text(
            "SELECT filled, applicable, completeness_ratio"
            " FROM company_completeness WHERE company_id = :cid"
        ),
        {"cid": str(company_id)},
    ).fetchone()
    if result:
        log.info(
            "company_completeness: %d / %d 件埋め済み（網羅率 %.0f%%）",
            result[0],
            result[1],
            (result[2] or 0) * 100,
        )
    else:
        log.warning("company_completeness が取得できませんでした")


if __name__ == "__main__":
    run()
