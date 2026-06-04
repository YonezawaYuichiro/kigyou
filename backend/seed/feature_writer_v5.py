"""V5 company_feature への書き込みユーティリティ。

dimensions_extractor / deep_dive から呼ばれる。
Haiku/Gemini の抽出結果を feature_key EAV 形式に変換して upsert する。

設計原則:
  - V2 テーブル（CompanyDimensions）への書き込みは既存コードが担う。このモジュールは V5 への「追加書き込み」のみ。
  - 全値は source_type='estimated', is_estimated=True で保存（自動抽出は推定扱い）。
  - source_type が 'official' または 'review' の既存行は上書きしない（手入力を保護）。
  - has_official_actual=True の項目（overtime_hours 等）は slot を分けて同一行に保存。
"""

import logging
import uuid
from datetime import date

import sqlalchemy as sa

from backend.database import get_session

log = logging.getLogger(__name__)


# ============================================================
# マッピング定義
# ============================================================

# Haiku の star dict → (feature_key, 変換タイプ)
# 変換タイプ:
#   scale5_direct : 0-5 値をそのまま value_numeric（Haiku が 0-5 で出力）
#   pct_to_scale5 : 0-1 割合を 1 + v*4 で 1-5 に変換（Haiku が 0-1 で出力）
#   pct_to_pct100 : 0-1 割合を v*100 で % に変換
#   bool_to_num   : True→1.0 / False→0.0（bool feature_key 用）
#   bool_to_scale5: True→5.0 / False→1.0（scale5 feature_key への bool 変換）
#   interviewer   : "current_engineer"→1.0 / else→0.0

_STAR_MAP: dict[str, tuple[str, str]] = {
    # scale5_direct（Haiku が 0-5 スケールで出力）
    "psychological_safety_score": ("f:psychological_safety", "scale5_direct"),
    "junior_authority_score": ("f:young_autonomy", "scale5_direct"),
    "cicd_maturity_score": ("f:mlops_maturity", "scale5_direct"),  # CI/CD=最近接
    "data_platform_score": ("f:data_platform_maturity", "scale5_direct"),
    "tech_debt_culture_score": ("f:tech_debt_culture", "scale5_direct"),
    # pct_to_scale5（Haiku が 0-1 で出力、V5 master は 1-5）
    "new_biz_policy_score": ("f:new_biz_activeness", "pct_to_scale5"),
    "competitive_advantage_score": ("f:competitive_advantage", "pct_to_scale5"),
    "megatrend_score": ("f:trend_fit", "pct_to_scale5"),
    "evaluation_score": ("f:evaluation_system", "pct_to_scale5"),
    "skill_support_score": ("f:skill_support_quality", "pct_to_scale5"),
    # pct_to_pct100（V5 master の unit='%', value_max=30/20）
    "rd_ratio": ("rnd_ratio", "pct_to_pct100"),
    "capex_ratio": ("capex_ratio", "pct_to_pct100"),
    # bool 系
    "has_coding_test": ("has_coding_test", "bool_to_num"),
    "career_track_diversity": ("has_specialist_track", "bool_to_num"),
    "hw_sw_integration": ("f:hw_sw_integration", "bool_to_scale5"),  # scale5 型
    "interviewer_type": ("field_engineer_joins", "interviewer"),
    # 非対応（スキップ理由）:
    #   has_patent → V5 は patent_count(direct) を使うが Haiku は bool を返す。count に変換不可。
    #   tech_modernity_score, infra_cloud_score → V5 master に対応 feature_key なし。
}

# circle dict → (feature_key, slot, 変換タイプ)
# slot: value_numeric | value_official | value_actual
_CIRCLE_MAP: dict[str, tuple[str, str, str]] = {
    "avg_overtime_actual": ("overtime_hours", "value_actual", "direct"),
    "deemed_overtime_hours": ("overtime_hours", "value_official", "direct"),
    "paid_leave_rate": ("paid_leave_usage_pct", "value_actual", "pct_to_pct100"),
    "remote_actual_rate": ("remote_rate", "value_actual", "pct_to_pct100"),
    "avg_tenure_years": ("avg_tenure", "value_numeric", "direct"),
    "selection_steps": ("interview_rounds", "value_numeric", "direct"),
    "intern_fast_track": ("has_early_route", "value_numeric", "bool_to_num"),
    "flex_system": ("has_flextime", "value_numeric", "bool_to_num"),
}


# ============================================================
# 変換ロジック
# ============================================================


def _convert(raw: object, vtype: str) -> float | None:
    """Haiku の生値を feature_definition の単位・スケールに変換する。"""
    if raw is None:
        return None
    try:
        if vtype == "scale5_direct":
            return float(raw)
        if vtype == "pct_to_scale5":
            return round(1.0 + float(raw) * 4.0, 2)
        if vtype == "pct_to_pct100":
            return round(float(raw) * 100.0, 2)
        if vtype == "bool_to_num":
            return 1.0 if raw else 0.0
        if vtype == "bool_to_scale5":
            return 5.0 if raw else 1.0
        if vtype == "interviewer":
            return 1.0 if str(raw) == "current_engineer" else 0.0
        if vtype == "direct":
            return float(raw)
    except (TypeError, ValueError):
        return None
    return None


# ============================================================
# DB upsert（1行単位）
# ============================================================


