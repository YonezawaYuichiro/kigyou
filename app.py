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

from backend.api.matching_engine import compute_matches
from backend.api.profile_manager import (
    load_or_create_profile,
    update_dimension_weights,
)
from backend.config import DATA_DIR, PROMPTS_DIR, settings
from backend.database import get_session
from backend.models import Company


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

# ─── サイドバー: V4 10次元重みスライダー ──────────────────────────────────
# V4: 全10次元スライダー（0〜10整数）、正規化後の値をスライダーに反映しない

_V4_DIM_NAMES = [
    "自社開発度",
    "新規事業・ビジョン",
    "使用技術の鮮度",
    "企業規模・安定性",
    "エンジニア成長支援",
    "社風・カルチャー",
    "キャリア成長",
    "WLB",
    "選考の技術評価度",
    "開発環境",
]
_V4_PRESETS: dict[str, list[int]] = {
    "技術成長": [8, 6, 8, 3, 7, 5, 8, 4, 6, 9],
    "WLB重視": [5, 4, 4, 5, 5, 7, 5, 10, 4, 4],
    "安定志向": [5, 5, 4, 9, 6, 6, 5, 6, 5, 4],
    "自社開発": [10, 8, 6, 3, 6, 7, 7, 5, 6, 7],
    "均等": [5] * 10,
}

if "sidebar_raw_weights" not in st.session_state:
    st.session_state["sidebar_raw_weights"] = [5] * 10

st.sidebar.header("🎚️ 重み設定（0〜10）")

# プリセットボタン（2列）
_preset_cols = st.sidebar.columns(2)
for _pi, (_pname, _pvals) in enumerate(_V4_PRESETS.items()):
    if _preset_cols[_pi % 2].button(_pname, use_container_width=True, key=f"preset_{_pi}"):
        st.session_state["sidebar_raw_weights"] = list(_pvals)
        st.rerun()

st.sidebar.divider()

# 全10次元スライダー（正規化前の値をsession_stateで保持）
_raw_weights: list[int] = []
for _di, _dname in enumerate(_V4_DIM_NAMES):
    _val = st.sidebar.slider(
        _dname,
        0,
        10,
        int(st.session_state["sidebar_raw_weights"][_di]),
        step=1,
        key=f"dim_slider_{_di}",
    )
    _raw_weights.append(_val)

_total_raw = sum(_raw_weights) or 10
_normalized_weights = [round(v / _total_raw, 4) for v in _raw_weights]
st.sidebar.caption(f"合計: {_total_raw} → 正規化して適用")

if st.sidebar.button("✅ 適用して再計算", type="primary"):
    update_dimension_weights(_session_id, _normalized_weights)
    st.session_state["sidebar_raw_weights"] = list(_raw_weights)
    for _k in [k for k in st.session_state if k.startswith("v2_")]:
        del st.session_state[_k]
    st.rerun()

st.sidebar.divider()

# 表示絞り込み（立地はハードフィルタに一本化）
st.sidebar.subheader("📍 表示絞り込み")
_V4_SHOW_ALL = st.sidebar.checkbox("全国表示（開発用）", value=False)
_V4_MIN_CONF = st.sidebar.slider("最低信頼度", 0.0, 1.0, 0.0, 0.05)

# V2プロフィール（マッチング用・封印中は軽量利用）
if "v2_profile_cache" not in st.session_state:
    st.session_state["v2_profile_cache"] = load_or_create_profile(_session_id)


# ─── メイン: V4 封印モード ────────────────────────────────────────────────
# V4: マッチングフローを一時封印し「企業分析」に集中する。
# _V4_SEALED = False に戻すと全タブが復活する。
_V4_SEALED = True
(tab_companies,) = st.tabs(["🏢 企業分析"])


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


# ─── 企業一覧ブラウザ（V4メイン機能） ─────────────────────────────────────


