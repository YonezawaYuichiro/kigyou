"""企業分析精度 評価ダッシュボード。

3層の評価フレームワークを可視化する:
  Layer 1: LLM抽出スコア vs OpenWork外部データの相関
  Layer 2: スコア分布（識別力・カテゴリ別比較）
  Layer 3: ゴールドスタンダード照合（有名企業の期待スコアとの一致率）

評価基準:
  データカバレッジ  ≥ 70%
  識別力 (std)     ≥ 0.12
  OpenWork相関 (r) ≥ 0.35
  ベンチマーク一致率 ≥ 70%
"""

import pandas as pd
import streamlit as st

from backend.seed.evaluator import run_all

st.set_page_config(page_title="精度評価", page_icon="📐", layout="wide")
st.title("📐 企業分析精度 評価ダッシュボード")
st.caption(
    "V4の10次元スコアが「正しいか」を3層で評価する。"
    "全指標がOKなら実装は信頼できる水準。NGなら該当フェーズを改善する。"
)

if st.button("🔄 評価を実行", type="primary"):
    with st.spinner("DB全企業を分析中..."):
        st.session_state["eval_report"] = run_all()

if "eval_report" not in st.session_state:
    st.info("「評価を実行」ボタンを押してください。")
    st.stop()

report = st.session_state["eval_report"]
total = report["total_companies"]

# ─── 総合サマリー ──────────────────────────────────────────────────────────
st.subheader("📊 総合サマリー")
col1, col2, col3, col4 = st.columns(4)

coverage_ok = sum(1 for c in report["coverage"] if c.get("status") == "✅ OK")
dist_ok = sum(1 for d in report["distribution"] if d.get("status") == "✅ OK")
corr_ok = sum(1 for v in report["correlation"].values() if v.get("status") == "✅ OK")
bm = report["benchmark"]

with col1:
    st.metric("対象企業数", f"{total}社")
with col2:
    st.metric(
        "データカバレッジ",
        f"{coverage_ok}/10次元",
        help="各次元でスコアがある企業が70%以上の次元数",
    )
with col3:
    st.metric(
        "OpenWork相関", f"{corr_ok}/3検証", help="LLM抽出スコアとOpenWorkの相関がr≥0.35の検証数"
    )
with col4:
    bm_pct = f"{bm['pass_rate']:.0%}"
    bm_status = "✅" if bm["pass_rate"] >= 0.70 else "⚠️"
    st.metric("ベンチマーク一致率", f"{bm_status} {bm_pct}")

st.divider()

# ─── Layer 2: データカバレッジ ─────────────────────────────────────────────
st.subheader("Layer 2: データカバレッジ")
cov_df = pd.DataFrame(
    [
        {
            "次元": f"dim[{c['dim']}] {c['name']}",
            "カバレッジ(%)": c.get("coverage_pct", 0),
            "判定": c.get("status", "—"),
        }
        for c in report["coverage"]
        if "error" not in c
    ]
)
st.dataframe(
    cov_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "カバレッジ(%)": st.column_config.ProgressColumn(
            min_value=0, max_value=100, format="%.1f%%"
        ),
    },
)

st.divider()

# ─── Layer 2: スコア識別力 ─────────────────────────────────────────────────
st.subheader("Layer 2: スコア識別力（標準偏差）")
st.caption("std ≥ 0.12 なら識別力あり。0.5付近に集中していないか確認する。")

dist_rows = [d for d in report["distribution"] if "error" not in d]
if dist_rows:
    dist_df = pd.DataFrame(
        [
            {
                "次元": f"dim[{d['dim']}] {d['name']}",
                "平均": d["mean"],
                "std": d["std"],
                "最小": d["min"],
                "最大": d["max"],
                "N": d["n"],
                "判定": d["status"],
            }
            for d in dist_rows
        ]
    )
    st.dataframe(dist_df, use_container_width=True, hide_index=True)

    # 次元別スコア分布バーチャート（mean ± std の視覚化）
    means = [d["mean"] for d in dist_rows]
    names = [f"dim[{d['dim']}]" for d in dist_rows]
    chart_df = pd.DataFrame({"平均スコア": means}, index=names)
    st.bar_chart(chart_df, height=250)

st.divider()

# ─── Layer 2: カテゴリ別スコア比較 ────────────────────────────────────────
st.subheader("Layer 2: カテゴリ別スコア比較")

