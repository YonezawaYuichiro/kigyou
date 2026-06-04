"""対照ペルソナ: リモート最優先・SaaS志向（エンジンテスト用 — freee が1位になるべきペルソナ）

事前宣言する期待順位（DBの実値から逆算）:
  freee > サイボウズ > PFN

根拠:
  remote_rate:    freee 90%(0.90) > サイボウズ 85%(0.85) > PFN 75%(0.75)
  oss_blog_freq:  freee 8件/月(0.16) = サイボウズ 8件/月(0.16) ※同値
  f:data_platform: freee 4/5(0.75) = サイボウズ 4/5(0.75) ※同値
  f:tech_debt:    freee 4/5(0.75) = サイボウズ 4/5(0.75) ※同値
  overtime_hours: サイボウズ 12h(0.85) > freee 15h(0.81) > PFN 25h(0.69)

freee を1位にする鍵: remote_rate に最高 weight を置き、残業・安定・研究開発系の weight を低くする。
remote 一点で freee がサイボウズを上回り、PFN は残業・安定で下位になる。

注意: oss_blog_freq は freee・サイボウズが同値なので差がつかない。
      remote_rate(1.5) の差分（0.05）が勝負どころ。接戦になる可能性がある。

実行: python -m backend.seed.phase3_remote_saas
"""

import json
import logging
import uuid

import sqlalchemy as sa

from backend.database import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

SESSION_ID = "phase3_remote_saas"

# ============================================================
# 期待順位（実行前宣言 — 変更禁止）
# ============================================================
# 1位: freee       (remote 90% = 3社中最高、WLB良好)
# 2位: サイボウズ  (remote 85%・残業12hで接戦)
# 3位: PFN         (remote 75%・残業25h・研究職カルチャーで不利)

# ============================================================
# 希望条件
# (feature_key, desired_value, desired_min, desired_max,
#  desired_tags, weight, is_hard_filter)
# ============================================================
PREFERENCES: list[tuple] = [
    # ===== ハードフィルタ =====
    # リモート 70% 以上（freee90%・サイボウズ85%→通過、PFN75%→通過だが低め）
    ("remote_rate", None, 70.0, None, None, 1.5, True),
    # ===== リモート・フレキシブル（最高 weight）=====
    ("has_flextime", 1.0, None, None, None, 1.3, False),
    ("f:leave_ease", None, 4.0, None, None, 1.2, False),
    ("paid_leave_usage_pct", None, 60.0, None, None, 1.0, False),
    # ===== SaaS・プロダクト系（中 weight）=====
    ("oss_blog_freq", None, 3.0, None, None, 1.2, False),  # freee と サイボウズ同値
    ("f:data_platform_maturity", None, 3.0, None, None, 1.0, False),
    ("f:tech_debt_culture", None, 3.0, None, None, 1.0, False),
    ("has_data_lake", 1.0, None, None, None, 0.8, False),
    ("f:dev_process_maturity", None, 3.0, None, None, 0.8, False),
    # ===== 採用・基本 =====
    ("has_coding_test", 1.0, None, None, None, 0.8, False),
    ("field_engineer_joins", 1.0, None, None, None, 0.7, False),
    ("salary_disclosed", 1.0, None, None, None, 0.6, False),
    # ===== 給与（最低ライン）=====
    ("salary_age30", None, 5_000_000.0, None, None, 0.6, False),
    # ===== 人事・評価 =====
    ("has_specialist_track", 1.0, None, None, None, 0.7, False),
    ("f:evaluation_system", None, 3.0, None, None, 0.6, False),
    ("f:young_autonomy", None, 3.0, None, None, 0.7, False),
    # ===== WLB（中 weight）=====
    ("annual_holiday_days", None, 120.0, None, None, 0.7, False),
    ("overtime_hours", None, None, 25.0, None, 0.7, False),  # freee15h・PFN25h で差がつく
    # ===== 研究・ML（低 weight — SaaS志向なので重視しない）=====
    ("f:mlops_maturity", None, 2.0, None, None, 0.3, False),
    ("rnd_ratio", None, 3.0, None, None, 0.2, False),
    # ===== 低優先 =====
    ("has_housing_support", None, None, None, None, 0.2, False),
    ("diversity", None, None, None, None, 0.2, False),
    ("stability", None, None, None, None, 0.3, False),
]

# ペルソナ属性
PROFILE = {
    "required_skills": ["TypeScript", "React", "Go", "バックエンド開発"],
    "target_roles": [
        "フロントエンドエンジニア",
        "バックエンドエンジニア",
        "フルスタックエンジニア",
    ],
    "qualifications": [],
}


def run() -> None:
    with get_session() as session:
        user_id = _upsert_user_profile(session)
        log.info("UserProfile id = %s", user_id)
        _upsert_preferences(session, user_id)
        log.info("リモートSaaSペルソナ投入完了: %d 件", len(PREFERENCES))


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
