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
import streamlit as st
from anthropic import Anthropic
from sqlalchemy import select

from backend.api.intent_translator import translate_intent
from backend.api.matching_engine import compute_matches
from backend.api.profile_manager import (
    load_or_create_profile,
    update_dimension_weights,
    update_hard_constraints,
)
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

# ─── サイドバー: V2クイック設定 ────────────────────────────────────────────
# V2プロフィールをsession_stateにキャッシュ（毎描画でDB呼び出しを避ける）
if "v2_profile_cache" not in st.session_state:
    st.session_state["v2_profile_cache"] = load_or_create_profile(_session_id)

_prof = st.session_state["v2_profile_cache"]
_hard = _prof.hard_constraints or {}
_dw = list(_prof.dimension_weights) if _prof.dimension_weights is not None else [0.1] * 10

st.sidebar.header("クイック設定")

# ── ハードフィルタ ──
st.sidebar.subheader("絞り込み条件")
_prefs_raw = st.sidebar.text_input(
    "勤務地（カンマ区切り・空欄=全国）",
    value=", ".join(_hard.get("preferred_prefectures", [])),
)
_max_ot_sb = st.sidebar.slider("残業上限（h/月）", 0, 80, int(_hard.get("max_overtime_hours", 30)))
_remote_sb = st.sidebar.multiselect(
    "リモートワーク",
    ["full", "partial", "none"],
    default=_hard.get("remote_work", ["full", "partial"]),
    format_func={"full": "フルリモート", "partial": "一部リモート", "none": "出社のみ"}.get,
)
_ALL_CATS = ["自社開発", "SIer", "スタートアップ", "受託開発", "その他"]
_cat_sb = st.sidebar.multiselect(
    "企業カテゴリ（空欄=全て）",
    _ALL_CATS,
    default=[c for c in _hard.get("preferred_categories", []) if c in _ALL_CATS],
)

# ── 次元重みスライダー（5主要次元を個別制御、残り5次元は均等配分） ──
st.sidebar.subheader("重み設定")
_SB_DIMS = {0: "立地", 5: "カルチャー", 6: "キャリア成長", 7: "WLB", 9: "技術・開発環境"}
_w_vals = {
    idx: st.sidebar.slider(label, 0.0, 1.0, float(_dw[idx]) if len(_dw) > idx else 0.1, 0.05)
    for idx, label in _SB_DIMS.items()
}
_remaining = max(0.0, 1.0 - sum(_w_vals.values()))
_other_w = _remaining / 5
_full_weights = [_other_w] * 10
for _idx, _v in _w_vals.items():
    _full_weights[_idx] = _v
_total_w = sum(_full_weights) or 1.0
_full_weights = [round(w / _total_w, 4) for w in _full_weights]
st.sidebar.caption(f"主要5次元合計: {sum(_w_vals.values()):.2f} → 正規化済み")

if st.sidebar.button("✅ 適用して再計算"):
    _prefectures = [s.strip() for s in _prefs_raw.split(",") if s.strip()]
    update_hard_constraints(
        _session_id,
        {
            "max_overtime_hours": _max_ot_sb,
            "remote_work": _remote_sb,
            "preferred_prefectures": _prefectures,
            "preferred_categories": _cat_sb,
        },
    )
    update_dimension_weights(_session_id, _full_weights)
    st.session_state.pop("v2_profile_cache", None)
    for _k in [k for k in st.session_state if k.startswith("v2_")]:
        del st.session_state[_k]
    st.rerun()

st.sidebar.divider()
st.sidebar.page_link("pages/1_profile_setup.py", label="📋 プロフィール詳細設定")


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
            st.session_state["v2_filter_stats"] = result.get("filter_stats", {})
    return st.session_state["v2_matches"]


