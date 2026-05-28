"""プロフィール設定ウィザード（V3）。

4ステップでユーザーの実力・条件を入力し DB に保存する。
  Step 1: スキル・経験入力（製作物カード複数 + GitHub URL）
  Step 2: 志望軸（業界・職種・開発フェーズ・希望年収）
  Step 3: 勤務条件（絶対条件 / 希望条件）
  Step 4: 確認・保存 → Sonnet 4.6 で tech_level_score 算出
"""

import uuid

import streamlit as st

from backend.api.github_analyzer import analyze_github
from backend.api.profile_manager import load_or_create_profile, save_profile

st.set_page_config(page_title="プロフィール設定 | GradMatch-AI", page_icon="👤", layout="centered")
st.title("👤 プロフィール設定")
st.caption("あなたのスキル・希望条件を登録してマッチング精度を上げましょう。")

if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
session_id: str = st.session_state["session_id"]

if "profile_loaded" not in st.session_state:
    with st.spinner("プロフィールを読み込み中..."):
        st.session_state["db_profile"] = load_or_create_profile(session_id)
    st.session_state["profile_loaded"] = True

profile = st.session_state["db_profile"]

if "wizard_step" not in st.session_state:
    st.session_state["wizard_step"] = 0

step = st.session_state["wizard_step"]
if step > 0:
    st.progress(step / 4, text=f"ステップ {step} / 4")

_PROJ_TYPES = ["個人開発", "インターン", "業務", "研究"]
_TEAM_SIZES = ["ソロ", "2〜5人", "5人以上"]


def _safe_index(lst: list, val: str, default: int = 0) -> int:
    return lst.index(val) if val in lst else default


# ─── Step 0: 現在の設定確認 ───────────────────────────────────────────────
if step == 0:
    st.subheader("現在のプロフィール設定")

    hard = profile.hard_constraints or {}
    col1, col2 = st.columns(2)
    with col1:
        score_str = f"{profile.tech_level_score:.2f}" if profile.tech_level_score else "未算出"
        st.metric("実務力スコア", score_str)
        if profile.tech_level_rationale:
            st.caption(profile.tech_level_rationale)
        st.markdown(f"**スキル**: {', '.join(profile.tech_skills or []) or '未入力'}")
        st.markdown(f"**資格**: {', '.join(profile.qualifications or []) or 'なし'}")
        st.markdown(f"**製作物**: {len(profile.projects or [])}件")
        st.markdown(f"**志望業界**: {', '.join(profile.target_industries or []) or '未選択'}")
        st.markdown(f"**志望職種**: {', '.join(profile.target_roles or []) or '未選択'}")
    with col2:
        _prefs = hard.get("preferred_prefectures", [])
        _cats = hard.get("preferred_categories", [])
        st.markdown(f"**勤務地**: {', '.join(_prefs) or '不問'}")
        st.markdown(f"**企業カテゴリ**: {', '.join(_cats) or '全て'}")
        st.markdown(f"**残業上限**: {hard.get('max_overtime_hours', '未設定')} h/月")
        _rl = {"full": "フルリモート", "partial": "一部リモート", "none": "出社のみ"}
        _remote_str = ", ".join(_rl.get(r, r) for r in hard.get("remote_work", []))
        st.markdown(f"**リモート**: {_remote_str or '未設定'}")
        st.markdown(f"**開発フェーズ**: {profile.dev_phase_preference or '未設定'}")
        st.markdown(f"**評価制度**: {profile.eval_preference or '未設定'}")

    st.divider()
    col_edit, col_back = st.columns(2)
    with col_edit:
        if st.button("✏️ プロフィールを編集", type="primary"):
            st.session_state["wizard_step"] = 1
            st.rerun()
    with col_back:
        st.page_link("app.py", label="← マッチングに戻る")

