"""対照ペルソナ: WLB最優先（エンジンテスト用）

事前宣言する期待順位（DBの実値から逆算）:
  サイボウズ > freee > PFN

根拠:
  overtime_hours:      サイボウズ 12h(0.85) > freee 15h(0.81) > PFN 25h(0.69)
  paid_leave_usage_pct:サイボウズ 78%(0.78) > freee 70%(0.70) > PFN 60%(0.60)
  annual_holiday_days: サイボウズ 125日(0.83) > freee 124日(0.80) > PFN 120日(0.67)
  f:leave_ease:        サイボウズ 5/5(1.00) > freee 4/5(0.75) > PFN 3/5(0.50)
  remote_rate:         freee 90%(0.90) > サイボウズ 85%(0.85) > PFN 75%(0.75)
  → リモートはfreeeが上だが他4軸でサイボウズが優位

注意: freeeの方がWLBが良いというブランド印象と逆転する。
      これはDBの実値ベースの期待であり、エンジンが正しく動けばサイボウズが1位になる。

実行: python -m backend.seed.phase3_wlb
"""

import json
import logging
import uuid

import sqlalchemy as sa

from backend.database import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SESSION_ID = "phase3_wlb"

# ============================================================
# 期待順位（実行前宣言 — 変更禁止）
# ============================================================
# 1位: サイボウズ  (overtime 0.85・leave_ease 1.00・有給 0.78)
# 2位: freee       (remote 0.90 が強みだが他軸でサイボウズに負ける)
# 3位: PFN         (残業 25h・有給 60%・休日 120日 と全軸で下位)

# ============================================================
# 希望条件
# (feature_key, desired_value, desired_min, desired_max,
#  desired_tags, weight, is_hard_filter)
# ============================================================
PREFERENCES: list[tuple] = [
    # ===== ハードフィルタ =====
    # 残業 20h 以内（サイボウズ12h・freee15h → 通過、PFN25h → 通過だが低スコア）
    ("overtime_hours", None, None, 20.0, None, 1.5, True),
    # リモート 50% 以上（全社通過）
    ("remote_rate", None, 50.0, None, None, 1.5, True),
    # ===== WLB 系（高 weight）=====
    ("annual_holiday_days", None, 120.0, None, None, 1.3, False),
    ("paid_leave_usage_pct", None, 60.0, None, None, 1.3, False),
    ("f:leave_ease", None, 4.0, None, None, 1.3, False),
    ("has_flextime", 1.0, None, None, None, 1.0, False),
    ("f:unwanted_rotation_risk", None, None, 3.0, None, 0.8, False),
    # ===== 給与（最低ライン）=====
    ("salary_age30", None, 5_000_000.0, None, None, 0.7, False),
    ("starting_salary_master", None, 200_000.0, None, None, 0.5, False),
    # ===== 採用・基本チェック =====
    ("has_coding_test", 1.0, None, None, None, 0.8, False),
    ("field_engineer_joins", 1.0, None, None, None, 0.7, False),
    ("salary_disclosed", 1.0, None, None, None, 0.6, False),
    # ===== 人事・評価 =====
    ("has_specialist_track", 1.0, None, None, None, 0.7, False),
    ("has_mentor", 1.0, None, None, None, 0.6, False),
    ("f:evaluation_system", None, 3.0, None, None, 0.6, False),
    ("turnover_3yr", None, None, 20.0, None, 0.7, False),
    # ===== カルチャー（中 weight）=====
    ("f:psychological_safety", None, 3.0, None, None, 0.7, False),
    ("f:exec_field_distance", None, None, 3.0, None, 0.6, False),
    # ===== 技術系（低 weight — WLBペルソナなので重視しない）=====
    ("f:mlops_maturity", None, 2.0, None, None, 0.2, False),
    ("rnd_ratio", None, 3.0, None, None, 0.2, False),
    ("has_data_lake", 1.0, None, None, None, 0.2, False),
    # ===== 低優先 =====
    ("has_housing_support", None, None, None, None, 0.3, False),
    ("diversity", None, None, None, None, 0.2, False),
    ("stability", None, None, None, None, 0.3, False),
]

# ペルソナ属性（DB に保存）
PROFILE = {
    "required_skills": ["Python", "バックエンド開発"],
    "target_roles": ["バックエンドエンジニア", "Webエンジニア"],
    "qualifications": [],
}


def run() -> None:
    with get_session() as session:
        user_id = _upsert_user_profile(session)
        log.info("UserProfile id = %s", user_id)
        _upsert_preferences(session, user_id)
        log.info("WLBペルソナ投入完了: %d 件", len(PREFERENCES))


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
