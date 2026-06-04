"""対照ペルソナ: 安定志向（エンジンテスト用）

事前宣言する期待順位（DBの実値から逆算）:
  サイボウズ > freee > PFN

根拠:
  turnover_3yr: サイボウズ 10%(0.83) > freee 20%(0.67) > PFN 25%(0.58)  [low_good]
  stability:    サイボウズ 4/5(0.75) = freee 4/5(0.75) > PFN 3/5(0.50)
  avg_tenure:   サイボウズ 8年(0.29) > freee 4.2年(0.13) > PFN 3.5年(0.10)
  salary_age30: PFN 900万(0.5+) > freee 750万(0.47) ≈ サイボウズ 720万(0.47)
                (給与はPFNが高いが安定性ペルソナではweightが低め)

注意: listing_type は feature_key でないため直接 weight 付けできない。
      stability (scale5) と turnover_3yr で間接的に安定性を評価する。
      → サイボウズ(東証プライム) の安定性は stability=4 に反映済み。

実行: python -m backend.seed.phase3_stable
"""

import json
import logging
import uuid

import sqlalchemy as sa

from backend.database import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SESSION_ID = "phase3_stable"

# ============================================================
# 期待順位（実行前宣言 — 変更禁止）
# ============================================================
# 1位: サイボウズ (turnover 10%・stability 4・avg_tenure 8年)
# 2位: freee       (turnover 20%・stability 4・avg_tenure 4.2年)
# 3位: PFN         (turnover 25%・stability 3・未上場・赤字フェーズ)

# ============================================================
# 希望条件
# (feature_key, desired_value, desired_min, desired_max,
#  desired_tags, weight, is_hard_filter)
# ============================================================
PREFERENCES: list[tuple] = [
    # ===== ハードフィルタ =====
    # 残業 30h 以内（全社通過だが PFN は余裕なし）
    ("overtime_hours", None, None, 30.0, None, 1.0, True),
    # ===== 安定性（高 weight）=====
    # 3年離職率 15% 以下（サイボウズ10%○、freee20%△、PFN25%△）
    ("turnover_3yr", None, None, 15.0, None, 1.5, False),
    ("stability", None, 3.0, None, None, 1.3, False),
    ("avg_tenure", None, 5.0, None, None, 1.0, False),
    # ===== 給与・待遇 =====
    ("salary_age30", None, 6_000_000.0, None, None, 1.0, False),
    ("starting_salary_master", None, 200_000.0, None, None, 0.7, False),
    # ===== 人事・評価 =====
    ("has_specialist_track", 1.0, None, None, None, 0.8, False),
    ("f:evaluation_system", None, 3.0, None, None, 0.8, False),
    ("f:unwanted_rotation_risk", None, None, 3.0, None, 0.9, False),
    ("f:training_quality", None, 3.0, None, None, 0.7, False),
    ("has_mentor", 1.0, None, None, None, 0.6, False),
    # ===== WLB（中 weight）=====
    ("annual_holiday_days", None, 120.0, None, None, 0.8, False),
    ("remote_rate", None, 40.0, None, None, 0.7, False),
    ("paid_leave_usage_pct", None, 60.0, None, None, 0.7, False),
    ("has_flextime", 1.0, None, None, None, 0.6, False),
    # ===== 採用チェック =====
    ("has_coding_test", 1.0, None, None, None, 0.7, False),
    ("field_engineer_joins", 1.0, None, None, None, 0.7, False),
    ("salary_disclosed", 1.0, None, None, None, 0.6, False),
    # ===== カルチャー =====
    ("f:psychological_safety", None, 3.0, None, None, 0.6, False),
    # ===== 技術系（低 weight — 安定志向なので重視しない）=====
    ("f:mlops_maturity", None, 2.0, None, None, 0.2, False),
    ("rnd_ratio", None, 3.0, None, None, 0.2, False),
    ("has_data_lake", 1.0, None, None, None, 0.2, False),
    # ===== 低優先 =====
    ("has_housing_support", None, None, None, None, 0.3, False),
    ("diversity", None, None, None, None, 0.2, False),
]

# ペルソナ属性
PROFILE = {
    "required_skills": ["Python", "Java", "バックエンド開発"],
    "target_roles": ["バックエンドエンジニア", "インフラエンジニア"],
    "qualifications": ["基本情報技術者"],
}


def run() -> None:
    with get_session() as session:
        user_id = _upsert_user_profile(session)
        log.info("UserProfile id = %s", user_id)
        _upsert_preferences(session, user_id)
        log.info("安定志向ペルソナ投入完了: %d 件", len(PREFERENCES))


def _upsert_user_profile(session) -> uuid.UUID:
    row = session.execute(
        sa.text("SELECT id FROM user_profile WHERE session_id = :sid"),
        {"sid": SESSION_ID},
    ).fetchone()

    ts = json.dumps(PROFILE["required_skills"], ensure_ascii=False)
    tr = json.dumps(PROFILE["target_roles"], ensure_ascii=False)
    qual = json.dumps(PROFILE["qualifications"], ensure_ascii=False)

    if row is None:
        uid = uuid.uuid4()
        session.execute(
            sa.text(
                "INSERT INTO user_profile (id, session_id, tech_skills, target_roles, qualifications)"
                " VALUES (:id, :sid, :ts, :tr, :qual)"
            ),
            {"id": str(uid), "sid": SESSION_ID, "ts": ts, "tr": tr, "qual": qual},
        )
        log.info("UserProfile 新規作成: %s", SESSION_ID)
        return uid
    else:
        uid = row[0]
        session.execute(
            sa.text(
                "UPDATE user_profile SET tech_skills=:ts, target_roles=:tr, qualifications=:qual"
                " WHERE id=:id"
            ),
            {"id": str(uid), "ts": ts, "tr": tr, "qual": qual},
        )
        return uid


def _upsert_preferences(session, user_id: uuid.UUID) -> None:
    count = 0
    for fkey, dv, dmin, dmax, dtags, weight, hard in PREFERENCES:
        exists = session.execute(
            sa.text("SELECT 1 FROM feature_definition WHERE feature_key = :fkey"),
            {"fkey": fkey},
        ).fetchone()
        if exists is None:
            log.warning("feature_definition に '%s' なし → スキップ", fkey)
            continue

        dtags_json = json.dumps(dtags) if dtags is not None else None
        session.execute(
            sa.text(
                "INSERT INTO user_preference"
                " (id, user_profile_id, feature_key,"
                "  desired_value, desired_min, desired_max, desired_tags, weight, is_hard_filter)"
                " VALUES (:id, :uid, :fkey, :dv, :dmin, :dmax, :dtags, :w, :hf)"
                " ON CONFLICT (user_profile_id, feature_key) DO UPDATE SET"
                "  desired_value=EXCLUDED.desired_value, desired_min=EXCLUDED.desired_min,"
                "  desired_max=EXCLUDED.desired_max, desired_tags=EXCLUDED.desired_tags,"
                "  weight=EXCLUDED.weight, is_hard_filter=EXCLUDED.is_hard_filter"
            ),
            {
                "id": str(uuid.uuid4()),
                "uid": str(user_id),
                "fkey": fkey,
                "dv": dv,
                "dmin": dmin,
                "dmax": dmax,
                "dtags": dtags_json,
                "w": weight,
                "hf": hard,
            },
        )
        count += 1
    log.info("user_preference: %d 件投入", count)


if __name__ == "__main__":
    run()