# ─── Step 1: スキル・経験入力 ─────────────────────────────────────────────
elif step == 1:
    st.subheader("Step 1: スキル・開発経験")

    col_a, col_b = st.columns(2)
    with col_a:
        graduation_year = st.number_input(
            "卒業予定年",
            min_value=2024,
            max_value=2030,
            value=int(profile.graduation_year or 2026),
            step=1,
        )
    with col_b:
        major = st.text_input(
            "専攻・研究分野",
            value=profile.major or "",
            placeholder="例: 情報工学, データサイエンス",
        )

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
        "補足・自由記述（任意）",
        value=profile.project_experience or "",
        height=80,
        placeholder="製作物カードに書ききれない補足情報があればここへ",
    )

    # ── 製作物カード ──────────────────────────────────────────────────────
    st.divider()
    st.markdown("**製作物・ポートフォリオ**（プライベートリポジトリも記載できます）")

    if "w1_projects" not in st.session_state:
        st.session_state["w1_projects"] = list(profile.projects or [])

    projects_list: list[dict] = st.session_state["w1_projects"]

    delete_idx: int | None = None
    for i, proj in enumerate(projects_list):
        label = proj.get("name") or f"製作物 {i + 1}"
        badge = " 🤖" if proj.get("is_ai") else ""
        with st.expander(f"{i + 1}. {label}{badge}", expanded=(i == len(projects_list) - 1)):
            c1, c2 = st.columns(2)
            with c1:
                name = st.text_input("プロジェクト名", value=proj.get("name", ""), key=f"pname_{i}")
                ptype = st.selectbox(
                    "種別",
                    _PROJ_TYPES,
                    index=_safe_index(_PROJ_TYPES, proj.get("type", "個人開発")),
                    key=f"ptype_{i}",
                )
            with c2:
                team_size = st.selectbox(
                    "チーム規模",
                    _TEAM_SIZES,
                    index=_safe_index(_TEAM_SIZES, proj.get("team_size", "ソロ")),
                    key=f"pteam_{i}",
                )
                duration = st.text_input(
                    "期間",
                    value=proj.get("duration", ""),
                    placeholder="例: 3ヶ月, 1年",
                    key=f"pdur_{i}",
                )

            description = st.text_area(
                "概要",
                value=proj.get("description", ""),
                height=80,
                placeholder="何を作ったか、どんな工夫をしたかを2〜3行で",
                key=f"pdesc_{i}",
            )
            stack_raw = st.text_input(
                "技術スタック（カンマ区切り）",
                value=", ".join(proj.get("tech_stack") or []),
                placeholder="例: Python, FastAPI, PostgreSQL, React",
                key=f"pstack_{i}",
            )
            c3, c4 = st.columns([2, 1])
            with c3:
                repo_url = st.text_input(
                    "リポジトリURL（任意）",
                    value=proj.get("repo_url", "") or "",
                    placeholder="https://github.com/...",
                    key=f"prepo_{i}",
                )
            with c4:
                is_ai = st.checkbox(
                    "AI/ML開発",
                    value=bool(proj.get("is_ai", False)),
                    key=f"pai_{i}",
                )

            if st.button("🗑 この製作物を削除", key=f"del_{i}"):
                delete_idx = i

            # 現在値をリストに書き戻す
            projects_list[i] = {
                "name": name,
                "type": ptype,
                "description": description,
                "tech_stack": [t.strip() for t in stack_raw.split(",") if t.strip()],
                "is_ai": is_ai,
                "team_size": team_size,
                "duration": duration,
                "repo_url": repo_url.strip() or None,
            }

    if delete_idx is not None:
        projects_list.pop(delete_idx)
        st.rerun()

    if st.button("➕ 製作物を追加"):
        projects_list.append(
            {
                "name": "",
                "type": "個人開発",
                "description": "",
                "tech_stack": [],
                "is_ai": False,
                "team_size": "ソロ",
                "duration": "",
                "repo_url": None,
            }
        )
        st.rerun()

    # ── GitHub ────────────────────────────────────────────────────────────
    st.divider()
    st.markdown("**GitHub（任意）— 公開リポジトリを自動解析してスコア精度を上げます**")
    col_gh, col_gh_btn = st.columns([4, 1])
    with col_gh:
        github_url_input = st.text_input(
            "GitHub URL",
            value=st.session_state.get("w1_github_url", ""),
            placeholder="例: https://github.com/your-username",
            label_visibility="collapsed",
        )
    with col_gh_btn:
        analyze_btn = st.button("🔍 解析", key="gh_analyze_btn")

    if analyze_btn and github_url_input.strip():
        with st.spinner("GitHub APIでリポジトリを取得し、コード内容を Haiku で解析中..."):
            gh_result = analyze_github(github_url_input.strip())
        if gh_result.get("error"):
            st.error(gh_result["error"])
        else:
            st.session_state["w1_github_result"] = gh_result
            st.session_state["w1_github_url"] = github_url_input.strip()
            st.success(
                f"解析完了: @{gh_result['username']} — "
                f"リポジトリ {gh_result['repo_count']}件 / "
                f"言語: {', '.join(gh_result['tech_stack']) or '不明'} / "
                f"スター合計: {gh_result['total_stars']} / "
                f"直近90日push: {gh_result['commit_frequency']}回"
            )

    if "w1_github_result" in st.session_state:
        gr = st.session_state["w1_github_result"]
        c_a, c_b, c_c, c_d = st.columns(4)
        c_a.metric("複雑さスコア", f"{gr['project_complexity_score']:.2f}")
        c_b.metric("テストコード", "あり" if gr.get("has_tests") else "なし")
        c_c.metric("CI/CD", "設定あり" if gr.get("has_ci") else "なし")
        c_d.metric("AI/ML開発", "あり" if gr.get("is_ai_ml") else "なし")

        if gr.get("quality_indicators"):
            st.caption("品質指標: " + " / ".join(gr["quality_indicators"]))
        if gr.get("tech_highlights"):
            st.caption("検出技術: " + " / ".join(gr["tech_highlights"]))

        analyses = gr.get("repo_analyses", [])
        if analyses:
            with st.expander("リポジトリ詳細評価（Haiku）"):
                for a in analyses:
                    st.markdown(f"**{a.get('repo_name', '')}**")
                    st.caption(a.get("assessment", ""))
                    qi = a.get("quality_indicators", [])
                    th = a.get("tech_highlights", [])
                    if qi or th:
                        st.caption(
                            ("品質: " + ", ".join(qi) if qi else "")
                            + (" / 技術: " + ", ".join(th) if th else "")
                        )

    if st.button("次へ →", type="primary"):
        st.session_state["w1_graduation_year"] = graduation_year
        st.session_state["w1_major"] = major.strip()
        st.session_state["w1_tech_skills"] = [
            s.strip() for s in tech_skills_raw.split(",") if s.strip()
        ]
        st.session_state["w1_qualifications"] = [
            s.strip() for s in qualifications_raw.split(",") if s.strip()
        ]
        st.session_state["w1_project_exp"] = project_exp
        # w1_projects はすでに session_state に書き戻し済み
        st.session_state["wizard_step"] = 2
        st.rerun()

