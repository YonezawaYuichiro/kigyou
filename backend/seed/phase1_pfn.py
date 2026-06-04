"""Phase 1: Preferred Networks株式会社 — 原本層・特徴量データ投入

DB に存在しない場合は company を新規 INSERT してから投入する。

PFN 特性:
  - 日本最高峰の深層学習研究会社
  - 独自AIチップ MN-Core を設計・開発（ハード×ソフト連携の核心）
  - Python / C++ / CUDA 主体、GPU クラスタ・クラウド並用
  - R&D 比率が極めて高い（売上のほぼ全額を研究開発へ）
  - 特許多数（自動運転・創薬・製造向けAI）
  - 未上場（ベンチャー、DeNA/トヨタ等が出資）

sanity check 事前宣言:
  このペルソナ（MLエンジニア志向）では PFN > サイボウズ(84.0) > freee の順を期待する。
  根拠: PFN は f:mlops_maturity=5, f:data_platform_maturity=5, rnd_ratio=70% が
  ユーザーの高優先度 weight(1.5) に刺さるため。

実行: python -m backend.seed.phase1_pfn
"""

import logging
import uuid

import sqlalchemy as sa

from backend.database import get_session
from backend.seed.normalizer import normalize_all

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

COMPANY_URL = "https://www.preferred.jp"
COMPANY_NAME = "Preferred Networks株式会社"

# ============================================================
# Tech Stack
# ============================================================
TECH_TAGS: list[str] = [
    "Python",
    "C++",
    "C",
    "Rust",
    "TypeScript",
    "PyTorch",
    "JAX",
    "ONNX",
    "MLflow",
    "Kubeflow",
    "Weights & Biases",
    "Ray",
    "Triton Inference Server",
    "Kubernetes",
    "Docker",
    "GCP",
    "AWS",
    "NVIDIA GPU",
    "エッジAI",
    "ロボティクス",
    "Prometheus",
    "Grafana",
]

# ============================================================
# 給与記録
# ============================================================
SALARY_RECORDS: list[dict] = [
    {
        "category": "初任給_院",
        "amount": 300_000,
        "includes_bonus": False,
        "year": 2024,
        "source": "採用ページ",
        "source_type": "official",
        "confidence": 0.85,
        "is_estimated": False,
    },
    {
        "category": "初任給_学部",
        "amount": 270_000,
        "includes_bonus": False,
        "year": 2024,
        "source": "採用ページ",
        "source_type": "official",
        "confidence": 0.85,
        "is_estimated": False,
    },
    {
        "category": "30歳平均",
        "amount": 9_000_000,
        "includes_bonus": True,
        "year": 2023,
        "source": "OpenWork・推定",
        "source_type": "estimated",
        "confidence": 0.55,
        "is_estimated": True,
    },
]

# ============================================================
# 収益記録（ほぼ非公開、推定）
# ============================================================
REVENUE_RECORDS: list[dict] = [
    {
        "year": 2023,
        "revenue": None,  # 非公開
        "operating_profit": None,
        "is_profitable": False,  # R&D投資フェーズで赤字
    },
]

