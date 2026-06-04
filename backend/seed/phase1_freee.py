"""Phase 1: freee株式会社 — 原本層・特徴量データ投入

既存 DB の company レコード (ID: 155833cd-aa97-46d2-888d-544ba7ba0b95) に対して
tech_tag / salary_record / company_feature を投入し、正規化まで実行する。

freee 特性:
  - BtoB SaaS（会計・HR・税務）
  - Go / TypeScript 主体、クラウドネイティブ
  - WLB 良好、リモートワーク積極的
  - ML/AI は補助的（会計データ活用）、MLOps 成熟度は中程度

実行: python -m backend.seed.phase1_freee
"""

import logging
import uuid

import sqlalchemy as sa

from backend.database import get_session
from backend.seed.normalizer import normalize_all

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

COMPANY_ID = uuid.UUID("155833cd-aa97-46d2-888d-544ba7ba0b95")

# ============================================================
# Tech Stack
# ============================================================
TECH_TAGS: list[str] = [
    "Go",
    "TypeScript",
    "JavaScript",
    "Ruby",
    "Python",
    "React",
    "Next.js",
    "Ruby on Rails",
    "FastAPI",
    "AWS",
    "GCP",
    "Kubernetes",
    "Docker",
    "GitHub Actions",
    "ArgoCD",
    "Prometheus",
    "Grafana",
    "PostgreSQL",
    "BigQuery",
    "dbt",
    "Apache Kafka",
]

# ============================================================
# 給与記録
# ============================================================
SALARY_RECORDS: list[dict] = [
    {
        "category": "初任給_学部",
        "amount": 230_000,
        "includes_bonus": False,
        "year": 2024,
        "source": "採用ページ",
        "source_type": "official",
        "confidence": 0.85,
        "is_estimated": False,
    },
    {
        "category": "初任給_院",
        "amount": 250_000,
        "includes_bonus": False,
        "year": 2024,
        "source": "採用ページ",
        "source_type": "official",
        "confidence": 0.85,
        "is_estimated": False,
    },
    {
        "category": "30歳平均",
        "amount": 7_500_000,
        "includes_bonus": True,
        "year": 2023,
        "source": "OpenWork",
        "source_type": "review",
        "confidence": 0.65,
        "is_estimated": True,
    },
]

