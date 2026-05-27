"""プロフィール設定ウィザード（V2）。

3ステップでユーザーの実力・条件を入力し DB に保存する。
  Step 1: スキル・経験入力 → Sonnet 4.6 で tech_level_score を算出
  Step 2: 条件設定（絶対条件 / 希望条件）
  Step 3: 確認・保存
"""

import uuid

import streamlit as st

from backend.api.profile_manager import load_or_create_profile, save_profile

st.set_page_config(page_title="プロフィール設定 | GradMatch-AI", page_icon="👤", layout="centered")
st.title("👤 プロフィール設定")
st.caption("あなたのスキル・希望条件を登録してマッチング精度を上げましょう。")

# セッションIDをブラウザセッション中に固定する
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
session_id: str = st.session_state["session_id"]

# 既存プロフィールをロード（my_profile.json からの fallback import も含む）
if "profile_loaded" not in st.session_state:
    with st.spinner("プロフィールを読み込み中..."):
        st.session_state["db_profile"] = load_or_create_profile(session_id)
    st.session_state["profile_loaded"] = True

profile = st.session_state["db_profile"]

# ウィザードのステップ管理
if "wizard_step" not in st.session_state:
    st.session_state["wizard_step"] = 1

step = st.session_state["wizard_step"]
st.progress(step / 3, text=f"ステップ {step} / 3")

# ─── Step 1: スキル・経験入力 ─────────────────────────────────────────────
if step == 1:
    st.subheader("Step 1: スキル・開発経験")

    tech_skills_raw = st.text_area(
        "保有スキル（カンマ区切り）",
        value=", ".join(profile.tech_skills or []),
        help="例: Python, AWS, PyTorch, Docker",
    )
    qualifications_raw = st.text_area(
        "保有資格（カンマ区切り）",
        value=", ".join(profile.qualifications or []),
        help="例: 応用情報技術者, G検定, AWS認定",
    )
    project_exp = st.text_area(
        "個人開発・インターン経験（自由記述）",
        value=profile.project_experience or "",
        height=200,
        placeholder=(
            "例:\n"
            "・Djangoでポートフォリオサイトを個人開発（AWS EC2 + RDS 構成）\n"
            "・3ヶ月の長期インターンでMLパイプラインのCI/CD整備を担当\n"
            "・PyTorchで画像分類モデルを実装、Edge AIデバイスへデプロイ"
        ),
    )

    if st.button("次へ →", type="primary"):
        st.session_state["w1_tech_skills"] = [
            s.strip() for s in tech_skills_raw.split(",") if s.strip()
        ]
        st.session_state["w1_qualifications"] = [
            s.strip() for s in qualifications_raw.split(",") if s.strip()
        ]
        st.session_state["w1_project_exp"] = project_exp
        st.session_state["wizard_step"] = 2
        st.rerun()

# ─── Step 2: 条件設定 ────────────────────────────────────────────────────
elif step == 2:
    st.subheader("Step 2: 就職条件")

    hard = profile.hard_constraints or {}
    soft = profile.soft_preferences or {}

    st.markdown("**絶対条件（これを外れる企業は除外）**")
    max_ot = st.slider(
        "残業上限（時間/月）",
        0,
        80,
        int(hard.get("max_overtime_hours", 30)),
    )
    remote_opts = st.multiselect(
        "リモートワーク",
        ["full", "partial", "none"],
        default=hard.get("remote_work", ["full", "partial"]),
        format_func=lambda x: {
            "full": "フルリモート",
            "partial": "一部リモート",
            "none": "出社のみ",
        }[x],
    )
    prefs_raw = st.text_input(
        "勤務地（カンマ区切り、空欄=不問）",
        value=", ".join(hard.get("preferred_prefectures", [])),
        help="例: 大阪府, 京都府, 兵庫県",
    )

    st.divider()
    st.markdown("**希望条件（重み付けに反映）**")
    priority = st.radio(
        "最も重視すること",
        ["career_growth", "wlb", "salary", "balanced"],
        format_func=lambda x: {
            "career_growth": "キャリア成長・技術力向上",
            "wlb": "ワークライフバランス",
            "salary": "給与・待遇",
            "balanced": "バランス重視",
        }[x],
        index=["career_growth", "wlb", "salary", "balanced"].index(
            soft.get("priority", "balanced")
        ),
        horizontal=True,
    )

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button("← 戻る"):
            st.session_state["wizard_step"] = 1
            st.rerun()
    with col_next:
        if st.button("次へ →", type="primary"):
            prefectures = [s.strip() for s in prefs_raw.split(",") if s.strip()]
            st.session_state["w2_hard"] = {
                "max_overtime_hours": max_ot,
                "remote_work": remote_opts,
                "preferred_prefectures": prefectures,
            }
            st.session_state["w2_soft"] = {"priority": priority}
            st.session_state["wizard_step"] = 3
            st.rerun()

# ─── Step 3: 確認・保存 ──────────────────────────────────────────────────
elif step == 3:
    st.subheader("Step 3: 確認・保存")

    skills = st.session_state.get("w1_tech_skills", [])
    quals = st.session_state.get("w1_qualifications", [])
    exp = st.session_state.get("w1_project_exp", "")
    hard = st.session_state.get("w2_hard", {})
    soft = st.session_state.get("w2_soft", {})

    st.markdown(f"**スキル**: {', '.join(skills) or '未入力'}")
    st.markdown(f"**資格**: {', '.join(quals) or 'なし'}")
    st.markdown(f"**残業上限**: {hard.get('max_overtime_hours')} h/月")
    st.markdown(f"**リモート**: {', '.join(hard.get('remote_work', []))}")
    st.markdown(f"**勤務地**: {', '.join(hard.get('preferred_prefectures', [])) or '不問'}")
    st.markdown(f"**優先事項**: {soft.get('priority', 'balanced')}")

    st.info("「保存して分析」を押すと Sonnet 4.6 で実務力スコアを算出します（数秒かかります）。")

    col_back, col_save = st.columns(2)
    with col_back:
        if st.button("← 戻る"):
            st.session_state["wizard_step"] = 2
            st.rerun()
    with col_save:
        if st.button("💾 保存してマッチング", type="primary"):
            with st.spinner("Sonnet 4.6 で実務力を評価中..."):
                saved = save_profile(
                    session_id=session_id,
                    tech_skills=skills,
                    qualifications=quals,
                    project_experience=exp,
                    architecture_experience=[],
                    hard_constraints=hard,
                    soft_preferences=soft,
                    recompute_level=True,
                )
            st.session_state["db_profile"] = saved
            st.session_state["profile_loaded"] = True
            # マッチング結果キャッシュをリセット
            for key in list(st.session_state.keys()):
                if key.startswith("v2_matches"):
                    del st.session_state[key]

            st.success(
                f"保存完了！ 実務力スコア: **{saved.tech_level_score:.2f}** / 1.0\n\n"
                f"> {saved.tech_level_rationale}"
            )
            st.caption("トップページに戻ってV2マッチングタブを確認してください。")
            st.session_state["wizard_step"] = 1  # 次回のためにリセット