# ─── Step 2: 志望軸 ──────────────────────────────────────────────────────
elif step == 2:
    st.subheader("Step 2: 志望軸")

    _INDUSTRY_OPTIONS = [
        "Web/SaaS",
        "AI/機械学習",
        "フィンテック",
        "ヘルスケア/医療IT",
        "ゲーム",
        "EC/物流",
        "エンタープライズSI",
        "スタートアップ/ベンチャー",
        "通信/インフラ",
        "その他",
    ]
    _ROLE_OPTIONS = [
        "バックエンドエンジニア",
        "フロントエンドエンジニア",
        "フルスタックエンジニア",
        "インフラ/SRE/DevOps",
        "データエンジニア",
        "MLエンジニア/AIエンジニア",
        "モバイルエンジニア",
        "エンベデッド/組み込み",
        "プロダクトマネージャー",
        "その他",
    ]

    target_industries = st.multiselect(
        "志望業界・ドメイン（複数選択可）",
        _INDUSTRY_OPTIONS,
        default=[i for i in (profile.target_industries or []) if i in _INDUSTRY_OPTIONS],
    )
    target_roles = st.multiselect(
        "志望職種・役割（複数選択可）",
        _ROLE_OPTIONS,
        default=[r for r in (profile.target_roles or []) if r in _ROLE_OPTIONS],
    )
    dev_phase = st.radio(
        "携わりたい開発フェーズ",
        ["R&D/PoC", "新規開発", "グロース/機能追加", "運用保守", "特になし"],
        index=["R&D/PoC", "新規開発", "グロース/機能追加", "運用保守", "特になし"].index(
            profile.dev_phase_preference or "特になし"
        ),
        horizontal=True,
    )
    min_salary = st.number_input(
        "希望最低年収（万円）",
        min_value=0,
        max_value=2000,
        value=int(profile.min_salary or 350),
        step=10,
        help="0 = 特にこだわらない",
    )

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button("← 戻る"):
            st.session_state["wizard_step"] = 1
            st.rerun()
    with col_next:
        if st.button("次へ →", type="primary"):
            st.session_state["w2_target_industries"] = target_industries
            st.session_state["w2_target_roles"] = target_roles
            st.session_state["w2_dev_phase"] = dev_phase if dev_phase != "特になし" else None
            st.session_state["w2_min_salary"] = min_salary if min_salary > 0 else None
            st.session_state["wizard_step"] = 3
            st.rerun()

