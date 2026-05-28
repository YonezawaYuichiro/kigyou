"""GradMatch-AI 推薦UI（Streamlit）。

起動: streamlit run app.py

タブ構成:
  タブ1 🏆 理想企業     - V2 ベクトルマッチング（ideal_score 降順）
  タブ2 🎯 受けるべき企業 - V2 合格可能性スコア（realistic_score 降順）
  タブ3 📊 従来スコアリング - V1 ルールベース5軸（後方互換）
"""

import json
import uuid

import pandas as pd
import sqlalchemy as sa
import streamlit as st
from anthropic import Anthropic
from sqlalchemy import select

from backend.api.matching_engine import compute_matches
from backend.api.profile_manager import load_or_create_profile
from backend.api.scorer import calc_score, load_profile
from backend.config import DATA_DIR, PROMPTS_DIR, settings
from backend.database import get_session
from backend.models import Company, CompanyMetrics, UserProfile


@st.cache_resource
def _get_anthropic() -> Anthropic:
    return Anthropic(api_key=settings.anthropic_api_key)


def _run_entry_analysis(company_data: dict, user_profile: dict) -> dict | None:
    """Claude Haiku で就職難易度・必要スキルを分析する。"""
    company_info = "\n".join(
        [
            f"企業名: {company_data['name']}",
            f"カテゴリ: {company_data['estimated_category']}",
            f"技術スタック: {', '.join(company_data['tech_stack'])}",
            f"採用職種: {', '.join(company_data.get('hiring_roles', []))}",
            f"従業員数: {company_data.get('employee_count') or 'N/A'}",
            f"上場区分: {'上場' if company_data.get('is_listed') else '非上場' if company_data.get('is_listed') is False else 'N/A'}",
            f"平均年収: {company_data.get('avg_annual_salary') or 'N/A'}万円",
            f"OpenWork評価: {company_data.get('openwork_score') or 'N/A'}",
        ]
    )
    user_skills = f"保有スキル: {', '.join(user_profile.get('required_skills', []) + user_profile.get('bonus_skills', []))}"
    template = (PROMPTS_DIR / "entry_analysis.txt").read_text(encoding="utf-8")
    prompt = template.format(company_info=company_info, user_skills=user_skills)
    resp = _get_anthropic().messages.create(
        model=settings.haiku_model,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


PROFILE_PATH = DATA_DIR / "my_profile.json"

st.set_page_config(page_title="GradMatch-AI", page_icon="🎯", layout="wide")
st.title("🎯 GradMatch-AI — 企業マッチング")

# ─── セッション管理（V2用） ────────────────────────────────────────────────
if "session_id" not in st.session_state:
    st.session_state["session_id"] = str(uuid.uuid4())
_session_id: str = st.session_state["session_id"]

# ─── サイドバー: 重みスライダー ────────────────────────────────────────────
st.sidebar.header("重み設定")

profile = load_profile(PROFILE_PATH)

w_tech = st.sidebar.slider("技術成長性", 0.0, 1.0, float(profile["weights"]["tech_growth"]), 0.05)
w_wlb = st.sidebar.slider("WLB", 0.0, 1.0, float(profile["weights"]["wlb"]), 0.05)
w_size = st.sidebar.slider("企業規模", 0.0, 1.0, float(profile["weights"]["company_size"]), 0.05)
w_self = st.sidebar.slider(
    "自社開発度", 0.0, 1.0, float(profile["weights"]["self_developed"]), 0.05
)
w_loc = st.sidebar.slider("立地", 0.0, 1.0, float(profile["weights"]["location"]), 0.05)

total = w_tech + w_wlb + w_size + w_self + w_loc
if total == 0:
    st.sidebar.error("重みの合計が 0 です。")
    st.stop()

normalized = {
    "tech_growth": w_tech / total,
    "wlb": w_wlb / total,
    "company_size": w_size / total,
    "self_developed": w_self / total,
    "location": w_loc / total,
}
st.sidebar.caption(f"合計 {total:.2f} → 正規化済み")

st.sidebar.divider()
st.sidebar.header("ハードフィルター")
apply_filters = st.sidebar.toggle("ハードフィルターを適用", value=True)
max_ot = st.sidebar.number_input(
    "残業上限 (h/月)",
    min_value=0,
    max_value=80,
    value=int(profile["hard_filters"].get("max_overtime_hours", 30)),
)
min_score = st.sidebar.number_input(
    "OpenWork スコア下限",
    min_value=0.0,
    max_value=5.0,
    value=float(profile["hard_filters"].get("min_openwork_score", 3.0)),
    step=0.1,
)

min_coverage = st.sidebar.slider("データ充実度（最低項目数）", 0, 5, 0)

st.sidebar.divider()
st.sidebar.header("スキル・希望")
req_skills_raw = st.sidebar.text_area(
    "必須スキル（カンマ区切り）",
    value=", ".join(profile.get("required_skills", [])),
)
bonus_skills_raw = st.sidebar.text_area(
    "ボーナススキル（カンマ区切り）",
    value=", ".join(profile.get("bonus_skills", [])),
)
prefs_raw = st.sidebar.text_area(
    "希望都道府県（カンマ区切り）",
    value=", ".join(profile.get("preferred_prefectures", [])),
)
quals_raw = st.sidebar.text_area(
    "保有資格（カンマ区切り）",
    value=", ".join(profile.get("qualifications", [])),
    help="例: 応用情報技術者, AWS認定\n資格は技術成長性スコアにボーナスとして反映されます（最大 +0.25）",
)

req_skills = [s.strip() for s in req_skills_raw.split(",") if s.strip()]
bonus_skills = [s.strip() for s in bonus_skills_raw.split(",") if s.strip()]
preferred_prefs = [s.strip() for s in prefs_raw.split(",") if s.strip()]
qualifications = [s.strip() for s in quals_raw.split(",") if s.strip()]

active_profile = {
    **profile,
    "weights": normalized,
    "hard_filters": {
        "max_overtime_hours": max_ot if apply_filters else None,
        "min_openwork_score": min_score if apply_filters else None,
    },
    "required_skills": req_skills,
    "bonus_skills": bonus_skills,
    "preferred_prefectures": preferred_prefs,
    "qualifications": qualifications,
}

st.sidebar.divider()
if st.sidebar.button("💾 プロフィールに保存"):
    updated = {**profile, "weights": {k: round(v, 4) for k, v in normalized.items()}}
    updated["hard_filters"]["max_overtime_hours"] = max_ot
    updated["hard_filters"]["min_openwork_score"] = min_score
    updated["required_skills"] = req_skills
    updated["bonus_skills"] = bonus_skills
    updated["preferred_prefectures"] = preferred_prefs
    updated["qualifications"] = qualifications
    PROFILE_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    st.sidebar.success("保存しました")


# ─── メイン: 3タブ ───────────────────────────────────────────────────────
tab_ideal, tab_realistic, tab_v1 = st.tabs(
    ["🏆 理想企業（V2）", "🎯 受けるべき企業（V2）", "📊 従来スコアリング（V1）"]
)


# ═══════════════════════════════════════════════════════════════════════════
# V2 共通: マッチング実行
# ═══════════════════════════════════════════════════════════════════════════
def _run_v2_matches() -> dict:
    """V2 マッチングを実行し session_state にキャッシュする。"""
    if "v2_matches" not in st.session_state:
        with st.spinner("ベクトルマッチング実行中..."):
            user_profile = load_or_create_profile(_session_id)
            result = compute_matches(user_profile)
            st.session_state["v2_matches"] = result
            st.session_state["v2_tech_level"] = user_profile.tech_level_score or 0.5
            st.session_state["v2_dimension_weights"] = result.get("dimension_weights", [0.1] * 10)
    return st.session_state["v2_matches"]


def _render_v2_table(rows: list[dict], score_col: str, score_label: str) -> str | None:
    """V2結果テーブルを描画して選択された企業名を返す。"""
    if not rows:
        st.info("該当企業が見つかりませんでした。プロフィール設定を確認してください。")
        return None

    df = pd.DataFrame(
        [
            {
                "企業名": r["name"],
                score_label: r[score_col],
                "信頼度": "⚠️" if (r.get("overall_confidence") or 1.0) < 0.4 else "✅",
                "カテゴリ": r["estimated_category"],
                "都道府県": r["hq_prefecture"],
                "OW評価": r.get("openwork_score"),
                "残業h": r.get("avg_overtime_hours"),
                "年収(万)": r.get("avg_annual_salary"),
            }
            for r in rows
        ]
    )
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            score_label: st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.3f"),
            "OW評価": st.column_config.NumberColumn(format="%.2f ★"),
            "残業h": st.column_config.NumberColumn(format="%.1f h"),
            "年収(万)": st.column_config.NumberColumn(format="%d 万円"),
        },
    )
    selected = event.selection.rows if event and event.selection else []
    return rows[selected[0]]["name"] if selected else None