# ============================================================
# company_feature 値
# ============================================================
# (feature_key, value_numeric, value_official, value_actual,
#  confidence, source, source_type, is_estimated)
FEATURE_VALUES: list[tuple] = [
    # ── 待遇（has_official_actual=True） ────────────────────────
    ("overtime_hours", None, 0, 15, 0.80, "OpenWork", "review", True),
    ("remote_rate", None, 100, 90, 0.80, "公式ワークスタイル", "official", False),
    ("paid_leave_usage_pct", None, None, 70, 0.70, "OpenWork", "review", True),
    # ── 待遇 direct ─────────────────────────────────────────────
    ("starting_salary_master", 250_000, None, None, 0.85, "採用ページ", "official", False),
    ("salary_age30", 7_500_000, None, None, 0.65, "OpenWork", "review", True),
    ("annual_holiday_days", 124, None, None, 0.85, "採用ページ", "official", False),
    ("avg_tenure", 4.2, None, None, 0.65, "OpenWork", "review", True),
    ("turnover_3yr", 20, None, None, 0.65, "OpenWork", "review", True),
    ("avg_age", 31.0, None, None, 0.70, "有価証券報告書2023", "official", False),
    ("rnd_ratio", 8.0, None, None, 0.70, "有価証券報告書2023", "official", False),
    ("review_freq_per_year", 4.0, None, None, 0.70, "採用ページ", "official", False),
    ("interview_rounds", 4.0, None, None, 0.70, "採用情報", "official", False),
    # ── bool ────────────────────────────────────────────────────
    ("has_flextime", 1.0, None, None, 0.90, "公式ワークスタイル", "official", False),
    ("has_coding_test", 1.0, None, None, 0.85, "採用情報・体験談", "official", False),
    ("field_engineer_joins", 1.0, None, None, 0.80, "採用情報", "official", False),
    ("has_specialist_track", 1.0, None, None, 0.75, "採用ページ", "official", False),
    ("has_mentor", 1.0, None, None, 0.75, "採用ページ", "official", False),
    ("has_early_route", 1.0, None, None, 0.70, "採用情報", "official", False),
    ("has_data_lake", 1.0, None, None, 0.80, "技術ブログ", "official", False),
    ("has_housing_support", 0.0, None, None, 0.75, "採用ページ", "official", False),
    ("has_fixed_ot", 0.0, None, None, 0.85, "採用ページ", "official", False),
    ("placement_guaranteed", 1.0, None, None, 0.75, "採用情報", "official", False),
    ("salary_disclosed", 1.0, None, None, 0.85, "採用情報", "official", False),
    # ── scale5 ──────────────────────────────────────────────────
    ("f:new_biz_activeness", 3.0, None, None, 0.70, "公式IR・ブログ", "official", False),
    ("f:competitive_advantage", 3.0, None, None, 0.65, "IR資料", "official", False),
    ("f:tech_barrier", 2.0, None, None, 0.65, "推定", "estimated", True),
    ("f:trend_fit", 3.0, None, None, 0.70, "市場分析", "estimated", True),
    ("f:market_growth", 4.0, None, None, 0.70, "SMB SaaS市場", "estimated", True),
    ("f:regulation_impact", 4.0, None, None, 0.70, "電子帳簿保存法等", "official", False),
    ("f:psychological_safety", 4.0, None, None, 0.75, "OpenWork・社員ブログ", "review", True),
    ("f:bottom_up_degree", 4.0, None, None, 0.70, "社員ブログ", "review", True),
    ("f:text_comm_culture", 4.0, None, None, 0.70, "技術ブログ", "review", True),
    ("f:exec_field_distance", 2.0, None, None, 0.70, "全員MTG制度あり", "official", False),
    ("organization_type", 4.0, None, None, 0.70, "組織構造", "estimated", True),
    ("f:young_autonomy", 4.0, None, None, 0.75, "OpenWork・社員ブログ", "review", True),
    ("f:skill_support_quality", 4.0, None, None, 0.75, "採用ページ", "official", False),
    ("f:evaluation_system", 4.0, None, None, 0.70, "採用ページ", "official", False),
    ("f:training_quality", 4.0, None, None, 0.70, "採用ページ", "official", False),
    ("f:unwanted_rotation_risk", 2.0, None, None, 0.75, "職種別採用", "official", False),
    ("f:tech_investment_direction", 4.0, None, None, 0.75, "技術ブログ", "official", False),
    ("f:leave_ease", 4.0, None, None, 0.70, "OpenWork", "review", True),
    ("f:reverse_q_sincerity", 4.0, None, None, 0.65, "体験談", "review", True),
    ("f:tech_stack_modernity", None, None, None, 0.0, "", "estimated", True),  # V5外: skip
    ("f:cloud_maturity", None, None, None, 0.0, "", "estimated", True),  # V5外: skip
    # MLOps は中程度（会計 SaaS で ML はデータ分析レベル）
    ("f:mlops_maturity", 3.0, None, None, 0.65, "技術ブログ", "official", False),
    (
        "f:data_platform_maturity",
        4.0,
        None,
        None,
        0.75,
        "技術ブログ・BigQuery活用",
        "official",
        False,
    ),
    ("f:tech_debt_culture", 4.0, None, None, 0.70, "技術ブログ・Go移行記事", "official", False),
    (
        "f:dev_process_maturity",
        4.0,
        None,
        None,
        0.70,
        "スクラム採用・技術ブログ",
        "official",
        False,
    ),
    ("f:dev_experience_quality", 4.0, None, None, 0.70, "社員ブログ", "official", False),
    ("f:hw_sw_integration", 1.0, None, None, 0.90, "純SaaS", "official", False),
    ("f:global_expansion", 1.0, None, None, 0.80, "国内専業", "official", False),
    ("stability", 4.0, None, None, 0.75, "東証グロース・黒字", "official", False),
    ("diversity", 3.0, None, None, 0.65, "有価証券報告書", "official", False),
    ("oss_blog_freq", 8.0, None, None, 0.80, "tech.freee.co.jp", "official", False),
    ("patent_count", 15.0, None, None, 0.65, "J-PlatPat推定", "estimated", True),
]


def run() -> None:
    cid = str(COMPANY_ID)

    # company 基本情報更新
    _update_company(cid)

    # tech_tags
    _insert_tech_tags(cid)

    # salary_records
    _insert_salary_records(cid)

    # company_feature
    written = _insert_features(cid)
    log.info("company_feature: %d 件投入", written)

    # 正規化
    normalize_all(COMPANY_ID)
    log.info("freee: 投入・正規化完了")