# ============================================================
# company_feature 値
# ============================================================
# (feature_key, value_numeric, value_official, value_actual,
#  confidence, source, source_type, is_estimated)
FEATURE_VALUES: list[tuple] = [
    # ── 待遇（has_official_actual=True） ────────────────────────
    ("overtime_hours", None, None, 25, 0.60, "推定・研究職カルチャー", "estimated", True),
    ("remote_rate", None, 80, 75, 0.70, "採用ページ", "official", False),
    ("paid_leave_usage_pct", None, None, 60, 0.55, "推定", "estimated", True),
    # ── 待遇 direct ─────────────────────────────────────────────
    ("starting_salary_master", 300_000, None, None, 0.85, "採用ページ", "official", False),
    ("salary_age30", 9_000_000, None, None, 0.55, "推定", "estimated", True),
    ("annual_holiday_days", 120, None, None, 0.70, "採用ページ", "official", False),
    ("avg_tenure", 3.5, None, None, 0.55, "推定・若い会社", "estimated", True),
    ("turnover_3yr", 25.0, None, None, 0.55, "推定", "estimated", True),
    ("avg_age", 29.0, None, None, 0.70, "採用情報・若い組織", "estimated", True),
    ("rnd_ratio", 70.0, None, None, 0.75, "事業報告・R&D特化", "official", False),
    ("review_freq_per_year", 2.0, None, None, 0.60, "推定", "estimated", True),
    ("interview_rounds", 4.0, None, None, 0.70, "採用情報", "official", False),
    # ── bool ────────────────────────────────────────────────────
    ("has_flextime", 1.0, None, None, 0.75, "採用ページ", "official", False),
    ("has_coding_test", 1.0, None, None, 0.90, "研究職採用・技術課題必須", "official", False),
    ("field_engineer_joins", 1.0, None, None, 0.85, "研究者面接", "official", False),
    ("has_specialist_track", 1.0, None, None, 0.90, "研究員・エンジニア職", "official", False),
    ("has_mentor", 1.0, None, None, 0.75, "採用ページ", "official", False),
    ("has_early_route", 0.0, None, None, 0.70, "新卒採用少数精鋭", "official", False),
    ("has_data_lake", 1.0, None, None, 0.90, "大規模学習基盤あり", "official", False),
    ("has_housing_support", 0.0, None, None, 0.70, "採用ページ", "official", False),
    ("has_fixed_ot", 0.0, None, None, 0.80, "採用ページ", "official", False),
    ("placement_guaranteed", 1.0, None, None, 0.80, "研究職採用・職種明確", "official", False),
    ("salary_disclosed", 1.0, None, None, 0.80, "採用情報", "official", False),
    # ── scale5 ──────────────────────────────────────────────────
    ("f:new_biz_activeness", 4.0, None, None, 0.80, "MN-Core/PainTS/CuriousG等", "official", False),
    (
        "f:competitive_advantage",
        5.0,
        None,
        None,
        0.85,
        "特許+独自チップ+研究論文",
        "official",
        False,
    ),
    ("f:tech_barrier", 5.0, None, None, 0.85, "MN-Core設計・深層学習特許群", "official", False),
    ("f:trend_fit", 5.0, None, None, 0.85, "AI・ロボット・創薬の中心", "official", False),
    ("f:market_growth", 5.0, None, None, 0.80, "生成AI・産業AI市場急拡大", "estimated", True),
    ("f:regulation_impact", 3.0, None, None, 0.65, "AI規制は中立〜追い風", "estimated", True),
    ("f:psychological_safety", 4.0, None, None, 0.70, "研究カルチャー・失敗許容", "review", True),
    ("f:bottom_up_degree", 4.0, None, None, 0.70, "研究テーマ裁量が大きい", "review", True),
    ("f:text_comm_culture", 3.0, None, None, 0.60, "研究者中心・口頭議論多い", "estimated", True),
    (
        "f:exec_field_distance",
        2.0,
        None,
        None,
        0.70,
        "岡野原代表が技術現場と近い",
        "official",
        False,
    ),
    ("organization_type", 5.0, None, None, 0.80, "研究開発組織が完全独立", "official", False),
    ("f:young_autonomy", 5.0, None, None, 0.80, "入社直後から研究テーマ担当", "official", False),
    (
        "f:skill_support_quality",
        5.0,
        None,
        None,
        0.85,
        "カンファレンス参加・論文発表全額支援",
        "official",
        False,
    ),
    (
        "f:evaluation_system",
        3.0,
        None,
        None,
        0.60,
        "研究成果ベース・不透明な部分も",
        "review",
        True,
    ),
    ("f:training_quality", 4.0, None, None, 0.75, "メンター制・研究指導体制", "official", False),
    (
        "f:unwanted_rotation_risk",
        1.0,
        None,
        None,
        0.85,
        "職種変更なし・専門職固定",
        "official",
        False,
    ),
    (
        "f:tech_investment_direction",
        5.0,
        None,
        None,
        0.90,
        "MN-Core開発・大規模GPU投資",
        "official",
        False,
    ),
    ("f:leave_ease", 3.0, None, None, 0.60, "研究職は締め切り次第", "estimated", True),
    ("f:reverse_q_sincerity", 4.0, None, None, 0.70, "研究者面接で率直な議論", "review", True),
    # MLOps・データ基盤（★ 最高評価 — PFN の核心）
    (
        "f:mlops_maturity",
        5.0,
        None,
        None,
        0.90,
        "MN-Core+Kubernetes+独自学習基盤",
        "official",
        False,
    ),
    (
        "f:data_platform_maturity",
        5.0,
        None,
        None,
        0.90,
        "ペタバイト規模学習データ基盤",
        "official",
        False,
    ),
    ("f:tech_debt_culture", 4.0, None, None, 0.75, "研究コード品質・段階的改善", "review", True),
    (
        "f:dev_process_maturity",
        3.0,
        None,
        None,
        0.65,
        "研究駆動で開発プロセス流動的",
        "estimated",
        True,
    ),
    (
        "f:dev_experience_quality",
        5.0,
        None,
        None,
        0.85,
        "最高スペックGPUサーバ・環境整備",
        "official",
        False,
    ),
    # ハード×ソフト連携（V5スキーマ検証用）
    (
        "f:hw_sw_integration",
        5.0,
        None,
        None,
        0.95,
        "MN-Core AIチップ設計+SW共同開発",
        "official",
        False,
    ),
    (
        "f:global_expansion",
        2.0,
        None,
        None,
        0.70,
        "海外学会発表多いが開発は国内中心",
        "official",
        False,
    ),
    ("stability", 3.0, None, None, 0.65, "黒字化は未達・出資基盤は厚い", "estimated", True),
    ("diversity", 3.0, None, None, 0.65, "外国籍研究者多いが全体比率中程度", "review", True),
    ("oss_blog_freq", 3.0, None, None, 0.75, "tech blog + 論文OSS公開", "official", False),
    # 特許件数（非 SaaS スキーマ経路の検証用）
    ("patent_count", 200.0, None, None, 0.80, "J-PlatPat・有報", "official", False),
    # 採用
    ("has_hw_product", None, None, None, 0.0, "", "estimated", True),  # スキップ（feature_key外）
]


