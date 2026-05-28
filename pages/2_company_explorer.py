"""企業情報閲覧ページ（V3 Phase 2）。

全341社を一覧表示し、フィルター・検索・詳細表示ができる。

フィルター:
  - カテゴリ / 都道府県 / confidence / 従業員数規模 / リモート可否

詳細表示:
  - 基本情報（OW評価・残業・年収・リモート）
  - 10次元スコアバーチャート（CompanyVector）
  - 主要ディメンション詳細（証拠テキスト付き）
  - confidence < 0.4 には「情報不足」バッジ表示
"""

import pandas as pd
import sqlalchemy as sa
import streamlit as st
from sqlalchemy.orm import selectinload

from backend.database import get_session
from backend.models import Company, CompanyDimensions, CompanyMetrics, CompanyVector

st.set_page_config(page_title="企業情報一覧 | GradMatch-AI", page_icon="🏢", layout="wide")
st.title("🏢 企業情報一覧")
st.caption("登録された全企業を検索・絞り込みできます。企業名をクリックして詳細を確認してください。")

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


@st.cache_data(ttl=600)
def _load_all_companies() -> list[dict]:
    """全企業情報をDBから取得してdictリストで返す。"""
    rows = []
    with get_session() as session:
        companies = (
            session.execute(
                sa.select(Company).options(
                    selectinload(Company.metrics),
                    selectinload(Company.dimensions),
                    selectinload(Company.vector),
                )
            )
            .scalars()
            .all()
        )
        for c in companies:
            m: CompanyMetrics | None = c.metrics
            d: CompanyDimensions | None = c.dimensions
            v: CompanyVector | None = c.vector
            rows.append(
                {
                    # 基本
                    "id": str(c.id),
                    "name": c.name,
                    "official_url": c.official_url,
                    "hq_prefecture": c.hq_prefecture or "不明",
                    "estimated_category": c.estimated_category or "不明",
                    "tech_stack": list(c.tech_stack or []),
                    "hiring_roles": list(c.hiring_roles or []),
                    # メトリクス
                    "openwork_score": m.openwork_score if m else None,
                    "avg_overtime_hours": m.avg_overtime_hours if m else None,
                    "avg_annual_salary": m.avg_annual_salary if m else None,
                    "employee_count": m.employee_count if m else None,
                    "remote_work_policy": m.remote_work_policy if m else None,
                    "is_listed": m.is_listed if m else None,
                    "founded_year": m.founded_year if m else None,
                    "average_age": m.average_age if m else None,
                    "ow_score_growth": m.ow_score_growth if m else None,
                    "ow_score_morale": m.ow_score_morale if m else None,
                    "ow_score_openness": m.ow_score_openness if m else None,
                    "openwork_url": m.openwork_url if m else None,
                    "green_url": m.green_url if m else None,
                    # ディメンション
                    "overall_confidence": d.overall_confidence if d else None,
                    "psychological_safety_score": (d.psychological_safety_score if d else None),
                    "psychological_safety_evidence": (
                        d.psychological_safety_evidence if d else None
                    ),
                    "junior_authority_score": d.junior_authority_score if d else None,
                    "junior_authority_evidence": (d.junior_authority_evidence if d else None),
                    "tech_env_evidence": d.tech_env_evidence if d else None,
                    "tech_modernity_score": d.tech_modernity_score if d else None,
                    "infra_cloud_score": d.infra_cloud_score if d else None,
                    "cicd_maturity_score": d.cicd_maturity_score if d else None,
                    "data_platform_score": d.data_platform_score if d else None,
                    "tech_debt_culture_score": d.tech_debt_culture_score if d else None,
                    "new_biz_policy_score": d.new_biz_policy_score if d else None,
                    "new_biz_policy_evidence": d.new_biz_policy_evidence if d else None,
                    "competitive_advantage_score": (d.competitive_advantage_score if d else None),
                    "evaluation_system_type": d.evaluation_system_type if d else None,
                    "skill_support_score": d.skill_support_score if d else None,
                    "skill_support_items": list(d.skill_support_items or []) if d else [],
                    "has_coding_test": d.has_coding_test if d else None,
                    "interviewer_type": d.interviewer_type if d else None,
                    "megatrend_score": d.megatrend_score if d else None,
                    "megatrend_alignment": list(d.megatrend_alignment or []) if d else [],
                    "low_confidence_fields": (list(d.low_confidence_fields or []) if d else []),
                    # 10次元ベクトル
                    "dim_scores": list(v.dim_scores) if v and v.dim_scores else None,
                }
            )
    return rows