@st.cache_data(ttl=300)
def _get_all_tech_levels() -> list[float]:
    """DB内の全UserProfileのtech_level_scoreを取得する。"""
    with get_session() as session:
        rows = (
            session.execute(
                sa.select(UserProfile.tech_level_score).where(
                    UserProfile.tech_level_score.is_not(None)
                )
            )
            .scalars()
            .all()
        )
    return [float(v) for v in rows]


def _render_market_position(tech_level: float) -> None:
    """ユーザーの実務力スコアを全ユーザーとの相対比較で表示する。"""
    all_levels = _get_all_tech_levels()
    if len(all_levels) < 2:
        return
    below = sum(1 for v in all_levels if v < tech_level)
    percentile = round(below / len(all_levels) * 100)

    if percentile >= 80:
        tier, color = "上位層", "🟢"
    elif percentile >= 50:
        tier, color = "中上位層", "🔵"
    elif percentile >= 20:
        tier, color = "中位層", "🟡"
    else:
        tier, color = "初級層", "🟠"

    with st.expander(f"📊 市場ポジション: {color} {tier}（上位 {100 - percentile}%）"):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.metric("あなたの実務力スコア", f"{tech_level:.2f}")
            st.caption(f"登録ユーザー {len(all_levels)} 人中 上位 {100 - percentile}% の実務力です")
        with col_m2:
            # tech_level_scoreに基づく「受けるべき企業帯」の目安
            if tech_level >= 0.7:
                target_range = "大手/メガベンチャー・競争率の高いスタートアップ"
            elif tech_level >= 0.5:
                target_range = "中堅SaaS・成長期スタートアップ・SIer上位"
            elif tech_level >= 0.3:
                target_range = "中小規模Web・受託開発・SIer中堅"
            else:
                target_range = "研修充実・未経験歓迎の企業"
            st.metric("受けるべき企業帯の目安", "")
            st.caption(f"→ {target_range}")

        scores_series = pd.Series(all_levels)
        hist_data = (
            pd.cut(
                scores_series,
                bins=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                labels=["0.0-0.2", "0.2-0.4", "0.4-0.6", "0.6-0.8", "0.8-1.0"],
            )
            .value_counts()
            .sort_index()
        )
        st.bar_chart(hist_data, height=120, x_label="実務力スコア帯", y_label="ユーザー数")


