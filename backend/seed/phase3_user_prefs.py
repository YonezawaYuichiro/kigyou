"""Phase 3: ユーザー希望条件 (user_preference) 入力スクリプト

data/my_profile.json を読み込み、feature_key 体系で user_preference テーブルに投入する。

マッピング方針:
  my_profile.json → user_preference
  ─────────────────────────────────────────
  hard_filters.max_overtime_hours → overtime_hours  (is_hard_filter=True, desired_max=30)
  hard_filters.remote_work        → remote_rate     (is_hard_filter=True, desired_min=50)
  weights.tech_growth (0.356)     → 開発環境系 feature の weight boost (×1.5)
  weights.wlb (0.254)             → WLB系 feature の weight boost (×1.2)
  weights.self_developed (0.254)  → 自社開発系 feature の weight boost (×1.3)
  required_skills                 → UserProfile.tech_skills に保存
  target_roles                    → UserProfile.target_roles に保存

実行: python -m backend.seed.phase3_user_prefs
"""

import json
import logging
import uuid
from pathlib import Path

import sqlalchemy as sa

from backend.database import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

PROFILE_PATH = Path(__file__).parents[2] / "data" / "my_profile.json"
SESSION_ID = "phase3_yuichiro"  # UserProfileの識別子

# ============================================================
# 希望条件定義
# (feature_key, desired_value, desired_min, desired_max,
#  desired_tags, weight, is_hard_filter)
#
# weight=None → feature_definition.default_weight を継承
# ============================================================
PREFERENCES: list[tuple] = [
    # ===== ハードフィルタ（必須条件）=====
    # 月平均残業30h以内（実態ベース）
    ("overtime_hours", None, None, 30.0, None, 1.5, True),
    # リモート実施率50%以上
    ("remote_rate", None, 50.0, None, None, 1.5, True),
    # ===== 10. 開発環境（tech_growth 最高優先度: weight×1.5）=====
    # desired_min: scale5 で「X 以上なら良い」→ 高いほど連続的に高得点（Goldilocks ペナルティなし）
    ("f:mlops_maturity", None, 4.0, None, None, 1.5, False),
    ("f:data_platform_maturity", None, 4.0, None, None, 1.5, False),
    ("f:tech_debt_culture", None, 4.0, None, None, 1.5, False),
    ("has_data_lake", 1.0, None, None, None, 1.3, False),
    ("f:dev_process_maturity", None, 4.0, None, None, 1.2, False),
    ("f:dev_experience_quality", None, 4.0, None, None, 1.0, False),
    ("oss_blog_freq", None, 3.0, None, None, 0.8, False),  # 月3本以上
    # ===== 4. 財務（技術投資の裏付け）=====
    ("rnd_ratio", None, 8.0, None, None, 1.3, False),  # R&D8%以上
    # ===== 2. ビジョン・戦略（自社開発重視: weight×1.3）=====
    ("f:tech_investment_direction", None, 4.0, None, None, 1.3, False),
    ("f:new_biz_activeness", None, 3.0, None, None, 1.3, False),
    # ===== 3. ビジネスモデル（プロダクト型を好む）=====
    ("f:competitive_advantage", None, 3.0, None, None, 1.2, False),
    ("f:tech_barrier", None, 3.0, None, None, 1.0, False),
    # ===== 7. 人事・評価・キャリア（学習投資が重要なMLエンジニア）=====
    ("f:young_autonomy", None, 4.0, None, None, 1.2, False),
    ("f:skill_support_quality", None, 4.0, None, None, 1.3, False),
    ("has_specialist_track", 1.0, None, None, None, 1.0, False),
    ("f:training_quality", None, 3.0, None, None, 0.8, False),
    ("has_mentor", 1.0, None, None, None, 0.8, False),
    ("f:evaluation_system", None, 3.0, None, None, 0.8, False),
    # 離職率20%以下を希望（low_good: desired_max=20は「20%以下が希望」の意）
    ("turnover_3yr", None, None, 20.0, None, 0.8, False),
    # 不本意ローテーションリスク3以下（low_goodスケール）
    ("f:unwanted_rotation_risk", None, None, 3.0, None, 0.8, False),
    # ===== 6. 組織・カルチャー =====
    ("f:psychological_safety", None, 4.0, None, None, 1.0, False),
    # f:bottom_up_degree は desired_value=3 のまま（強すぎると混沌→ Goldilocks 意図あり）
    ("f:bottom_up_degree", 3.0, None, None, None, 0.8, False),
    ("f:text_comm_culture", None, 4.0, None, None, 0.8, False),
    # 経営距離は3以下を希望（low_good: 値が小さいほど良い）
    ("f:exec_field_distance", None, None, 3.0, None, 0.7, False),
    # ===== 8. 待遇・WLB（weight×1.2）=====
    ("annual_holiday_days", None, 120.0, None, None, 1.2, False),  # 120日以上
    ("f:leave_ease", None, 4.0, None, None, 1.2, False),
    ("paid_leave_usage_pct", None, 60.0, None, None, 1.0, False),  # 取得率60%以上
    ("has_flextime", 1.0, None, None, None, 1.0, False),
    # ===== 9. 採用（技術重視企業の見極め）=====
    ("has_coding_test", 1.0, None, None, None, 1.2, False),  # コーテストある=技術重視の証拠
    ("field_engineer_joins", 1.0, None, None, None, 1.0, False),
    ("salary_disclosed", 1.0, None, None, None, 0.8, False),
    ("f:reverse_q_sincerity", None, 3.0, None, None, 0.7, False),
    # ===== 給与（最低ライン）=====
    ("starting_salary_master", None, 200_000.0, None, None, 0.8, False),  # 月20万以上
    ("salary_age30", None, 5_000_000.0, None, None, 0.8, False),  # 30歳500万以上
    # ===== 低優先（weight を下げる）=====
    ("has_housing_support", None, None, None, None, 0.2, False),
    ("diversity", None, None, None, None, 0.2, False),
    ("stability", None, None, None, None, 0.3, False),
]