def _employee_band(count: int | None) -> str:
    if count is None:
        return "不明"
    if count < 50:
        return "〜50名"
    if count < 200:
        return "50〜200名"
    if count < 500:
        return "200〜500名"
    if count < 1000:
        return "500〜1000名"
    return "1000名以上"


# ─── データ読み込み ────────────────────────────────────────────────────────
all_companies = _load_all_companies()

# ─── サイドバー: フィルター ────────────────────────────────────────────────
st.sidebar.header("絞り込み")

# テキスト検索
search_q = st.sidebar.text_input("🔍 企業名・スタック検索", placeholder="例: サイボウズ, React")

# カテゴリ
categories = sorted({c["estimated_category"] for c in all_companies})
sel_categories = st.sidebar.multiselect("カテゴリ", categories)

# 都道府県
prefectures = sorted({c["hq_prefecture"] for c in all_companies})
sel_prefs = st.sidebar.multiselect("都道府県", prefectures)

# リモート
sel_remote = st.sidebar.multiselect(
    "リモート可否",
    ["full", "partial", "none"],
    format_func=lambda x: {"full": "フルリモート", "partial": "一部リモート", "none": "出社のみ"}[
        x
    ],
)

# 信頼度
min_conf = st.sidebar.slider("最低信頼度", 0.0, 1.0, 0.0, 0.1)

# 従業員規模
_BANDS = ["〜50名", "50〜200名", "200〜500名", "500〜1000名", "1000名以上", "不明"]
sel_bands = st.sidebar.multiselect("従業員規模", _BANDS)

# 並び替え
sort_col = st.sidebar.selectbox(
    "並び替え",
    ["overall_confidence", "openwork_score", "avg_overtime_hours", "avg_annual_salary", "name"],
    format_func=lambda x: {
        "overall_confidence": "信頼度（高い順）",
        "openwork_score": "OW評価（高い順）",
        "avg_overtime_hours": "残業（少ない順）",
        "avg_annual_salary": "年収（高い順）",
        "name": "企業名（50音順）",
    }[x],
)

# ─── フィルタリング ────────────────────────────────────────────────────────
filtered = all_companies

if search_q:
    q = search_q.lower()
    filtered = [
        c
        for c in filtered
        if q in c["name"].lower() or any(q in t.lower() for t in c["tech_stack"])
    ]

if sel_categories:
    filtered = [c for c in filtered if c["estimated_category"] in sel_categories]

if sel_prefs:
    filtered = [c for c in filtered if c["hq_prefecture"] in sel_prefs]

if sel_remote:
    filtered = [c for c in filtered if c["remote_work_policy"] in sel_remote]

if min_conf > 0.0:
    filtered = [c for c in filtered if (c["overall_confidence"] or 0.0) >= min_conf]

if sel_bands:
    filtered = [c for c in filtered if _employee_band(c["employee_count"]) in sel_bands]

# ─── 並び替え ──────────────────────────────────────────────────────────────
reverse = sort_col != "avg_overtime_hours"
if sort_col == "name":
    filtered = sorted(filtered, key=lambda x: x["name"])
else:
    filtered = sorted(filtered, key=lambda x: x[sort_col] or 0, reverse=reverse)

# ─── 一覧テーブル ──────────────────────────────────────────────────────────
st.subheader(f"検索結果: {len(filtered)} 社 / 全{len(all_companies)}社")