_DIM_LABELS = [
    "立地",
    "ビジョン",
    "BM堅牢性",
    "財務健全性",
    "業界トレンド",
    "カルチャー",
    "キャリア成長",
    "WLB",
    "採用透明度",
    "開発環境",
]

_DIM_EVIDENCE_KEYS = {
    1: "new_biz_policy_evidence",
    5: "psychological_safety_evidence",
    6: "junior_authority_evidence",
    9: "tech_env_evidence",
}


def _render_xai_panel(detail: dict, dimension_weights: list[float]) -> None:
    """なぜこの企業がマッチするかを説明するパネル。"""
    dim_scores = detail.get("dim_scores")
    if not dim_scores or len(dim_scores) != 10 or len(dimension_weights) != 10:
        st.info("10次元ベクトルが未計算のため、マッチング説明を表示できません。")
        return

    ideal = detail["ideal_score"]
    realistic = detail["realistic_score"]
    gap = round(ideal - realistic, 4)
    tech_demand = detail.get("tech_demand", 0.5)

    # スコア差分
    col_i, col_r, col_g = st.columns(3)
    with col_i:
        st.metric("理想スコア", f"{ideal:.3f}", help="コサイン類似度：価値観の一致度")
    with col_r:
        delta_str = f"-{gap:.3f}" if gap > 0.005 else None
        st.metric(
            "現実的スコア",
            f"{realistic:.3f}",
            delta=delta_str,
            delta_color="inverse",
        )
    with col_g:
        if gap > 0.05:
            st.metric(
                "技術ギャップ補正",
                f"-{gap:.3f}",
                help=f"企業の技術要求 {tech_demand:.2f} に対してあなたの実務力が低いため割引",
            )
        else:
            st.metric("技術ギャップ補正", "なし ✅")

    # 企業スコア vs ユーザー重み 比較チャート
    st.markdown("**10次元スコア比較（企業 vs あなたの重み）**")
    comp_df = pd.DataFrame(
        {"企業スコア": dim_scores, "あなたの重み": dimension_weights},
        index=_DIM_LABELS,
    )
    st.bar_chart(comp_df, horizontal=True, height=310)

    # 貢献度トップ3次元
    contributions = [s * w for s, w in zip(dim_scores, dimension_weights, strict=True)]
    top3 = sorted(range(10), key=lambda i: contributions[i], reverse=True)[:3]

    st.markdown("**マッチ理由トップ3次元**")
    for rank, idx in enumerate(top3, 1):
        label = _DIM_LABELS[idx]
        score = dim_scores[idx]
        weight = dimension_weights[idx]
        contrib = contributions[idx]
        evidence_key = _DIM_EVIDENCE_KEYS.get(idx)
        evidence = detail.get(evidence_key) if evidence_key else None

        with st.expander(
            f"#{rank} **{label}**  ー  企業 {score:.2f} × 重み {weight:.3f} = 貢献度 {contrib:.3f}",
            expanded=(rank == 1),
        ):
            if evidence:
                st.caption(f"根拠: {evidence}")
            else:
                st.caption("証拠テキストなし（データ不足 or 該当次元に証拠フィールドなし）")


