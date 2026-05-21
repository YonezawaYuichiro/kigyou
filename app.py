"""Step 3: GradMatch-AI 推薦UI（Streamlit）。

起動: streamlit run app.py
"""

import json

import pandas as pd
import streamlit as st
from anthropic import Anthropic
from sqlalchemy import select

from backend.api.scorer import calc_score, load_profile
from backend.config import DATA_DIR, PROMPTS_DIR, settings
from backend.database import get_session
from backend.models import Company, CompanyMetrics


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
    return json.loads(resp.content[0].text)


PROFILE_PATH = DATA_DIR / "my_profile.json"

st.set_page_config(page_title="GradMatch-AI", page_icon="🎯", layout="wide")
st.title("🎯 GradMatch-AI — 企業マッチング")

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

req_skills = [s.strip() for s in req_skills_raw.split(",") if s.strip()]
bonus_skills = [s.strip() for s in bonus_skills_raw.split(",") if s.strip()]
preferred_prefs = [s.strip() for s in prefs_raw.split(",") if s.strip()]

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
}

st.sidebar.divider()
if st.sidebar.button("💾 プロフィールに保存"):
    updated = {**profile, "weights": {k: round(v, 4) for k, v in normalized.items()}}
    updated["hard_filters"]["max_overtime_hours"] = max_ot
    updated["hard_filters"]["min_openwork_score"] = min_score
    updated["required_skills"] = req_skills
    updated["bonus_skills"] = bonus_skills
    updated["preferred_prefectures"] = preferred_prefs
    PROFILE_PATH.write_text(json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8")
    st.sidebar.success("保存しました")


# ─── メイン: スコアリング ─────────────────────────────────────────────────
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


raw_rows = _fetch_companies()

results = []
for r in raw_rows:
    # Company/CompanyMetrics の代わりに dict から疑似オブジェクトを使う
    class _C:
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

# ─── テーブル表示 ──────────────────────────────────────────────────────────
st.subheader(f"マッチング結果: {len(results)} 社")

df_src = pd.DataFrame(results)
df = df_src[
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
# None を NaN に変換（object 列のまま "None" 文字列で表示されるのを防ぐ）
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

st.dataframe(
    df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "マッチスコア": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.3f"),
        "OW評価": st.column_config.NumberColumn(format="%.2f ★"),
        "残業h": st.column_config.NumberColumn(format="%.1f h"),
        "年収(万)": st.column_config.NumberColumn(format="%d 万円"),
        "従業員数": st.column_config.NumberColumn(format="%d 名"),
    },
)

# ─── 企業詳細 ──────────────────────────────────────────────────────────────
st.divider()
st.subheader("企業詳細")

selected_name = st.selectbox("企業を選択", [r["name"] for r in results])
if selected_name:
    detail = next(r for r in results if r["name"] == selected_name)

    # スコア根拠グラフ
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
            "上場" if detail["is_listed"] else ("非上場" if detail["is_listed"] is False else "N/A")
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

    # ─── 就職分析 ──────────────────────────────────────────────────────────
    st.divider()
    st.subheader("就職分析")
    cache_key = f"entry_analysis_{detail['name']}"
    if cache_key not in st.session_state:
        st.session_state[cache_key] = None

    if st.button("🔍 就職分析を実行（Claude Haiku）"):
        with st.spinner("分析中..."):
            try:
                st.session_state[cache_key] = _run_entry_analysis(detail, active_profile)
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