def _upsert_feature(
    session: object,
    company_id: str,
    feature_key: str,
    *,
    value_numeric: float | None = None,
    value_official: float | None = None,
    value_actual: float | None = None,
    confidence: float = 0.6,
    source: str = "auto_extraction",
    as_of: date | None = None,
) -> bool:
    """company_feature を upsert する（role_id=NULL 全社値）。

    既存行が source_type='official' または 'review' の場合は上書きしない（手入力保護）。
    実際に書き込んだ場合 True、スキップした場合 False を返す。
    """
    today = as_of or date.today()

    existing = session.execute(  # type: ignore[union-attr]
        sa.text(
            "SELECT id, source_type FROM company_feature"
            " WHERE company_id = :cid AND role_id IS NULL AND feature_key = :fkey"
        ),
        {"cid": company_id, "fkey": feature_key},
    ).fetchone()

    if existing:
        # 手入力（official/review）は保護: 自動抽出で上書きしない
        if existing.source_type in ("official", "review"):
            log.debug("skip %s: existing source_type=%s", feature_key, existing.source_type)
            return False

        # 既存 estimated 行を部分的に更新（NULL 値は更新しない → 他スロットを保持）
        updates: list[str] = []
        params: dict = {"id": str(existing.id)}
        if value_numeric is not None:
            updates.append("value_numeric = :vnum")
            params["vnum"] = value_numeric
        if value_official is not None:
            updates.append("value_official = :voff")
            params["voff"] = value_official
        if value_actual is not None:
            updates.append("value_actual = :vact")
            params["vact"] = value_actual
        if not updates:
            return False
        updates += [
            "confidence = :conf",
            "source = :src",
            "source_type = 'estimated'",
            "is_estimated = true",
            "updated_at = now()",
        ]
        params.update({"conf": confidence, "src": source})
        session.execute(  # type: ignore[union-attr]
            sa.text(f"UPDATE company_feature SET {', '.join(updates)} WHERE id = :id"),  # noqa: S608
            params,
        )
    else:
        session.execute(  # type: ignore[union-attr]
            sa.text(
                "INSERT INTO company_feature"
                " (id, company_id, role_id, feature_key,"
                "  value_numeric, value_official, value_actual,"
                "  confidence, source, source_type, as_of_date, is_estimated)"
                " VALUES (gen_random_uuid(), :cid, NULL, :fkey,"
                "         :vnum, :voff, :vact,"
                "         :conf, :src, 'estimated', :asof, true)"
            ),
            {
                "cid": company_id,
                "fkey": feature_key,
                "vnum": value_numeric,
                "voff": value_official,
                "vact": value_actual,
                "conf": confidence,
                "src": source,
                "asof": today,
            },
        )
    return True


# ============================================================
# 公開 API
# ============================================================


def write_from_star(
    company_id: str | uuid.UUID,
    star: dict,
    confidence: float,
    source: str = "gemini+haiku",
) -> int:
    """Haiku の star dict を company_feature に書き込む。書き込み件数を返す。"""
    cid = str(company_id)
    written = 0
    with get_session() as session:
        for haiku_key, (fkey, vtype) in _STAR_MAP.items():
            raw = star.get(haiku_key)
            if raw is None:
                continue
            value = _convert(raw, vtype)
            if value is None:
                continue
            try:
                if _upsert_feature(
                    session,
                    cid,
                    fkey,
                    value_numeric=value,
                    confidence=confidence,
                    source=source,
                ):
                    written += 1
            except Exception as e:
                log.warning("star write failed [%s -> %s]: %s", haiku_key, fkey, e)
    log.debug("write_from_star: %d 件書き込み", written)
    return written


def write_from_circle(
    company_id: str | uuid.UUID,
    circle: dict,
    confidence: float,
    source: str = "gemini+haiku",
) -> int:
    """Haiku の circle dict を company_feature に書き込む。書き込み件数を返す。"""
    cid = str(company_id)

    # feature_key ごとにスロットをまとめる（overtime_hours は official+actual を同一行に）
    pending: dict[str, dict[str, float | None]] = {}
    for haiku_key, (fkey, slot, vtype) in _CIRCLE_MAP.items():
        raw = circle.get(haiku_key)
        if raw is None:
            continue
        value = _convert(raw, vtype)
        if value is None:
            continue
        if fkey not in pending:
            pending[fkey] = {"value_numeric": None, "value_official": None, "value_actual": None}
        pending[fkey][slot] = value

    written = 0
    with get_session() as session:
        for fkey, slots in pending.items():
            try:
                if _upsert_feature(
                    session,
                    cid,
                    fkey,
                    value_numeric=slots["value_numeric"],
                    value_official=slots["value_official"],
                    value_actual=slots["value_actual"],
                    confidence=confidence,
                    source=source,
                ):
                    written += 1
            except Exception as e:
                log.warning("circle write failed [%s]: %s", fkey, e)

    log.debug("write_from_circle: %d 件書き込み", written)
    return written


def write_github_stats(
    company_id: str | uuid.UUID,
    github_score_0to1: float,
    source: str = "github_api",
) -> None:
    """GitHub 解析スコア（0-1）を company_feature に書き込む（0-100 に変換して保存）。"""
    cid = str(company_id)
    score_100 = round(github_score_0to1 * 100.0, 1)
    with get_session() as session:
        _upsert_feature(
            session,
            cid,
            "github_activity_score",
            value_numeric=score_100,
            confidence=0.85,
            source=source,
        )
    log.debug("github_activity_score: %.1f", score_100)


def write_blog_stats(
    company_id: str | uuid.UUID,
    post_freq_per_month: float,
    source: str = "blog_analysis",
) -> None:
    """ブログ更新頻度（件/月）を company_feature に書き込む。"""
    cid = str(company_id)
    with get_session() as session:
        _upsert_feature(
            session,
            cid,
            "oss_blog_freq",
            value_numeric=round(post_freq_per_month, 1),
            confidence=0.8,
            source=source,
        )
    log.debug("oss_blog_freq: %.1f 件/月", post_freq_per_month)