if filtered:
    df = pd.DataFrame(
        [
            {
                "企業名": c["name"],
                "信頼度": (
                    f"⚠️ {c['overall_confidence']:.2f}"
                    if (c["overall_confidence"] or 1.0) < 0.4
                    else f"✅ {c['overall_confidence']:.2f}"
                    if c["overall_confidence"]
                    else "—"
                ),
                "カテゴリ": c["estimated_category"],
                "都道府県": c["hq_prefecture"],
                "OW評価": c["openwork_score"],
                "残業h": c["avg_overtime_hours"],
                "年収(万)": c["avg_annual_salary"],
                "従業員": c["employee_count"],
                "リモート": {
                    "full": "フル",
                    "partial": "一部",
                    "none": "出社",
                }.get(c["remote_work_policy"] or "", "—"),
            }
            for c in filtered
        ]
    )
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "OW評価": st.column_config.NumberColumn(format="%.2f ★"),
            "残業h": st.column_config.NumberColumn(format="%.1f h"),
            "年収(万)": st.column_config.NumberColumn(format="%d 万円"),
            "従業員": st.column_config.NumberColumn(format="%d 名"),
        },
    )
    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows:
        st.session_state["explorer_selected"] = filtered[selected_rows[0]]["name"]
else:
    st.info("条件に合う企業が見つかりませんでした。フィルターを緩めてください。")

# ─── 企業詳細 ──────────────────────────────────────────────────────────────
selected_name = st.session_state.get("explorer_selected")
if selected_name and not any(c["name"] == selected_name for c in filtered):
    selected_name = None