def _load_companies_for_browser(osaka_only: bool, min_confidence: float) -> list[dict]:
    """企業一覧をDBから取得してフィルタリングする。"""
    with get_session() as session:
        from sqlalchemy.orm import selectinload

        companies = (
            session.execute(
                select(Company).options(
                    selectinload(Company.metrics),
                    selectinload(Company.dimensions),
                    selectinload(Company.vector),
                )
            )
            .scalars()
            .all()
        )
        results = []
        for company in companies:
            dims = company.dimensions
            metrics = company.metrics
            vec = company.vector
            conf = dims.overall_confidence if dims else None
            if osaka_only and company.hq_prefecture not in ("大阪府", "京都府", "兵庫県"):
                continue
            if min_confidence > 0 and (conf is None or conf < min_confidence):
                continue
            results.append(
                {
                    "company_id": str(company.id),
                    "name": company.name,
                    "official_url": company.official_url,
                    "hq_prefecture": company.hq_prefecture,
                    "estimated_category": company.estimated_category,
                    "tech_stack": list(company.tech_stack or []),
                    "overall_confidence": conf,
                    "openwork_score": metrics.openwork_score if metrics else None,
                    "avg_overtime_hours": metrics.avg_overtime_hours if metrics else None,
                    "avg_annual_salary": metrics.avg_annual_salary if metrics else None,
                    "employee_count": metrics.employee_count if metrics else None,
                    "remote_work_policy": metrics.remote_work_policy if metrics else None,
                    "openwork_url": metrics.openwork_url if metrics else None,
                    "green_url": metrics.green_url if metrics else None,
                    "is_listed": metrics.is_listed if metrics else None,
                    "founded_year": metrics.founded_year if metrics else None,
                    "ow_score_morale": metrics.ow_score_morale if metrics else None,
                    "ow_score_openness": metrics.ow_score_openness if metrics else None,
                    "ow_score_growth": metrics.ow_score_growth if metrics else None,
                    # CompanyDimensions
                    "psychological_safety_score": dims.psychological_safety_score if dims else None,
                    "psychological_safety_evidence": dims.psychological_safety_evidence
                    if dims
                    else None,
                    "junior_authority_score": dims.junior_authority_score if dims else None,
                    "junior_authority_evidence": dims.junior_authority_evidence if dims else None,
                    "tech_env_evidence": dims.tech_env_evidence if dims else None,
                    "new_biz_policy_evidence": dims.new_biz_policy_evidence if dims else None,
                    "has_coding_test": dims.has_coding_test if dims else None,
                    "interviewer_type": dims.interviewer_type if dims else None,
                    "skill_support_score": dims.skill_support_score if dims else None,
                    "skill_support_items": list(dims.skill_support_items or []) if dims else None,
                    "evaluation_score": dims.evaluation_score if dims else None,
                    "evaluation_system_type": dims.evaluation_system_type if dims else None,
                    "career_track_diversity": dims.career_track_diversity if dims else None,
                    "tech_modernity_score": dims.tech_modernity_score if dims else None,
                    "infra_cloud_score": dims.infra_cloud_score if dims else None,
                    "cicd_maturity_score": dims.cicd_maturity_score if dims else None,
                    "tech_debt_culture_score": dims.tech_debt_culture_score if dims else None,
                    "competitive_advantage_score": dims.competitive_advantage_score
                    if dims
                    else None,
                    "competitive_advantage_evidence": dims.competitive_advantage_evidence
                    if dims
                    else None,
                    "hiring_difficulty_score": dims.hiring_difficulty_score if dims else None,
                    "megatrend_alignment": list(dims.megatrend_alignment or []) if dims else None,
                    # CompanyVector
                    "dim_scores": list(vec.dim_scores)
                    if vec and vec.dim_scores is not None
                    else None,
                }
            )
    results.sort(key=lambda x: x["overall_confidence"] or 0.0, reverse=True)
    return results