def _get_or_create_company() -> str:
    """official_url で検索して既存 ID を返す。なければ INSERT して新 ID を返す。"""
    with get_session() as session:
        row = session.execute(
            sa.text("SELECT id FROM company WHERE official_url = :url"),
            {"url": COMPANY_URL},
        ).fetchone()
        if row:
            log.info("company 既存: %s", row[0])
            return str(row[0])

        new_id = uuid.uuid4()
        session.execute(
            sa.text(
                "INSERT INTO company"
                " (id, name, official_url, hq_prefecture, estimated_category,"
                "  tech_stack, hiring_roles, llm_confidence, release_flag,"
                "  has_relocation, engineer_count, listing_type, founded_year, target_market)"
                " VALUES (:id, :name, :url, :pref, :cat,"
                "         '[]'::jsonb, '[]'::jsonb, 'manual', false,"
                "         :rel, :eng, :lst, :yr, :mkt)"
            ),
            {
                "id": str(new_id),
                "name": COMPANY_NAME,
                "url": COMPANY_URL,
                "pref": "東京都",
                "cat": "AI・機械学習専業",
                "rel": False,
                "eng": 350,
                "lst": "未上場",
                "yr": 2014,
                "mkt": "BtoB",
            },
        )
        log.info("company 新規 INSERT: %s (%s)", COMPANY_NAME, new_id)
        return str(new_id)


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
                    " VALUES (gen_random_uuid(), :cid, :tid, NULL, :src, 'official', 0.90)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"cid": cid, "tid": tid, "src": "採用ページ・技術ブログ・論文"},
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


def _insert_revenue_records(cid: str) -> None:
    with get_session() as session:
        for rec in REVENUE_RECORDS:
            session.execute(
                sa.text(
                    "INSERT INTO revenue_record"
                    " (id, company_id, year, revenue, operating_profit, is_profitable)"
                    " VALUES (gen_random_uuid(), :cid, :year, :revenue, :operating_profit, :is_profitable)"
                    " ON CONFLICT DO NOTHING"
                ),
                {"cid": cid, **rec},
            )
    log.info("revenue_record: %d 件処理", len(REVENUE_RECORDS))


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


def run() -> None:
    cid = _get_or_create_company()
    _insert_tech_tags(cid)
    _insert_salary_records(cid)
    _insert_revenue_records(cid)

    written = _insert_features(cid)
    log.info("company_feature: %d 件投入", written)

    normalize_all(uuid.UUID(cid))
    log.info("PFN: 投入・正規化完了 (company_id=%s)", cid)


if __name__ == "__main__":
    run()