# ─── Step 3: 勤務条件 ────────────────────────────────────────────────────
elif step == 3:
    st.subheader("Step 3: 勤務条件・価値観")

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
    st.markdown("**希望条件・価値観（重み付けに反映）**")

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
    eval_pref = st.radio(
        "評価制度の希望",
        ["成果主義", "プロセス重視", "年功序列", "こだわらない"],
        index=["成果主義", "プロセス重視", "年功序列", "こだわらない"].index(
            profile.eval_preference or "こだわらない"
        ),
        horizontal=True,
    )
    psych_safety = st.slider(
        "心理的安全性の重視度",
        0.0,
        1.0,
        float(profile.psych_safety_importance or 0.5),
        step=0.1,
        help="1.0に近いほど「発言しやすい文化」を重視してマッチングします",
    )
    mbti = st.text_input(
        "MBTI（任意）",
        value=profile.mbti or "",
        placeholder="例: INTJ, ENFP（空欄でもOK）",
        max_chars=10,
    )

    col_back, col_next = st.columns(2)
    with col_back:
        if st.button("← 戻る"):
            st.session_state["wizard_step"] = 2
            st.rerun()
    with col_next:
        if st.button("次へ →", type="primary"):
            prefectures = [s.strip() for s in prefs_raw.split(",") if s.strip()]
            st.session_state["w3_hard"] = {
                "max_overtime_hours": max_ot,
                "remote_work": remote_opts,
                "preferred_prefectures": prefectures,
            }
            st.session_state["w3_soft"] = {"priority": priority}
            st.session_state["w3_eval_pref"] = eval_pref if eval_pref != "こだわらない" else None
            st.session_state["w3_psych_safety"] = psych_safety
            st.session_state["w3_mbti"] = mbti.strip() or None
            st.session_state["wizard_step"] = 4
            st.rerun()