def _render_v2_table(rows: list[dict], score_col: str, score_label: str) -> str | None:
    """V2結果テーブルを描画して選択された企業名を返す。"""
    if not rows:
        stats = st.session_state.get("v2_filter_stats", {})
        total = stats.get("total_companies", "?")
        passed = stats.get("passed_hard_filter", "?")
        has_vec = stats.get("has_vector", "?")
        st.warning(
            f"企業が見つかりませんでした　"
            f"DB: **{total}社** → ハードフィルタ通過: **{passed}社** → ベクトルあり: **{has_vec}社**"
        )
        if isinstance(passed, int) and passed == 0:
            st.caption("残業上限・リモート・勤務地の条件を緩めると企業が表示されます。")
        elif isinstance(has_vec, int) and has_vec == 0:
            st.caption("企業のベクトルデータが見つかりません。DBのセットアップを確認してください。")
        st.page_link("pages/1_profile_setup.py", label="👤 プロフィール設定を開く")
        return None

    df = pd.DataFrame(
        [
            {
                "企業名": r["name"],
                score_label: r[score_col],
                "信頼度": "⚠️" if (r.get("overall_confidence") or 1.0) < 0.4 else "✅",
                "カテゴリ": r["estimated_category"],
                "都道府県": r["hq_prefecture"],
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
        },
    )
    selected = event.selection.rows if event and event.selection else []
    return rows[selected[0]]["name"] if selected else None


# 新卒エンジニア志望学生の推定分布（6因子ルーブリック理論値）
# 各帯の「この帯以上のスコアを持つ学生の割合（上位X%）」の境界値
_STUDENT_PERCENTILE_THRESHOLDS = [
    (0.0, 100),
    (0.2, 85),
    (0.4, 55),
    (0.6, 25),
    (0.8, 7),
    (1.01, 1),
]
# 各スコア帯の学生分布（参照用）
_STUDENT_REF_DIST = {
    "初学者  (0.0–0.2)": 15,
    "基礎あり (0.2–0.4)": 30,
    "中程度  (0.4–0.6)": 30,
    "実務近い (0.6–0.8)": 18,
    "即戦力  (0.8–1.0)": 7,
}


def _estimate_student_percentile(score: float) -> int:
    """新卒エンジニア志望者の推定分布における「上位X%」を返す。"""
    for i in range(len(_STUDENT_PERCENTILE_THRESHOLDS) - 1):
        lo, lo_pct = _STUDENT_PERCENTILE_THRESHOLDS[i]
        hi, hi_pct = _STUDENT_PERCENTILE_THRESHOLDS[i + 1]
        if lo <= score < hi:
            t = (score - lo) / (hi - lo)
            return max(1, round(lo_pct + t * (hi_pct - lo_pct)))
    return 1


def _render_market_position(tech_level: float) -> None:
    """ユーザーの実務力スコアを新卒学生の推定分布と比較して表示する。"""
    top_pct = _estimate_student_percentile(tech_level)

    if tech_level >= 0.6:
        tier, color = "上位層", "🟢"
    elif tech_level >= 0.4:
        tier, color = "中上位層", "🔵"
    elif tech_level >= 0.2:
        tier, color = "中位層", "🟡"
    else:
        tier, color = "基礎層", "🟠"

    with st.expander(f"📊 市場ポジション: {color} {tier}（推定 上位 {top_pct}%）"):
        col_m1, col_m2 = st.columns(2)
        with col_m1:
            st.metric("あなたの実務力スコア", f"{tech_level:.2f}")
            st.caption(
                f"新卒エンジニア志望者の推定分布で **上位 約{top_pct}%**"
                f"（6因子ルーブリック理論値。個人差あり）"
            )
        with col_m2:
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

        # 参照分布（横棒グラフ: ラベルが読みやすい）
        band_keys = list(_STUDENT_REF_DIST.keys())
        band_idx = min(int(tech_level / 0.2), 4)
        ref_df = pd.DataFrame(
            {
                "学生の割合(%)": list(_STUDENT_REF_DIST.values()),
                "あなたの位置": [10 if i == band_idx else 0 for i in range(5)],
            },
            index=band_keys,
        )
        st.bar_chart(ref_df, horizontal=True, height=200, x_label="割合(%)")
        st.caption(
            f"▲ 推定分布（理論値）  ／  あなた（{tech_level:.2f}）→ **{band_keys[band_idx].strip()}**"
        )

        st.divider()
        st.caption(
            "**実務力スコアの算出方法（6因子ルーブリック・学生基準 / Dreyfus習得モデル準拠）**"
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "因子": "F1 実装量",
                        "0点（未経験）": "実装なし",
                        "2点（平均的学生）": "個人3件以上/研究室",
                        "4点（学生上位5%）": "長期インターン(3ヶ月+)/OSS PR",
                    },
                    {
                        "因子": "F2 実装品質",
                        "0点（未経験）": "評価不可",
                        "2点（平均的学生）": "基本設計パターンあり",
                        "4点（学生上位5%）": "テスト+CI/CD+コードレビュー",
                    },
                    {
                        "因子": "F3 技術幅",
                        "0点（未経験）": "1領域のみ",
                        "2点（平均的学生）": "フルスタック",
                        "4点（学生上位5%）": "フルスタック+AI/ML+インフラ全揃い",
                    },
                    {
                        "因子": "F4 技術深度",
                        "0点（未経験）": "入門書レベル",
                        "2点（平均的学生）": "自力デバッグ可",
                        "4点（学生上位5%）": "複雑設計/AtCoder水色/AI論文実装",
                    },
                    {
                        "因子": "F5 資格・競プロ",
                        "0点（未経験）": "なし",
                        "2点（平均的学生）": "応用情報/AWS Associate/AtCoder茶",
                        "4点（学生上位5%）": "IPA高度試験/AtCoder水色以上",
                    },
                    {
                        "因子": "F6 外部発信",
                        "0点（未経験）": "なし",
                        "2点（平均的学生）": "Qiita10本以上/勉強会登壇",
                        "4点（学生上位5%）": "Kaggle Expert以上/学会発表",
                    },
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
        st.caption("スコア = (F1+F2+F3+F4+F5+F6) ÷ 24　※最大24点 → 1.0（学生基準）")


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

_REMOTE_LABELS = {"full": "フルリモート", "partial": "一部リモート", "none": "出社のみ"}


def _get_dim_evidence(idx: int, detail: dict) -> str | None:
    """次元インデックスに対応する証拠テキストを取得 or 手元データから合成する。"""
    # 専用フィールドがある次元を優先
    ev = detail.get(_DIM_EVIDENCE_KEYS[idx]) if idx in _DIM_EVIDENCE_KEYS else None
    if ev:
        return ev

    # 手元データから合成
    if idx == 0:  # 立地
        pref = detail.get("hq_prefecture")
        return f"本社所在地: {pref}" if pref else None
    if idx == 2:  # BM堅牢性
        cat = detail.get("estimated_category")
        return f"事業カテゴリ: {cat}" if cat else None
    if idx == 3:  # 財務健全性
        parts = []
        if detail.get("employee_count"):
            parts.append(f"従業員 {detail['employee_count']}名")
        if detail.get("openwork_score"):
            parts.append(f"OW評価 {detail['openwork_score']:.1f}★")
        return " / ".join(parts) if parts else None
    if idx == 4:  # 業界トレンド
        ts = detail.get("tech_stack")
        return f"技術スタック: {', '.join(ts[:6])}" if ts else None
    if idx == 7:  # WLB
        parts = []
        ot = detail.get("avg_overtime_hours")
        if ot is not None:
            parts.append(f"残業 {ot:.0f}h/月")
        remote = _REMOTE_LABELS.get(detail.get("remote_work_policy") or "", "")
        if remote:
            parts.append(remote)
        return " / ".join(parts) if parts else None
    if idx == 8:  # 採用透明度
        ct = detail.get("has_coding_test")
        if ct is not None:
            return f"コーディングテスト: {'あり' if ct else 'なし'}"
    return None


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

    # 企業スコア vs ユーザー重み 比較（dataframeで静的表示 → スクロール時リサイズなし）
    st.markdown("**10次元スコア比較（企業 vs あなたの重み）**")
    comp_df = pd.DataFrame(
        {
            "次元": _DIM_LABELS,
            "企業スコア": [round(s, 3) for s in dim_scores],
            "あなたの重み": [round(w, 3) for w in dimension_weights],
        }
    )
    st.dataframe(
        comp_df,
        hide_index=True,
        use_container_width=True,
        height=385,
        column_config={
            "企業スコア": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.3f"),
            "あなたの重み": st.column_config.ProgressColumn(
                min_value=0, max_value=0.5, format="%.3f"
            ),
        },
    )

    # 貢献度トップ3次元
    contributions = [s * w for s, w in zip(dim_scores, dimension_weights, strict=True)]
    top3 = sorted(range(10), key=lambda i: contributions[i], reverse=True)[:3]

    st.markdown("**マッチ理由トップ3次元**")
    for rank, idx in enumerate(top3, 1):
        label = _DIM_LABELS[idx]
        score = dim_scores[idx]
        weight = dimension_weights[idx]
        contrib = contributions[idx]
        evidence = _get_dim_evidence(idx, detail)

        with st.expander(
            f"#{rank} **{label}**  ー  企業 {score:.2f} × 重み {weight:.3f} = 貢献度 {contrib:.3f}",
            expanded=(rank == 1),
        ):
            if evidence:
                st.caption(f"根拠: {evidence}")
            else:
                st.caption("根拠データなし（この次元の証拠情報はDBに未登録）")


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

    # データがある項目だけ収集（ない項目は表示しない）
    _avail: list[tuple[str, str]] = []
    _ot = detail.get("avg_overtime_hours")
    if _ot is not None:
        _avail.append(("残業", f"{_ot:.0f} h/月"))
    _sal = detail.get("avg_annual_salary")
    if _sal is not None:
        _avail.append(("年収", f"{_sal:.0f} 万円"))
    _ps = detail.get("psychological_safety_score")
    if _ps is not None:
        _avail.append(("心理的安全性", f"{_ps:.1f} / 5"))
    _ja = detail.get("junior_authority_score")
    if _ja is not None:
        _avail.append(("若手裁量", f"{_ja:.1f} / 5"))
    _ow = detail.get("openwork_score")
    if _ow is not None:
        _avail.append(("OW評価", f"{_ow:.2f} ★"))
    _remote = _REMOTE_LABELS.get(detail.get("remote_work_policy") or "", "")
    if _remote:
        _avail.append(("リモート", _remote))

    if _avail:
        _cols = st.columns(min(len(_avail), 3))
        for _i, (_lbl, _val) in enumerate(_avail):
            with _cols[_i % 3]:
                st.metric(_lbl, _val)
    else:
        st.caption("数値データなし（OpenWork・LLM抽出の情報が未取得）")

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
    col_score, col_link = st.columns([3, 1])
    with col_score:
        st.caption(f"あなたの実務力スコア: **{tech_level:.2f}** / 1.0")
    with col_link:
        st.page_link("pages/1_profile_setup.py", label="👤 プロフィール設定で更新")

    _render_market_position(tech_level)

    # ─── 意図翻訳エンジン ───────────────────────────────────────────────────
    with st.expander("💬 自然言語で重みを調整（意図翻訳）"):
        st.caption(
            "理想の就職先を自由に書いてください。Sonnet 4.6 が10次元重みに変換してマッチングに反映します。"
        )
        intent_text = st.text_area(
            "理想の就職先（自由記述）",
            value=st.session_state.get("intent_text", ""),
            height=80,
            placeholder="例: AIを使った開発がしたい。若手でも裁量が大きく、残業が少ない会社が良い。",
            label_visibility="collapsed",
        )
        col_tr, col_apply, col_clear = st.columns([2, 2, 1])
        with col_tr:
            translate_btn = st.button("🤖 重みを提案", key="translate_btn")
        with col_apply:
            apply_btn = st.button(
                "✅ この重みでマッチング",
                key="apply_intent",
                disabled="intent_weights" not in st.session_state,
            )
        with col_clear:
            if st.button("✕ リセット", key="clear_intent"):
                for k in ["intent_weights", "intent_explanation", "intent_text"]:
                    st.session_state.pop(k, None)
                for k in [k for k in st.session_state if k.startswith("v2_")]:
                    del st.session_state[k]
                st.rerun()

        if translate_btn and intent_text.strip():
            base_dw = st.session_state.get("v2_dimension_weights", [0.1] * 10)
            with st.spinner("Sonnet 4.6 が重みを計算中..."):
                new_weights, explanation = translate_intent(intent_text, base_dw)
            st.session_state["intent_weights"] = new_weights
            st.session_state["intent_explanation"] = explanation
            st.session_state["intent_text"] = intent_text

        if "intent_weights" in st.session_state:
            _DIM_LABELS_SHORT = [
                "立地",
                "ビジョン",
                "BM",
                "財務",
                "トレンド",
                "カルチャー",
                "キャリア",
                "WLB",
                "採用",
                "開発環境",
            ]
            iw = st.session_state["intent_weights"]
            iw_df = pd.DataFrame({"提案重み": iw}, index=_DIM_LABELS_SHORT)
            st.bar_chart(iw_df, horizontal=True, height=200)
            if st.session_state.get("intent_explanation"):
                st.caption(f"Sonnetの解釈: {st.session_state['intent_explanation']}")

        if apply_btn and "intent_weights" in st.session_state:
            with st.spinner("プロフィールに適用中..."):
                update_dimension_weights(_session_id, st.session_state["intent_weights"])
            for k in [k for k in st.session_state if k.startswith("v2_")]:
                del st.session_state[k]
            st.success("重みを適用しました。マッチングを再実行します。")
            st.rerun()

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
    # V1設定（従来のサイドバー項目をタブ内エクスパンダーに移動）
    with st.expander("⚙️ V1スコアリング設定", expanded=False):
        _v1_profile = load_profile(PROFILE_PATH)
        _v1_col1, _v1_col2 = st.columns(2)
        with _v1_col1:
            _w_tech = st.slider(
                "技術成長性",
                0.0,
                1.0,
                float(_v1_profile["weights"]["tech_growth"]),
                0.05,
                key="v1_w_tech",
            )
            _w_wlb = st.slider(
                "WLB", 0.0, 1.0, float(_v1_profile["weights"]["wlb"]), 0.05, key="v1_w_wlb"
            )
            _w_size = st.slider(
                "企業規模",
                0.0,
                1.0,
                float(_v1_profile["weights"]["company_size"]),
                0.05,
                key="v1_w_size",
            )
        with _v1_col2:
            _w_self = st.slider(
                "自社開発度",
                0.0,
                1.0,
                float(_v1_profile["weights"]["self_developed"]),
                0.05,
                key="v1_w_self",
            )
            _w_loc = st.slider(
                "立地", 0.0, 1.0, float(_v1_profile["weights"]["location"]), 0.05, key="v1_w_loc"
            )
        _v1_total = _w_tech + _w_wlb + _w_size + _w_self + _w_loc or 1.0
        _v1_norm = {
            "tech_growth": _w_tech / _v1_total,
            "wlb": _w_wlb / _v1_total,
            "company_size": _w_size / _v1_total,
            "self_developed": _w_self / _v1_total,
            "location": _w_loc / _v1_total,
        }
        st.caption(f"合計 {_v1_total:.2f} → 正規化済み")
        _v1_c1, _v1_c2 = st.columns(2)
        with _v1_c1:
            apply_filters = st.toggle("ハードフィルターを適用", value=True, key="v1_apply_filters")
            min_coverage = st.slider("データ充実度（最低項目数）", 0, 5, 0, key="v1_min_coverage")
        with _v1_c2:
            _v1_max_ot = st.number_input(
                "残業上限 (h/月)",
                0,
                80,
                int(_v1_profile["hard_filters"].get("max_overtime_hours", 30)),
                key="v1_max_ot",
            )
            _v1_min_score = st.number_input(
                "OpenWork スコア下限",
                0.0,
                5.0,
                float(_v1_profile["hard_filters"].get("min_openwork_score", 3.0)),
                step=0.1,
                key="v1_min_score",
            )
        _v1_req_raw = st.text_input(
            "必須スキル（カンマ区切り）",
            value=", ".join(_v1_profile.get("required_skills", [])),
            key="v1_req_skills",
        )
        _v1_bonus_raw = st.text_input(
            "ボーナススキル（カンマ区切り）",
            value=", ".join(_v1_profile.get("bonus_skills", [])),
            key="v1_bonus_skills",
        )
        _v1_prefs_raw = st.text_input(
            "希望都道府県（カンマ区切り）",
            value=", ".join(_v1_profile.get("preferred_prefectures", [])),
            key="v1_prefs",
        )

    active_profile = {
        **_v1_profile,
        "weights": _v1_norm,
        "hard_filters": {
            "max_overtime_hours": _v1_max_ot if apply_filters else None,
            "min_openwork_score": _v1_min_score if apply_filters else None,
        },
        "required_skills": [s.strip() for s in _v1_req_raw.split(",") if s.strip()],
        "bonus_skills": [s.strip() for s in _v1_bonus_raw.split(",") if s.strip()],
        "preferred_prefectures": [s.strip() for s in _v1_prefs_raw.split(",") if s.strip()],
        "qualifications": _v1_profile.get("qualifications", []),
    }

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