def _render_v2_detail(name: str, rows: list[dict], dimension_weights: list[float]) -> None:
    """V2企業詳細を描画する。"""
    detail = next((r for r in rows if r["name"] == name), None)
    if not detail:
        return
    st.subheader(f"企業詳細: {name}")
    conf = detail.get("overall_confidence")
    if conf is not None and conf < 0.4:
        st.warning(
            f"情報不足（信頼度 {conf:.2f}）: LLM抽出データが少ないため精度が低い可能性があります。"
        )

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("残業", f"{detail.get('avg_overtime_hours') or 'N/A'} h/月")
        st.metric("年収", f"{detail.get('avg_annual_salary') or 'N/A'} 万円")
    with col2:
        st.metric("心理的安全性", f"{detail.get('psychological_safety_score') or 'N/A'} / 5")
        st.metric("若手裁量", f"{detail.get('junior_authority_score') or 'N/A'} / 5")
    with col3:
        st.metric("OW評価", f"{detail.get('openwork_score') or 'N/A'} ★")
        remote_label = {
            "full": "フルリモート",
            "partial": "一部リモート",
            "none": "出社のみ",
        }.get(detail.get("remote_work_policy") or "", "不明")
        st.metric("リモート", remote_label)

    links = []
    if detail.get("official_url"):
        links.append(f"[公式サイト]({detail['official_url']})")
    if detail.get("openwork_url"):
        links.append(f"[OpenWork]({detail['openwork_url']})")
    if detail.get("green_url"):
        links.append(f"[Green]({detail['green_url']})")
    if links:
        st.markdown("  |  ".join(links))

    st.divider()
    with st.expander("🔍 なぜこの企業がマッチするか？", expanded=True):
        _render_xai_panel(detail, dimension_weights)