def run() -> None:
    profile = _load_profile()
    with get_session() as session:
        user_id = _upsert_user_profile(session, profile)
        log.info("UserProfile id = %s", user_id)

        _upsert_preferences(session, user_id)
        _print_summary(session, user_id)


def _load_profile() -> dict:
    with open(PROFILE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _upsert_user_profile(session, profile: dict) -> uuid.UUID:
    """UserProfile を session_id で取得または新規作成し、tech_skills 等を更新する。"""
    row = session.execute(
        sa.text("SELECT id FROM user_profile WHERE session_id = :sid"),
        {"sid": SESSION_ID},
    ).fetchone()

    tech_skills = json.dumps(
        profile.get("required_skills", []) + profile.get("bonus_skills", []),
        ensure_ascii=False,
    )
    target_roles = json.dumps(profile.get("target_roles", []), ensure_ascii=False)
    qualifications = json.dumps(profile.get("qualifications", []), ensure_ascii=False)

    if row is None:
        uid = uuid.uuid4()
        session.execute(
            sa.text(
                "INSERT INTO user_profile"
                " (id, session_id, tech_skills, target_roles, qualifications)"
                " VALUES (:id, :sid, :ts, :tr, :qual)"
            ),
            {
                "id": str(uid),
                "sid": SESSION_ID,
                "ts": tech_skills,
                "tr": target_roles,
                "qual": qualifications,
            },
        )
        log.info("UserProfile 新規作成: session_id=%s", SESSION_ID)
    else:
        uid = row[0]
        session.execute(
            sa.text(
                "UPDATE user_profile"
                " SET tech_skills = :ts, target_roles = :tr, qualifications = :qual"
                " WHERE id = :id"
            ),
            {"id": str(uid), "ts": tech_skills, "tr": target_roles, "qual": qualifications},
        )
        log.info("UserProfile 更新: session_id=%s", SESSION_ID)

    return uid


def _upsert_preferences(session, user_id: uuid.UUID) -> None:
    """user_preference を投入。既存行は ON CONFLICT で desired_value / weight を上書き。"""
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
                "  desired_value, desired_min, desired_max, desired_tags,"
                "  weight, is_hard_filter)"
                " VALUES"
                " (:id, :uid, :fkey,"
                "  :dv, :dmin, :dmax, :dtags,"
                "  :w, :hf)"
                " ON CONFLICT (user_profile_id, feature_key) DO UPDATE SET"
                "  desired_value  = EXCLUDED.desired_value,"
                "  desired_min    = EXCLUDED.desired_min,"
                "  desired_max    = EXCLUDED.desired_max,"
                "  desired_tags   = EXCLUDED.desired_tags,"
                "  weight         = EXCLUDED.weight,"
                "  is_hard_filter = EXCLUDED.is_hard_filter"
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

    hard_count = sum(1 for _, _, _, _, _, _, hf in PREFERENCES if hf)
    log.info("user_preference: %d 件投入（うちハードフィルタ %d 件）", count, hard_count)


def _print_summary(session, user_id: uuid.UUID) -> None:
    """投入結果サマリー。"""
    rows = session.execute(
        sa.text(
            "SELECT up.feature_key, fd.display_name, fd.default_weight,"
            "       up.desired_value, up.desired_min, up.desired_max,"
            "       up.weight, up.is_hard_filter"
            " FROM user_preference up"
            " JOIN feature_definition fd ON up.feature_key = fd.feature_key"
            " WHERE up.user_profile_id = :uid"
            " ORDER BY up.is_hard_filter DESC, up.weight DESC NULLS LAST"
        ),
        {"uid": str(user_id)},
    ).fetchall()

    log.info("=== user_preference 一覧 ===")
    for r in rows:
        hf = "【必須】" if r.is_hard_filter else "      "
        w_eff = r.weight if r.weight is not None else r.default_weight
        dv_str = (
            f"desired={r.desired_value}"
            if r.desired_value is not None
            else f"min={r.desired_min} max={r.desired_max}"
        )
        log.info(
            "  %s %-35s  w=%.1f  %s",
            hf,
            r.feature_key,
            w_eff,
            dv_str,
        )


if __name__ == "__main__":
    run()