# ─── Step 4: 確認・保存 ──────────────────────────────────────────────────
elif step == 4:
    st.subheader("Step 4: 確認・保存")

    skills = st.session_state.get("w1_tech_skills", [])
    quals = st.session_state.get("w1_qualifications", [])
    exp = st.session_state.get("w1_project_exp", "")
    projects = st.session_state.get("w1_projects", [])
    grad_year = st.session_state.get("w1_graduation_year")
    major_val = st.session_state.get("w1_major", "")
    industries = st.session_state.get("w2_target_industries", [])
    roles = st.session_state.get("w2_target_roles", [])
    dev_phase = st.session_state.get("w2_dev_phase")
    min_sal = st.session_state.get("w2_min_salary")
    hard = st.session_state.get("w3_hard", {})
    soft = st.session_state.get("w3_soft", {})
    eval_pref = st.session_state.get("w3_eval_pref")
    psych_safety = st.session_state.get("w3_psych_safety", 0.5)
    mbti_val = st.session_state.get("w3_mbti")
    gh_result = st.session_state.get("w1_github_result")
    github_summary = gh_result.get("summary") if gh_result else None

    with st.expander("入力内容を確認", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            st.markdown(f"**卒業予定年**: {grad_year}年")
            st.markdown(f"**専攻**: {major_val or '未入力'}")
            st.markdown(f"**スキル**: {', '.join(skills) or '未入力'}")
            st.markdown(f"**資格**: {', '.join(quals) or 'なし'}")
            st.markdown(f"**志望業界**: {', '.join(industries) or '未選択'}")
            st.markdown(f"**志望職種**: {', '.join(roles) or '未選択'}")
        with col2:
            st.markdown(f"**開発フェーズ**: {dev_phase or '特になし'}")
            st.markdown(f"**希望最低年収**: {f'{min_sal}万円' if min_sal else 'こだわらない'}")
            st.markdown(f"**残業上限**: {hard.get('max_overtime_hours')} h/月")
            st.markdown(f"**リモート**: {', '.join(hard.get('remote_work', []))}")
            st.markdown(f"**勤務地**: {', '.join(hard.get('preferred_prefectures', [])) or '不問'}")
            st.markdown(f"**評価制度**: {eval_pref or 'こだわらない'}")
            st.markdown(f"**心理的安全性重視度**: {psych_safety:.1f}")
            st.markdown(f"**MBTI**: {mbti_val or '未入力'}")

        if projects:
            st.markdown(f"**製作物**: {len(projects)}件")
            for p in projects:
                ai_badge = " 🤖" if p.get("is_ai") else ""
                st.caption(
                    f"・{p.get('name', '無題')}（{p.get('type', '')} / "
                    f"{p.get('team_size', '')} / {p.get('duration', '')}）{ai_badge}"
                )

        if gh_result:
            st.markdown(
                f"**GitHub**: @{gh_result['username']} "
                f"（リポジトリ{gh_result['repo_count']}件 / "
                f"複雑さ{gh_result['project_complexity_score']:.2f}）"
            )

    st.info(
        "「保存してマッチング」を押すと Sonnet 4.6 で実務力スコアを算出します（数秒かかります）。"
    )

    col_back, col_save = st.columns(2)
    with col_back:
        if st.button("← 戻る"):
            st.session_state["wizard_step"] = 3
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
                    graduation_year=grad_year,
                    major=major_val or None,
                    target_industries=industries or None,
                    target_roles=roles or None,
                    dev_phase_preference=dev_phase,
                    min_salary=min_sal,
                    mbti=mbti_val,
                    eval_preference=eval_pref,
                    psych_safety_importance=psych_safety,
                    github_summary=github_summary,
                    projects=projects or None,
                    recompute_level=True,
                )
            st.session_state["db_profile"] = saved
            st.session_state["profile_loaded"] = True
            # v2キャッシュをすべてクリアしてマッチング再実行を促す
            for key in list(st.session_state.keys()):
                if key.startswith("v2_"):
                    del st.session_state[key]

            st.success(
                f"保存完了！ 実務力スコア: **{saved.tech_level_score:.2f}** / 1.0\n\n"
                f"> {saved.tech_level_rationale}"
            )
            if github_summary:
                st.caption(f"GitHub @{gh_result['username']} のデータも評価に反映されました。")
            st.session_state["wizard_step"] = 0
            st.rerun()