def _render_comparison_chart(names: list[str], rows: list[dict]) -> None:
    """選択した複数企業の10次元スコアを並べて比較する。"""
    selected = [r for r in rows if r["name"] in names]
    if not selected:
        return
    dim_names_v4 = [
        "自社開発度",
        "新規事業",
        "使用技術",
        "企業規模",
        "育成支援",
        "カルチャー",
        "キャリア",
        "WLB",
        "選考評価",
        "開発環境",
    ]
    comp_data: dict[str, list] = {}
    for c in selected:
        scores = c.get("dim_scores")
        comp_data[c["name"]] = (
            [round(s, 3) for s in scores] if scores and len(scores) == 10 else [0.0] * 10
        )
    comp_df = pd.DataFrame(comp_data, index=dim_names_v4)
    st.bar_chart(comp_df, height=300, horizontal=True)
    meta_rows = [
        {
            "企業名": c["name"],
            "信頼度": round(c["overall_confidence"] or 0, 2),
            "OW評価": c["openwork_score"] or "N/A",
            "残業h/月": c["avg_overtime_hours"] or "N/A",
            "採用難易度": round(c["hiring_difficulty_score"] or 0, 2),
        }
        for c in selected
    ]
    st.dataframe(pd.DataFrame(meta_rows), hide_index=True, use_container_width=True)


def _render_company_browser() -> None:
    """V4メイン: 企業一覧ブラウザ + 選択詳細 + 比較機能。"""
    if "company_browser_cache" not in st.session_state:
        with st.spinner("企業データを読み込み中..."):
            st.session_state["company_browser_cache"] = _load_companies_for_browser(
                osaka_only=not _V4_SHOW_ALL,
                min_confidence=_V4_MIN_CONF,
            )
    rows: list[dict] = st.session_state["company_browser_cache"]

    col_search, col_cat, col_sort = st.columns([3, 2, 2])
    with col_search:
        search_q = st.text_input("🔍 企業名・技術スタック検索", "")
    with col_cat:
        cat_filter = st.multiselect(
            "カテゴリ絞り込み",
            ["自社開発", "SIer", "スタートアップ", "受託開発", "その他"],
            default=[],
        )
    with col_sort:
        sort_by = st.selectbox("並び替え", ["信頼度", "OpenWork評価", "残業少ない順"])

    filtered = rows
    if search_q:
        q = search_q.lower()
        filtered = [
            r
            for r in filtered
            if q in r["name"].lower() or any(q in t.lower() for t in r["tech_stack"])
        ]
    if cat_filter:
        filtered = [r for r in filtered if r["estimated_category"] in cat_filter]
    if sort_by == "OpenWork評価":
        filtered = sorted(filtered, key=lambda x: x["openwork_score"] or 0, reverse=True)
    elif sort_by == "残業少ない順":
        filtered = sorted(filtered, key=lambda x: x["avg_overtime_hours"] or 999)

    st.caption(f"表示: **{len(filtered)}社** / 全{len(rows)}社")

    with st.expander("📊 企業比較（2〜3社選択）"):
        compare_names = st.multiselect(
            "比較する企業", [r["name"] for r in filtered], max_selections=3
        )
        if len(compare_names) >= 2:
            _render_comparison_chart(compare_names, filtered)

    df = pd.DataFrame(
        [
            {
                "企業名": r["name"],
                "都道府県": r["hq_prefecture"],
                "カテゴリ": r["estimated_category"],
                "信頼度": round(r["overall_confidence"] or 0, 2),
                "OW評価": r["openwork_score"] or "",
                "残業h": r["avg_overtime_hours"] or "",
            }
            for r in filtered
        ]
    )
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "信頼度": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.2f"),
        },
    )
    selected_idxs = event.selection.rows if event and event.selection else []
    if selected_idxs:
        selected_name = filtered[selected_idxs[0]]["name"]
        _render_v2_detail(selected_name, filtered, _normalized_weights)

    if st.button("🔄 データ再読み込み"):
        st.session_state.pop("company_browser_cache", None)
        st.rerun()


# ─── タブ: 企業分析 ───────────────────────────────────────────────────────
with tab_companies:
    _render_company_browser()