cat_data = report["category"]
sanity = cat_data.get("_sanity_dim0", {})
status_icon = "✅" if "✅" in sanity.get("status", "") else "⚠️"
st.caption(f"{status_icon} dim[0] サニティチェック: 自社開発 > SIer　→　{sanity.get('values', {})}")

target_cats = ["自社開発", "SIer", "スタートアップ", "受託開発"]
avail_cats = [c for c in target_cats if c in cat_data and cat_data[c].get("dim_means")]
if avail_cats:
    dim_names = [f"dim[{i}]" for i in range(10)]
    cat_df = pd.DataFrame(
        {cat: cat_data[cat]["dim_means"] for cat in avail_cats},
        index=dim_names,
    )
    st.bar_chart(cat_df, height=300, horizontal=True)

st.divider()

# ─── Layer 1: LLM vs OpenWork 相関 ────────────────────────────────────────
st.subheader("Layer 1: LLM抽出スコア vs OpenWork 相関")
st.caption(
    "外部の客観データ（OpenWork口コミ集約スコア）とLLM抽出値の相関を測定。"
    "r ≥ 0.35 なら「LLMの判断がある程度的中している」と解釈できる。"
)

corr_data = report["correlation"]
corr_rows = []
for result in corr_data.values():
    if "error" in result:
        corr_rows.append(
            {
                "検証内容": result["label"],
                "相関係数 r": "—",
                "p値": "—",
                "N": "—",
                "判定": f"⚠️ {result['error']}",
            }
        )
    else:
        corr_rows.append(
            {
                "検証内容": result["label"],
                "相関係数 r": result["r"],
                "p値": result["p"],
                "N": result["n"],
                "判定": result["status"],
            }
        )
corr_df = pd.DataFrame(corr_rows)
st.dataframe(corr_df, use_container_width=True, hide_index=True)

st.divider()

# ─── Layer 3: ゴールドスタンダード照合 ────────────────────────────────────
st.subheader("Layer 3: ゴールドスタンダード照合")
st.caption(
    f"ベンチマーク企業 {len(bm['details'])}社の「期待スコア」と実際のスコアを比較。"
    f"一致率: **{bm['pass_rate']:.0%}** ({bm['passed']}/{bm['total']})　{bm['status']}"
)

for detail in bm["details"]:
    if "pass_rate" not in detail:
        st.write(f"⚪ {detail.get('name')}: {detail.get('status')}")
        continue

    icon = "✅" if detail["pass_rate"] >= 0.70 else "⚠️"
    with st.expander(f"{icon} {detail['name']} — 一致率 {detail['pass_rate']:.0%}"):
        if detail.get("notes"):
            st.caption(detail["notes"])
        if detail.get("items"):
            items_df = pd.DataFrame(
                [
                    {
                        "次元": f"dim[{it['dim']}] {it['name']}",
                        "期待": "↑ 高い" if it["expect"] == "high" else "↓ 低い",
                        "実際のスコア": it["actual"],
                        "判定": "✅" if it["ok"] else "❌",
                    }
                    for it in detail["items"]
                ]
            )
            st.dataframe(items_df, hide_index=True, use_container_width=True)

# ─── 評価基準の説明 ────────────────────────────────────────────────────────
with st.expander("📋 評価基準の読み方"):
    st.markdown("""
| 指標 | 合格ライン | 意味 |
|------|-----------|------|
| データカバレッジ | 各次元 ≥ 70% | 企業の7割以上でスコアが存在する |
| 識別力 (std) | ≥ 0.12 | スコアが0.5付近に集中していない |
| OpenWork相関 (r) | ≥ 0.35 | LLMの判断がOpenWrokと一定以上一致 |
| ベンチマーク一致率 | ≥ 70% | 有名企業の期待スコアと実際が一致 |

**全部OKなら** → V4の分析精度は信頼できる。実運用に進んでよい。

**カバレッジがNG** → データ収集フェーズ（Phase B: blog_analyzer / github_org_analyzer）を実行して情報を補強する。

**識別力がNG** → 特定次元のスコア計算式を見直す（vector_builder.py）。

**相関がNG** → dimensions_extractor.py のプロンプトを見直す。

**ベンチマークがNG** → その企業がDBに未登録 or スコア計算ロジックの見直しが必要。
""")