# ─── タブ1: 理想企業 ───────────────────────────────────────────────────────
with tab_ideal:
    tech_level = st.session_state.get("v2_tech_level", 0.5)
    st.caption(
        f"あなたの実務力スコア: **{tech_level:.2f}** / 1.0　　[プロフィール設定](./1_profile_setup)で更新できます。"
    )

    _render_market_position(tech_level)

    col_refresh, col_info = st.columns([1, 5])
    with col_refresh:
        if st.button("🔄 再計算", key="refresh_ideal"):
            for k in [k for k in st.session_state if k.startswith("v2_")]:
                del st.session_state[k]
            st.rerun()

    matches = _run_v2_matches()
    ideal_rows = matches.get("ideal", [])
    st.subheader(f"理想企業ランキング: {len(ideal_rows)} 社")

    selected_ideal = _render_v2_table(ideal_rows, "ideal_score", "理想スコア")
    if selected_ideal:
        st.session_state["v2_selected_ideal"] = selected_ideal

    dw = st.session_state.get("v2_dimension_weights", [0.1] * 10)
    selected = st.session_state.get("v2_selected_ideal")
    if selected:
        st.divider()
        _render_v2_detail(selected, ideal_rows, dw)

# ─── タブ2: 受けるべき企業 ─────────────────────────────────────────────────
with tab_realistic:
    matches = _run_v2_matches()
    realistic_rows = matches.get("realistic", [])
    st.subheader(f"受けるべき企業ランキング: {len(realistic_rows)} 社")
    st.caption("理想スコアを実務力スコアとの差（技術レベルギャップ）で補正したランキングです。")

    selected_real = _render_v2_table(realistic_rows, "realistic_score", "現実的スコア")
    if selected_real:
        st.session_state["v2_selected_real"] = selected_real

    dw = st.session_state.get("v2_dimension_weights", [0.1] * 10)
    selected = st.session_state.get("v2_selected_real")
    if selected:
        st.divider()
        _render_v2_detail(selected, realistic_rows, dw)


@st.cache_data(ttl=300)
def _fetch_companies() -> list[dict]:
    rows = []
    with get_session() as session:
        companies = session.scalars(select(Company)).all()
        for c in companies:
            m = c.metrics
            rows.append(
                {
                    "id": str(c.id),
                    "name": c.name,
                    "official_url": c.official_url,
                    "hq_prefecture": c.hq_prefecture,
                    "estimated_category": c.estimated_category,
                    "tech_stack": list(c.tech_stack or []),
                    "hiring_roles": list(c.hiring_roles or []),
                    "llm_confidence": c.llm_confidence,
                    "openwork_score": m.openwork_score if m else None,
                    "avg_overtime_hours": m.avg_overtime_hours if m else None,
                    "paid_leave_rate": m.paid_leave_rate if m else None,
                    "avg_annual_salary": m.avg_annual_salary if m else None,
                    "openwork_review_count": m.openwork_review_count if m else None,
                    "openwork_url": m.openwork_url if m else None,
                    "employee_count": m.employee_count if m else None,
                    "average_age": m.average_age if m else None,
                    "is_listed": m.is_listed if m else None,
                    "founded_year": m.founded_year if m else None,
                    "green_url": m.green_url if m else None,
                    "ow_score_growth": m.ow_score_growth if m else None,
                    "ow_score_morale": m.ow_score_morale if m else None,
                    "ow_score_openness": m.ow_score_openness if m else None,
                    "remote_work_policy": m.remote_work_policy if m else None,
                }
            )
    return rows