def _update_company(cid: str) -> None:
    with get_session() as session:
        session.execute(
            sa.text(
                "UPDATE company SET"
                "  has_relocation = :rel,"
                "  engineer_count = :eng,"
                "  listing_type   = :lst,"
                "  founded_year   = :yr,"
                "  target_market  = :mkt"
                " WHERE id = :id"
            ),
            {
                "rel": False,
                "eng": 500,
                "lst": "東証グロース",
                "yr": 2012,
                "mkt": "BtoB",
                "id": cid,
            },
        )
    log.info("company 更新完了")


def _get_tech_tag_id(session, name: str) -> int | None:
    row = session.execute(
        sa.text("SELECT id FROM tech_tag WHERE name = :n"), {"n": name}
    ).fetchone()
    return row[0] if row else None


def _insert_tech_tags(cid: str) -> None:
    with get_session() as session:
        for tag_name in TECH_TAGS:
            tid = _get_tech_tag_id(session, tag_name)
            if tid is None:
                log.warning("tech_tag 未登録: %s", tag_name)
                continue
            session.execute(
                sa.text(
                    "INSERT INTO company_tech"
                    " (id, company_id, tech_tag_id, role_id, source, source_type, confidence)"
                    " VALUES (gen_random_uuid(), :cid, :tid, NULL, :src, 'official', 0.85)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"cid": cid, "tid": tid, "src": "採用ページ・技術ブログ"},
            )
    log.info("tech_tag: %d 件処理", len(TECH_TAGS))


def _insert_salary_records(cid: str) -> None:
    with get_session() as session:
        for rec in SALARY_RECORDS:
            session.execute(
                sa.text(
                    "INSERT INTO salary_record"
                    " (id, company_id, category, amount, includes_bonus,"
                    "  year, source, source_type, confidence, is_estimated)"
                    " VALUES (gen_random_uuid(), :cid, :category, :amount, :includes_bonus,"
                    "         :year, :source, :source_type, :confidence, :is_estimated)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"cid": cid, **rec},
            )
    log.info("salary_record: %d 件処理", len(SALARY_RECORDS))


def _upsert_feature(
    session,
    cid: str,
    feature_key: str,
    value_numeric,
    value_official,
    value_actual,
    confidence: float,
    source: str,
    source_type: str,
    is_estimated: bool,
) -> bool:
    """company_feature を upsert する。値が全 None のエントリはスキップ。"""
    if value_numeric is None and value_official is None and value_actual is None:
        return False

    existing = session.execute(
        sa.text(
            "SELECT id FROM company_feature"
            " WHERE company_id = :cid AND role_id IS NULL AND feature_key = :fkey"
        ),
        {"cid": cid, "fkey": feature_key},
    ).fetchone()

    if existing:
        session.execute(
            sa.text(
                "UPDATE company_feature SET"
                "  value_numeric   = COALESCE(:vnum, value_numeric),"
                "  value_official  = COALESCE(:voff, value_official),"
                "  value_actual    = COALESCE(:vact, value_actual),"
                "  confidence = :conf, source = :src,"
                "  source_type = :stype, is_estimated = :est,"
                "  updated_at = now()"
                " WHERE id = :id"
            ),
            {
                "vnum": value_numeric,
                "voff": value_official,
                "vact": value_actual,
                "conf": confidence,
                "src": source,
                "stype": source_type,
                "est": is_estimated,
                "id": str(existing.id),
            },
        )
    else:
        session.execute(
            sa.text(
                "INSERT INTO company_feature"
                " (id, company_id, role_id, feature_key,"
                "  value_numeric, value_official, value_actual,"
                "  confidence, source, source_type, as_of_date, is_estimated)"
                " VALUES (gen_random_uuid(), :cid, NULL, :fkey,"
                "         :vnum, :voff, :vact,"
                "         :conf, :src, :stype, CURRENT_DATE, :est)"
            ),
            {
                "cid": cid,
                "fkey": feature_key,
                "vnum": value_numeric,
                "voff": value_official,
                "vact": value_actual,
                "conf": confidence,
                "src": source,
                "stype": source_type,
                "est": is_estimated,
            },
        )
    return True


def _insert_features(cid: str) -> int:
    written = 0
    with get_session() as session:
        for row in FEATURE_VALUES:
            fkey, vnum, voff, vact, conf, src, stype, est = row
            ok = _upsert_feature(session, cid, fkey, vnum, voff, vact, conf, src, stype, est)
            if ok:
                written += 1
    return written


if __name__ == "__main__":
    run()