if selected_name:
    detail = next(c for c in filtered if c["name"] == selected_name)
    st.divider()
    st.subheader(f"🏢 {detail['name']}")

    conf = detail["overall_confidence"] or 0.0
    if conf < 0.4:
        st.warning(
            f"情報不足（信頼度 {conf:.2f}）: データが不足しているため、スコアの精度が低い可能性があります。"
        )
        if detail["low_confidence_fields"]:
            st.caption(f"不足フィールド: {', '.join(detail['low_confidence_fields'])}")
    else:
        st.success(f"信頼度: {conf:.2f} — データ充実")

    # 基本指標
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("OW総合評価", f"{detail['openwork_score'] or 'N/A'} ★")
        st.metric("残業時間", f"{detail['avg_overtime_hours'] or 'N/A'} h/月")
    with col2:
        st.metric("平均年収", f"{detail['avg_annual_salary'] or 'N/A'} 万円")
        st.metric("平均年齢", f"{detail['average_age'] or 'N/A'} 歳")
    with col3:
        st.metric("従業員数", f"{detail['employee_count'] or 'N/A'} 名")
        listed = (
            "上場"
            if detail["is_listed"]
            else ("非上場" if detail["is_listed"] is False else "不明")
        )
        st.metric("上場区分", listed)
    with col4:
        remote_label = {
            "full": "フルリモート",
            "partial": "一部リモート",
            "none": "出社のみ",
        }.get(detail["remote_work_policy"] or "", "不明")
        st.metric("リモート", remote_label)
        st.metric("設立年", f"{detail['founded_year'] or 'N/A'}年")

    # OWサブスコア
    ow_sub = {
        k: detail[k]
        for k in ["ow_score_growth", "ow_score_morale", "ow_score_openness"]
        if detail[k] is not None
    }
    if ow_sub:
        ow_df = pd.DataFrame(
            {"スコア": list(ow_sub.values())},
            index=[
                {
                    "ow_score_growth": "成長性",
                    "ow_score_morale": "社員士気",
                    "ow_score_openness": "風通し",
                }[k]
                for k in ow_sub
            ],
        )
        st.markdown("**OpenWork サブスコア（/5）**")
        st.bar_chart(ow_df, horizontal=True, height=120)

    # 技術スタック
    if detail["tech_stack"]:
        st.markdown(f"**技術スタック**: {', '.join(detail['tech_stack'])}")

    # リンク
    links = []
    if detail["official_url"]:
        links.append(f"[公式サイト]({detail['official_url']})")
    if detail["openwork_url"]:
        links.append(f"[OpenWork]({detail['openwork_url']})")
    if detail["green_url"]:
        links.append(f"[Green]({detail['green_url']})")
    if links:
        st.markdown("  |  ".join(links))

    st.divider()

    # 10次元スコアバーチャート
    dim_scores = detail["dim_scores"]
    if dim_scores:
        st.markdown("**10次元スコアベクトル（0.0〜1.0）**")
        dim_df = pd.DataFrame(
            {"スコア": dim_scores},
            index=_DIM_LABELS,
        )
        st.bar_chart(dim_df, horizontal=True, height=280)
    else:
        st.info("10次元ベクトルは未計算です。")

    st.divider()

    # ディメンション詳細
    st.markdown("**ディメンション詳細**")

    tab_culture, tab_career, tab_dev, tab_biz = st.tabs(
        ["🧠 カルチャー", "📈 キャリア", "💻 開発環境", "💡 事業・採用"]
    )

    with tab_culture:
        col_a, col_b = st.columns(2)
        with col_a:
            ps = detail["psychological_safety_score"]
            st.metric("心理的安全性", f"{ps:.1f} / 5" if ps else "N/A")
            if detail["psychological_safety_evidence"]:
                st.caption(detail["psychological_safety_evidence"])
        with col_b:
            ja = detail["junior_authority_score"]
            st.metric("若手裁量度", f"{ja:.1f} / 5" if ja else "N/A")
            if detail["junior_authority_evidence"]:
                st.caption(detail["junior_authority_evidence"])

    with tab_career:
        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("評価制度", detail["evaluation_system_type"] or "不明")
            ss = detail["skill_support_score"]
            st.metric("スキル支援スコア", f"{ss:.2f}" if ss else "N/A")
        with col_b:
            if detail["skill_support_items"]:
                st.markdown("**支援制度**")
                for item in detail["skill_support_items"]:
                    st.markdown(f"- {item}")

    with tab_dev:
        scores_map = {
            "モダン度": detail["tech_modernity_score"],
            "インフラ/クラウド": detail["infra_cloud_score"],
            "CI/CD成熟度": detail["cicd_maturity_score"],
            "データ基盤": detail["data_platform_score"],
            "技術的負債文化": detail["tech_debt_culture_score"],
        }
        valid_scores = {k: v for k, v in scores_map.items() if v is not None}
        if valid_scores:
            dev_df = pd.DataFrame(
                {"スコア": list(valid_scores.values())},
                index=list(valid_scores.keys()),
            )
            st.bar_chart(dev_df, horizontal=True, height=200)
        if detail["tech_env_evidence"]:
            st.info(f"**証拠テキスト**: {detail['tech_env_evidence']}")

    with tab_biz:
        col_a, col_b = st.columns(2)
        with col_a:
            nb = detail["new_biz_policy_score"]
            st.metric("ビジョン積極度", f"{nb:.2f}" if nb else "N/A")
            if detail["new_biz_policy_evidence"]:
                st.caption(detail["new_biz_policy_evidence"])
            ca = detail["competitive_advantage_score"]
            st.metric("競合優位性", f"{ca:.2f}" if ca else "N/A")
        with col_b:
            coding = detail["has_coding_test"]
            st.metric(
                "コーディングテスト",
                "あり ✅" if coding else ("なし" if coding is False else "不明"),
            )
            itype_label = {
                "current_engineer": "現職エンジニア",
                "hr_only": "人事のみ",
                "mixed": "混在",
                "unknown": "不明",
            }.get(detail["interviewer_type"] or "unknown", "不明")
            st.metric("面接官", itype_label)
            if detail["megatrend_alignment"]:
                st.markdown(f"**メガトレンド**: {', '.join(detail['megatrend_alignment'])}")