# ─── タブ3: 従来スコアリング（V1） ────────────────────────────────────────
with tab_v1:
    raw_rows = _fetch_companies()

    results = []
    for r in raw_rows:

        class _C:  # noqa: N801
            pass

        c = _C()
        c.tech_stack = r["tech_stack"]
        c.estimated_category = r["estimated_category"]
        c.hq_prefecture = r["hq_prefecture"]

        m: CompanyMetrics | None = None
        if (
            r["openwork_score"] is not None
            or r["avg_overtime_hours"] is not None
            or r["employee_count"] is not None
        ):
            m = _C()  # type: ignore[assignment]
            m.openwork_score = r["openwork_score"]  # type: ignore[union-attr]
            m.avg_overtime_hours = r["avg_overtime_hours"]  # type: ignore[union-attr]
            m.paid_leave_rate = r["paid_leave_rate"]  # type: ignore[union-attr]
            m.openwork_review_count = r["openwork_review_count"]  # type: ignore[union-attr]
            m.employee_count = r["employee_count"]  # type: ignore[union-attr]
            m.ow_score_growth = r["ow_score_growth"]  # type: ignore[union-attr]
            m.ow_score_morale = r["ow_score_morale"]  # type: ignore[union-attr]
            m.ow_score_openness = r["ow_score_openness"]  # type: ignore[union-attr]
            m.remote_work_policy = r["remote_work_policy"]  # type: ignore[union-attr]

        if apply_filters:
            filters = active_profile["hard_filters"]
            if filters.get("max_overtime_hours") and r["avg_overtime_hours"] is not None:
                if r["avg_overtime_hours"] > filters["max_overtime_hours"]:
                    continue
            if filters.get("min_openwork_score") and r["openwork_score"] is not None:
                if r["openwork_score"] < filters["min_openwork_score"]:
                    continue

        score_result = calc_score(c, m, active_profile)  # type: ignore[arg-type]
        results.append(
            {
                **r,
                "score": score_result["total"],
                "axes": score_result["axes"],
                "data_coverage": score_result["data_coverage"],
            }
        )

    results.sort(key=lambda x: x["score"], reverse=True)
    if min_coverage > 0:
        results = [r for r in results if r["data_coverage"] >= min_coverage]

    header_col, search_col = st.columns([2, 3])
    with header_col:
        st.subheader(f"マッチング結果: {len(results)} 社")
    with search_col:
        search_query = st.text_input(
            "🔍 企業名で絞り込み", placeholder="例: サイボウズ", label_visibility="collapsed"
        )

    filtered = (
        [r for r in results if search_query.lower() in r["name"].lower()]
        if search_query
        else results
    )

    df_src = pd.DataFrame(filtered)
    df = (
        df_src[
            [
                "name",
                "score",
                "data_coverage",
                "estimated_category",
                "hq_prefecture",
                "openwork_score",
                "avg_overtime_hours",
                "avg_annual_salary",
                "employee_count",
                "llm_confidence",
            ]
        ].copy()
        if not df_src.empty
        else pd.DataFrame()
    )
    if not df.empty:
        for col in ["openwork_score", "avg_overtime_hours", "avg_annual_salary", "employee_count"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["data_coverage"] = df["data_coverage"].apply(lambda n: "★" * int(n) + "☆" * (5 - int(n)))
        df.columns = [
            "企業名",
            "マッチスコア",
            "充実度",
            "カテゴリ",
            "都道府県",
            "OW評価",
            "残業h",
            "年収(万)",
            "従業員数",
            "信頼度",
        ]

    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "マッチスコア": st.column_config.ProgressColumn(
                min_value=0, max_value=1, format="%.3f"
            ),
            "OW評価": st.column_config.NumberColumn(format="%.2f ★"),
            "残業h": st.column_config.NumberColumn(format="%.1f h"),
            "年収(万)": st.column_config.NumberColumn(format="%d 万円"),
            "従業員数": st.column_config.NumberColumn(format="%d 名"),
        },
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows and filtered:
        st.session_state["selected_company"] = filtered[selected_rows[0]]["name"]

    with st.expander("企業を手動追加"):
        with st.form("manual_add_form"):
            url_input = st.text_input("企業のURL（例: https://hutzper.com/）")
            submitted = st.form_submit_button("追加")
        if submitted and url_input.strip():
            import contextlib
            import io

            from backend.seed.manual_add import add_companies

            buf = io.StringIO()
            with st.spinner("追加中..."):
                try:
                    with contextlib.redirect_stdout(buf):
                        add_companies([url_input.strip()])
                    st.success(buf.getvalue())
                    _fetch_companies.clear()
                except Exception as e:
                    st.error(f"追加失敗: {e}")

    st.divider()
    selected_name = st.session_state.get("selected_company")
    if selected_name and not any(r["name"] == selected_name for r in results):
        selected_name = None

    if selected_name:
        st.subheader(f"企業詳細: {selected_name}")
        detail = next(r for r in results if r["name"] == selected_name)

        _axis_labels = {
            "tech_growth": "技術成長性",
            "wlb": "WLB",
            "company_size": "企業規模",
            "self_developed": "自社開発度",
            "location": "立地",
        }
        axes = detail["axes"]
        ax_df = pd.DataFrame(
            {"スコア": [axes[k] for k in _axis_labels]},
            index=list(_axis_labels.values()),
        )
        cov = detail["data_coverage"]
        detail_col, chart_col = st.columns([2, 3])
        with detail_col:
            st.metric("マッチスコア", f"{detail['score']:.3f}")
            st.caption(f"データ充実度: {'★' * cov}{'☆' * (5 - cov)} ({cov}/5項目)")
        with chart_col:
            st.bar_chart(ax_df, horizontal=True, height=200)

        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("OpenWork 評価", f"{detail['openwork_score'] or 'N/A'}")
            st.metric("従業員数", f"{detail['employee_count'] or 'N/A'} 名")
        with col2:
            st.metric("残業時間", f"{detail['avg_overtime_hours'] or 'N/A'} h/月")
            st.metric("平均年収", f"{detail['avg_annual_salary'] or 'N/A'} 万円")
            st.metric("平均年齢", f"{detail['average_age'] or 'N/A'} 歳")
        with col3:
            st.metric("カテゴリ", detail["estimated_category"])
            st.metric("所在地", detail["hq_prefecture"])
            listed_label = (
                "上場"
                if detail["is_listed"]
                else ("非上場" if detail["is_listed"] is False else "N/A")
            )
            st.metric("上場区分", listed_label)

        founded = detail.get("founded_year")
        st.markdown(
            f"**設立年**: {founded or 'N/A'}年　**技術スタック**: {', '.join(detail['tech_stack']) or 'N/A'}"
        )

        links = []
        if detail["official_url"]:
            links.append(f"[公式サイト]({detail['official_url']})")
        if detail["openwork_url"]:
            links.append(f"[OpenWork]({detail['openwork_url']})")
        if detail.get("green_url"):
            links.append(f"[Green]({detail['green_url']})")
        if links:
            st.markdown("  |  ".join(links))

        st.divider()
        st.subheader("就職分析")
        cache_key = f"entry_analysis_{detail['name']}"
        if cache_key not in st.session_state:
            st.session_state[cache_key] = None

        if st.button("🔍 就職分析を実行（Claude Haiku）"):
            with st.spinner("分析中..."):
                try:
                    result = _run_entry_analysis(detail, active_profile)
                    if result is None:
                        st.error(
                            "分析失敗: LLMの応答をJSONとして解析できませんでした。再度お試しください。"
                        )
                    else:
                        st.session_state[cache_key] = result
                except Exception as e:
                    st.error(f"分析失敗: {e}")

        analysis = st.session_state.get(cache_key)
        if analysis:
            diff_color = {"低": "green", "中": "orange", "高": "red"}.get(
                analysis.get("difficulty", ""), "gray"
            )
            st.markdown(
                f"**就職難易度**: :{diff_color}[{analysis['difficulty']}]　{analysis['difficulty_reason']}"
            )
            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**重要スキル**")
                for s in analysis.get("required_skills", []):
                    st.markdown(f"- {s}")
            with col_b:
                st.markdown("**スキルギャップ**")
                gaps = analysis.get("skill_gap", [])
                if gaps:
                    for s in gaps:
                        st.markdown(f"- ⚠️ {s}")
                else:
                    st.markdown("✅ ギャップなし")
            st.info(analysis.get("advice", ""))
