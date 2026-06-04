# 実装履歴ログ

---

## 2026-06-04: Phase 5a 本検証 — パイプライン実行・配管テスト・バグ修正

- **実装内容**:
  - `backend/seed/master_data.py` — `github_activity_score` を feature_definition に追加（59件）
  - `backend/seed/dimensions_extractor.py` — Haiku プロンプト4箇所を修正
    - `不明=2.5` / `言及なし=0.5` → `証拠なし=null。証拠なき場合はフィールドを省略`
    - 対象: `culture_org.psychological_safety_score`, `career_hr.junior_authority_score`,
      `vision_strategy.new_biz_policy_score`, `tech_env` 全5スコア項目
  - `tests/seed/test_feature_writer_v5.py` — **新規**: `_convert()` 単体テスト 23件
  - `backend/seed/validate_pipeline_v5.py` — **新規**: V2 dims → write_from_star 配管テストスクリプト
  - `backend/seed/feature_writer_v5.py` — `_upsert_feature` の戻り値を bool に変更（カウントバグ修正）

- **実際に走らせて発見したバグ（走らせなければ永久に気づかなかった）**:
  1. `github_activity_score` が feature_definition に未登録 → FK 違反で write_github_stats が失敗
  2. `write_from_star` の written カウントが保護スキップを含めてカウントしていた（`_upsert_feature` が None 返し）

- **V2 Haiku 抽出 vs V5 手入力 差分（サイボウズ）**:
  - psychological_safety: V2=2.5（デフォルト） vs V5=5.0（手入力） → 差 -2.5
  - young_autonomy: V2=2.5 vs V5=4.0 → 差 -1.5
  - competitive_advantage: V2=4.0 vs V5=4.0 → 一致
  - evaluation_score=0.0 も「証拠なし」デフォルト → pct_to_scale5 で 1.0（最低）に変換 → 誤値

- **設計上の結論**:
  - Haiku は主観項目（psychological_safety, young_autonomy）を証拠なしで中間値（2.5）で埋める
  - プロンプト修正（証拠なし→null）で次回以降は改善される
  - 客観項目（salary, listing, rnd, github）はパイプラインで素直に入る
  - 主観項目は自動化の限界: 手検証 or confidence ゲートで低品質時は非表示が正しい設計

- **動作確認**: `pytest tests/ -q` → 44/44 passed。`ruff check` → All checks passed
  - validate_pipeline_v5 実行結果: Cybozu → star 1/8 件書き込み（7件 official/review 保護、配管確認OK）

## 2026-06-04: Phase 5b — 2社目（freee/PFN）投入・ランキング検証・sanity check

- **実装内容**:
  - `backend/seed/phase1_freee.py` — **新規**: freee株式会社データ投入（53件・正規化済）
  - `backend/seed/phase1_pfn.py` — **新規**: Preferred Networks株式会社データ投入（新規INSERT・51件）
    - company INSERT 時に hq_prefecture / estimated_category / tech_stack / hiring_roles / llm_confidence / release_flag が NOT NULL 制約で必要だった

- **ランキング結果（sanity check）**:
  - 事前宣言: PFN > サイボウズ > freee（MLOps/R&D重視ペルソナのため）
  - 実結果: freee(84.8) > サイボウズ(84.0) > PFN(77.7)
  - **差分の原因**: phase3_user_prefs.py が f:mlops_maturity 等に desired_value=4 を設定
    → 距離ペナルティにより PFN(5/5) が subscore=0.75、freee(4/5) が 1.00 になる
    → 意図は「4以上が良い」→ desired_min=4 を使うべきだった（desired_value は「ちょうど4が好み」）

- **非SaaSスキーマ検証（Phase 5b の主目的）**:
  - patent_count（直接値）: PFN 200件 → normalized=0.40 ✓
  - f:hw_sw_integration（scale5）: PFN 5/5 → normalized=1.00 ✓
  - tech_tag hardware カテゴリ: NVIDIA GPU / エッジAI / ロボティクス 投入 ✓
  - スキーマ変更なしで非SaaSドメイン対応完了 ✓

- **残課題**: phase3_user_prefs.py の f:mlops_maturity / f:data_platform_maturity を
  desired_value=4 → desired_min=4 に変更すると PFN が期待通り上位になる（Phase 5c で修正可）

## 2026-06-04: Phase 5a — 自動パイプライン → company_feature (V5) 対応

- **実装内容**:
  - `backend/seed/feature_writer_v5.py` — **新規**: Haiku 抽出結果を V5 company_feature に書き込むユーティリティ
    - `write_from_star()`: Haiku star dict → 16 feature_key へ upsert
    - `write_from_circle()`: Haiku circle dict → 8 feature_key へ upsert（overtime_hours など2スロット対応）
    - `write_github_stats()`: GitHub スコア (0-1) → github_activity_score (0-100)
    - `write_blog_stats()`: ブログ更新頻度 → oss_blog_freq
  - `backend/seed/dimensions_extractor.py` — `_process_single_company()` 末尾に V5 書き込み + 正規化を追加
  - `backend/seed/deep_dive.py` — Step 2 (GitHub) / Step 3 (Blog) 後に V5 書き込み追加、Step 5 前に normalize_all 追加

- **設計判断**:
  - V2 テーブル（CompanyDimensions）への書き込みはそのまま維持（並列書き込み）
  - 手入力データ（source_type='official'/'review'）は自動抽出で上書きしない（保護）
  - Haiku の出力スケール差を吸収: scale5_direct(0-5) / pct_to_scale5(0-1→1-5) / pct_to_pct100(0-1→%)
  - `cicd_maturity_score` → `f:mlops_maturity` は最近接マッピング（V5 master に f:cicd_maturity なし）
  - `has_patent`（bool）→ スキップ（V5 は patent_count/direct を使う。Haiku から count が取れない）
  - `hw_sw_integration`（bool）→ `f:hw_sw_integration`（scale5）: True→5.0 / False→1.0

- **動作確認**: `pytest tests/ -v` → 21/21 passed。`ruff check` → All checks passed

## 2026-06-04: Phase 5 前提 — normalizer direction 不変条件テスト

- **実装内容**:
  - `tests/seed/test_normalizer.py` — **新規**: `_normalize()` の direction 反転を 12ケースで検証
    - high_good: 高値 → 高スコア
    - low_good: 低値 → 高スコア（反転）。サイボウズ残業 12h → 0.85 も検証
    - クランプ（範囲外値）、neutral、min=max のエッジケース

- **設計判断**:
  - `value_normalized` は常に「1.0 = 最も望ましい」の不変条件を normalizer.py L83-84 が保証
  - この前提がないと "_compute_subscore で desired_min/max → value_normalized をそのまま" という設計が符号逆転を引き起こす
  - 84.0点の WLB・安定性寄与が正符号で計算されていることを単体テストで確認

- **動作確認**: `pytest tests/seed/test_normalizer.py -v` → 12/12 passed。`ruff check` もクリア

---

## 2026-06-04: V5 gap 対応 — field_mapping.md / 1.基本情報.txt / schema.sql の未実装項目埋め

- **実装内容**:
  - `backend/models.py` — Company に `listing_type` / `founded_year` / `target_market` 追加
  - `alembic/versions/d2e3f4a5b6c7_v5_company_extra_columns.py` — **新規** migration
    - 既存 head `725ed1d875c4`（fix_constraints）の後続に接続（down_revision 修正で解決）
  - `backend/seed/master_data.py` — feature_definition を 50件 → **58件** に拡充
    - `patent_count` / `business_domain` / `target_market` / `organization_type`
    - `has_fixed_ot` / `fixed_ot_hours` / `placement_guaranteed` / `f:global_expansion`
  - `backend/seed/phase1_cybozu.py` — サイボウズデータを 49件 → **55件** に拡充
    - company.listing_type="東証プライム" / founded_year=1997 / target_market="BtoB"
    - 新feature: organization_type=4 / has_fixed_ot=1 / fixed_ot_hours=30 / f:global_expansion=3 等

- **設計判断**:
  - `listing_type` は TEXT（ENUM にしない）— 東証プライム/スタンダード/グロース/未上場/グループ等多様なため
  - `target_market` は company 直接列（簡易版）。将来的にタグ化（business_domain タグと同様）
  - `business_domain` / `target_market` / `charging_model` は tag型 → company_feature で数値保存しない。completeness = 55/58 (95%) は正常
  - alembic "Multiple head revisions" は `725ed1d875c4` という既存 fix migration が存在したため。down_revision を修正して解決

- **動作確認**:
  - feature_definition: 58件・scale5全件rubric付き
  - company_completeness: 55/58 (95%)
  - マッチングスコア: 84.0点 維持（新項目はuser_preferenceに未登録のため加重平均に未反映）

---

## 2026-06-03: V5 Phase 4 — マッチングスコア計算エンジン

- **実装内容**:
  - `backend/api/matching_engine_v5.py` — **新規**: feature_key EAV ベース加重平均マッチングエンジン
    - V2 pgvector エンジン (matching_engine.py) と並存。既存コードを破壊しない
    - ハードフィルタ: raw値で判定（overtime_hours ≤ 30 / remote_rate ≥ 50%）
    - サブスコア: desired_value あり → 距離ペナルティ / なし → value_normalized をそのまま使用
    - bool: 一致=1.0 / 不一致=0.0
    - 加重平均: weight = user_preference.weight ?? feature_definition.default_weight
    - 欠損データは分子・分母ともに除外（0扱いしない）
    - 寄与TOP3 / BOTTOM3 で説明可能性を確保
    - CLI: `python -m backend.api.matching_engine_v5 --user-id UUID --company-id UUID`

- **設計判断**:
  - tag/onehot はマッチング時の Jaccard 計算に回す（Phase 5）
  - desired_min/max soft preference は value_normalized をそのまま使用（direction 考慮済みのため）
  - Windows cp932 対応で出力に ASCII 文字のみ使用（≥→>= / ≤→<= / ✓→[OK]）

- **動作確認**:
  - サイボウズ × ユーザー: **総合スコア 84.0点**
  - ハードフィルタ: [OK] 通過（残業12h≤30 / リモート85%≥50%）
  - TOP3: f:data_platform_maturity / f:mlops_maturity / f:tech_debt_culture（全て1.00×w=1.5）
  - BOTTOM3: has_housing_support=0.00 / oss_blog_freq=0.16 / diversity=0.75（低weightで影響小）

- **残課題（Phase 5）**:
  - tag/onehot の Jaccard マッチング実装
  - 2社目以降を追加してランキングを検証
  - industry_master / tech_tag マスタを拡充

---

## 2026-06-03: V5 Phase 3 — ユーザー希望条件 (user_preference) 投入

- **実装内容**:
  - `backend/seed/phase3_user_prefs.py` — **新規**: my_profile.json → user_preference 変換
    - UserProfile を session_id="phase3_yuichiro" で生成（tech_skills / target_roles / qualifications 保存）
    - user_preference: 39件投入（ハードフィルタ2件: overtime_hours≤30 / remote_rate≥50%）
    - weight を old_weight（tech_growth/wlb/self_developed）から feature_key 粒度にマッピング
    - 高優先: f:mlops_maturity / f:data_platform_maturity / f:tech_debt_culture → w=1.5
    - 中優先: f:young_autonomy / f:skill_support_quality / rnd_ratio → w=1.2〜1.3
    - 低優先: has_housing_support / diversity → w=0.2

- **設計判断**:
  - weight=None の項目は feature_definition.default_weight を自動継承（設定不要項目は省略）
  - desired_min/max で範囲指定（例: turnover_3yr ≤ 20%、salary_age30 ≥ 500万）
  - tech_stack 希望（Python/PyTorch/AWS）はUserProfile.tech_skillsに保存。Jaccard類似度はPhase 4マッチングエンジンで処理

- **動作確認**: ruff OK / 39件投入・0件スキップ

---

## 2026-06-03: V5 Phase 2 — 特徴量正規化パイプライン

- **実装内容**:
  - `backend/seed/normalizer.py` — **新規**: value_normalized 計算バッチ
    - `direct` / `scale5` / `bool` 対象。`tag` / `onehot` はマッチング時に Jaccard 等で処理
    - `high_good` / `neutral`: `(v - min) / (max - min)`、クランプ [0, 1]
    - `low_good`: `1 - (v - min) / (max - min)`（残業・離職率・exec_field_distance 等）
    - `has_official_actual=True`: `value_actual` 優先、なければ `value_official`
    - `--company-id UUID` オプションで1社指定可能

- **設計判断**:
  - v1は feature_definition.value_min/max による固定正規化。業界内相対正規化はPhase 5以降
  - 再実行時は既存 value_normalized を上書きするため、データ更新後にそのまま再実行可能

- **動作確認**:
  - サイボウズ 49件更新・0件スキップ
  - 心理的安全性・ボトムアップ = 1.00 / リモート実態85% = 0.85 / 残業12h(low_good) = 0.85 など直感的な結果を確認

- **残課題**:
  - Phase 3: UserPreference 入力（希望値 + weight + hard_filter）
  - Phase 4: マッチングスコア計算

---

## 2026-06-03: V5 Phase 0 — 3層DB構成スキーマ確定 & 特徴量カタログ整備

- **実装内容**:
  - `backend/models.py` — 新テーブル12本追加 + Company に2カラム追加
    - 原本層: `JobRole` / `Office` / `IndustryMaster` / `CompanyIndustry` / `TechTag` / `CompanyTech` / `SalaryRecord` / `RevenueRecord` / `CompanyText`
    - 特徴量層: `FeatureDefinition` / `CompanyFeature`（縦持ちEAV）
    - ユーザー層: `UserPreference`
    - `Company` に `has_relocation`（転勤有無）/ `engineer_count` を追加
  - `alembic/versions/c1d2e3f4a5b6_v5_two_layer.py` — マイグレーション
    - `NULLS NOT DISTINCT` index（PG16）でrole_id=NULL全社行の重複防止
    - `BigInteger` で revenue/operating_profit（Integer上限~21.4億円超え対策）
    - `CheckConstraint` で source_type/method/direction のタイポをDB側で防止
    - `CREATE VIEW company_completeness`（NULL非0扱いのペナルティ可視化）
  - `backend/seed/master_data.py` — **新規**: マスタデータ投入スクリプト
    - industry_master: 20業界カテゴリ
    - tech_tag: 85タグ（language/framework/cloud/mlops/data/devops/hardware）
    - feature_definition: 50項目（★=1.0/〇=0.5/再考✕=0.2、scale5全件にrubric付与）

- **設計判断**:
  - CompanyDimensions/CompanyMetrics は漸進廃止方針（既存コード稼働中のため即時DROP不可）
  - feature_definitionの feature_key は `f:` prefix を scale5 の識別に使用（field_mapping.md の正本に従う）
  - `NULLS NOT DISTINCT` は PG15+ 機能。プロジェクトの Docker イメージ `pgvector/pgvector:pg16` で使用可能
  - value_normalized の正規化基準は v1 固定 min/max。相対正規化（業界内）は Phase 5 以降

- **動作確認**:
  - `ruff check` ALL PASS（3ファイル）
  - `pytest` 26 passed（既存テスト全通過）
  - `alembic upgrade head` — Docker 未起動のため未実行。Docker Desktop 起動後に実行すること

- **残課題**:
  - Docker Desktop 起動 → `alembic upgrade head` → `python -m backend.seed.master_data`
  - Phase 1: 実在1社を原本層に手動入力し `SELECT * FROM company_completeness` で網羅率確認
  - feature_definition の追加・補正はPhase 1埋め後に判明する（過不足は実際に埋めて初めて分かる）

---

## 2026-05-28: V4 企業分析精度評価システム

- **実装内容**:
  - `pyproject.toml` — `scipy>=1.13.0` を dependencies に追加
  - `benchmarks/known_companies.json` — **新規**: ゴールドスタンダード企業10社（サイボウズ/freee/NTTデータ/楽天/パナソニック/LINE/メルカリ/富士通/Sansan/SmartHR）
  - `backend/seed/evaluator.py` — **新規**: 3層評価フレームワーク
    - Layer 1: LLM抽出スコア vs OpenWork外部データのSpearman相関（dim[5]/dim[6]/dim[4]）
    - Layer 2: データカバレッジ率 / スコア識別力（std） / カテゴリ別比較
    - Layer 3: ゴールドスタンダード照合（期待スコアとの一致率）
  - `pages/3_evaluation.py` — **新規**: Streamlit評価ダッシュボード（4指標サマリー・相関表・分布グラフ・ベンチマーク照合）

- **設計判断**:
  - 評価は「自動相関（外部データがあるもの）」と「手動ベンチマーク（期待値を人が定義）」の2種類に分類。財務や事業性次元は外部正解データがないため手動定義のみ
  - Spearman相関を選択（値の分布が正規分布でない前提のため、Pearsonより頑健）
  - ゴールドスタンダード照合の合格閾値: 期待が高い次元 > 0.55、期待が低い次元 < 0.45（絶対値でなく方向性のみ確認）
  - benchmarks/ ディレクトリは git 追跡対象（センシティブ情報なし）

- **動作確認**: ruff check ALL PASS

- **残課題**: 
  - 50社確定・V4ベクトル計算後に `python -m backend.seed.evaluator` を実行して初回ベースライン測定
  - DBにサイボウズ・freee等の有名企業が入っていない場合は「⚪ 未登録」扱い（スキップ）

---

## 2026-05-28: V4 Phase A〜F — 10次元再定義・企業分析基盤整備・UI封印

- **実装内容**:
  - `backend/models.py` — CompanyDimensionsに `hiring_difficulty_score` 追加、CompanyVectorコメント更新
  - `alembic/versions/b1c2d3e4f5a6_v4_add_hiring_difficulty.py` — マイグレーション
  - `backend/seed/vector_builder.py` — 10次元を新卒エンジニア特化に全面再定義（v4.0）。立地をdim[0]から除外してhard_filterに一本化。自社開発度/新規事業/使用技術鮮度/事業安定性/育成投資/カルチャー/キャリア/WLB/選考技術評価/開発環境。`compute_hiring_difficulty()` 追加
  - `backend/seed/priority_scorer.py` — **新規**: 50社選定スコアリング（大阪+自社開発+データギャップ）
  - `backend/seed/blog_analyzer.py` — **新規**: Zenn/Qiita技術ブログ自動検出・定量化
  - `backend/seed/github_org_analyzer.py` — **新規**: GitHub Organization解析（言語分布・活動度）
  - `backend/seed/scoring_rubric.py` — **新規**: チェックリスト方式スコアリング定義（カルチャー/育成/若手裁量/開発環境）
  - `backend/seed/dimensions_extractor.py` — `compute_objective_confidence_from_star()` 追加。Haiku自己申告→証拠充実度からの算術計算に変更
  - `app.py` — V4封印モード（_V4_SEALED=True）。サイドバーを全10次元スライダー（0〜10整数・プリセット5種）に全面改修。企業一覧ブラウザ・比較機能・大阪デフォルト表示を新設

- **設計判断**:
  - 立地は10次元から除外してhard_filter（preferred_prefectures）に一本化。10次元はすべて「ユーザーが重みを変えたい軸」として再定義
  - 技術発信活動（ブログ/GitHub）は独立次元にせず、データ収集後にdim[2]/dim[9]の証拠として活用
  - hiring_difficulty_scoreは10次元の外に独立して realistic_score の割引計算に使用
  - スライダー正規化後の値をsession_stateに戻さない（0〜10のまま維持）ことで操作性を改善
  - blog_analyzer / github_org_analyzerはPhase E2（50社確定後）に実行予定

- **動作確認**: ruff check ALL PASS（全9ファイル）

- **残課題**: Phase E2（50社選定→データ再収集→ベクトル再計算）、Phase C/D のdimensions_extractor完全統合（現在は信頼度客観化のみ）

---

## 2026-05-28: V3 UI改善・サイドバー統合・評価基準刷新（V3完了）

- **実装内容**:
  - `app.py` — サイドバーをV2 UserProfile基準に全面改修（V1 my_profile.json依存を廃止）。クイック設定（勤務地・カテゴリ・リモート・重みスライダー）を「✅ 適用して再計算」ボタンで一括反映。V1コントロールをtab_v1内に移動
  - `app.py` — `_render_v2_table()` からOW評価・残業h・年収(万)列を削除（平等にかからない情報のため）
  - `app.py` — `_render_v2_detail()` を動的表示に変更（Noneのメトリクスは完全非表示、`st.metric` でリスト化）
  - `app.py` — `_get_dim_evidence()` 追加：専用証拠フィールドがない次元（立地・WLB等）は手元データから証拠テキストを合成
  - `app.py` — `_render_xai_panel()` の横棒グラフを `st.bar_chart` → `st.dataframe + ProgressColumn` に変更（スクロールリサイズ問題を回避）
  - `app.py` — `_render_market_position()` を固定参照分布（理論的正規分布）ベースに変更（DB登録者数依存を廃止）
  - `pages/1_profile_setup.py` — Step 0（現在の設定確認ページ）追加。初期wizard_stepを0に変更
  - `backend/api/matching_engine.py` — `_normalize_pref()` 追加（「大阪府」と「大阪」を同一視）、`_build_filter_set()` に `preferred_categories` フィルタ追加
  - `backend/api/profile_manager.py` — `update_hard_constraints()` 追加（サイドバー用、hard_constraintsのみ直接上書き）
  - `backend/api/profile_manager.py` — 実務力評価を6因子ルーブリックに刷新（F1:実装量, F2:実装品質, F3:技術幅, F4:技術深度, F5:資格, F6:外部発信）。LLM出力を6スコアに限定してPython側で機械計算（LLMのホリスティック判断を排除）

- **設計判断**:
  - サイドバー/profile_setupがV1(my_profile.json)とV2(UserProfile DB)に分裂していた根本問題を解消。V2 UserProfileを唯一のソースとして統合
  - 「出来レース」感を排除するため、Dreyfus習得モデル・IPA ITスキル標準に基づく5+1因子ルーブリックへ。採点は `(F1+F2+F3+F4+F5+F6)/24` のPython算術計算で決定論的にする
  - パーセンタイル表示を「登録ユーザーX人中」→「新卒学生推定分布」に変更。固定参照分布なのでユーザー数に関わらず意味のある比較が可能
  - グラフのスクロールリサイズはStreamlit既知バグ。`st.dataframe + ProgressColumn` は静的レンダリングのため影響なし

- **動作確認**: ruff check ALL PASS（`ruff check app.py backend/api/matching_engine.py backend/api/profile_manager.py pages/1_profile_setup.py`）

- **残課題**: V3完了。V4方針はユーザーと別途決定

---

## 2026-05-28: V3 Phase 6 — FastAPI化

- **実装内容**:
  - `backend/api/schemas.py` — Pydantic v2 リクエスト/レスポンス型 （ProfileRequest / ProfileResponse / CompanyListItem / CompanyListResponse / CompanyDimensionsSchema / CompanyDetailResponse / MatchItem / MatchResponse）
  - `backend/api/routes.py` — FastAPI APIRouter。5エンドポイント実装（health / profile / matches / companies / companies/{id}）
  - `backend/main.py` — FastAPIアプリ本体。CORSMiddleware（localhost:8501）+ ルーターマウント
  - `pyproject.toml` — fastapi>=0.115.0 / uvicorn[standard]>=0.32.0 を dependencies に追加

- **設計判断**:
  - Streamlit側のリファクタ（requestsでAPIを叩く移行）は「段階的」とし今回は行わない。FastAPI側だけ整備して将来の移行に備える
  - `GET /api/v2/companies` は全件取得後Python側でフィルタリング（pgvectorの型制約のためSQL側での絞り込みが難しい項目があるため）
  - セッションIDはクエリパラメータで受け取る（ヘッダーだとSwagger UIでのテストが面倒なため）

- **動作確認**: ruff ALL PASS、pytest 26 passed、`from backend.main import app` でルート5件確認

- **残課題**: なし（V3全Phase完了）

---

## 2026-05-28: V3 Phase 5 — GitHub解析

- **実装内容**:
  - `backend/api/github_analyzer.py` — 新規作成。GitHub API（認証不要）で公開リポジトリを解析し `project_complexity_score`・使用言語・commit頻度・OSSコントリビュートを定量化
  - `backend/api/profile_manager.py` — `_assess_tech_level()` に `github_summary` 引数追加（プロンプトに追加セクションとして挿入）、`save_profile()` に同パラメータ追加
  - `pages/1_profile_setup.py` — Step 1 に GitHub URL 入力欄 + 「🔍 解析」ボタンを追加。Step 4 の保存時に `github_summary` を渡す

- **設計判断**:
  - DB カラム追加なし（GitHub URL は session_state のみに保持）。Phase 5 の目的はttech_level精度向上なので URL の永続化は不要と判断
  - GitHub API は最大 2 リクエスト（repos + events）に制限し、レート制限 60req/h に配慮
  - 解析エラーは例外を raise せず `error` キーで返す → UI が graceful に表示
  - `project_complexity_score` はスター数（log scale）・言語数・複雑トピック・リポジトリ規模の加算方式

- **動作確認**: ruff check ALL PASS、pytest 26 passed

- **残課題**: V3 Phase 6（FastAPI化）

---

## 2026-05-28: V3 Phase 4 — 意図翻訳エンジン

- **実装内容**:
  - `backend/api/intent_translator.py` — 新規作成。`translate_intent(free_text, base_weights)` でSonnet 4.6が10次元重みに変換
  - `backend/api/profile_manager.py` — `update_dimension_weights(session_id, weights)` を追加（dimension_weightsのみ直接上書き）
  - `app.py` — V2「理想企業」タブに「💬 自然言語で重みを調整」expanderを追加

- **設計判断**:
  - 提案→確認→適用の3段階UI: Sonnetの提案を一旦バーチャートで見せてから「✅この重みでマッチング」で確定
  - 適用時は `update_dimension_weights()` でDBを直接上書き（`save_profile()` を通すとdimension_weightsが再計算されてしまうため）
  - 「✕ リセット」でintent_weightsと再計算キャッシュを両方削除してプロフィール設定に戻せる
  - `disabled="intent_weights" not in st.session_state` で「翻訳前に適用」を防止

- **動作確認**: ruff check ALL PASS、pytest 26 passed

- **残課題**: V3 Phase 5（GitHub解析）

---

## 2026-05-28: V3 Phase 3 — XAI（マッチング説明）

- **実装内容**:
  - `backend/api/matching_engine.py` — `_enrich_results()` に dim_scores (CompanyVector) + 3種の証拠テキスト (psychological_safety_evidence / junior_authority_evidence / new_biz_policy_evidence) / tech_demand を追加。`compute_matches()` が `dimension_weights` も返すよう変更
  - `app.py` — `_render_xai_panel()` 新規追加（スコア差分3列・企業スコアvsユーザー重み比較チャート・貢献度トップ3次元expander）。`_render_v2_detail()` を改修してXAIパネル埋め込み。`_run_v2_matches()` で `v2_dimension_weights` をsession_stateにキャッシュ

- **設計判断**:
  - `contributions[i] = dim_scores[i] × dimension_weights[i]` の降順で「なぜこの企業か」を説明。コサイン類似度の内積分解と対応
  - ideal vs realistic の差分 (`gap`) が 0.05 未満なら「技術ギャップなし」と表示し、不安を与えない
  - 4次元のみ証拠テキストあり（ビジョン/カルチャー/キャリア/開発環境）。それ以外はキャプション補足
  - plotly未導入のため radar chart → st.bar_chart 横棒グラフ（2系列比較）で代替

- **動作確認**: ruff check ALL PASS、pytest 26 passed

- **残課題**: V3 Phase 4（意図翻訳エンジン）

---

## 2026-05-28: V3 Phase 2 — 企業情報閲覧ページ

- **実装内容**:
  - `pages/2_company_explorer.py` — 新規作成

- **設計判断**:
  - フィルター（カテゴリ/都道府県/リモート/信頼度/従業員規模）はサイドバーに集約
  - テキスト検索は企業名と技術スタックの両方を対象にする
  - confidence < 0.4 の企業は⚠️バッジ + 不足フィールド一覧を表示
  - 10次元スコアは横棒グラフで表示（plotlyは未導入のためst.bar_chartを使用）
  - ディメンション詳細はタブ（カルチャー/キャリア/開発環境/事業・採用）で整理
  - `@st.cache_data(ttl=600)` でDBクエリをキャッシュ（10分間）

- **動作確認**: ruff check ALL PASS、pytest 26 passed

- **残課題**: V3 Phase 3（XAI — マッチング説明）

---

## 2026-05-28: V3 Phase 1 — ユーザー分析フロー完成

- **実装内容**:
  - `backend/models.py` — UserProfile に9カラム追加（graduation_year, major, target_industries, target_roles, dev_phase_preference, min_salary, mbti, eval_preference, psych_safety_importance）
  - `alembic/versions/5b55de823f24_add_v3_user_profile_fields.py` — 新規マイグレーション、適用済み
  - `backend/api/profile_manager.py` — `_compute_dimension_weights()` を精緻化（志望業界・職種・開発フェーズ・評価制度・心理的安全性を重みに反映）、`save_profile()` に新フィールド引数追加
  - `pages/1_profile_setup.py` — 3ステップ→4ステップに拡張（Step 2「志望軸」を新設）
  - `app.py` — 市場ポジション可視化を追加（`_get_all_tech_levels()` + `_render_market_position()`）

- **設計判断**:
  - `_compute_dimension_weights()` への追加フィールドはすべてオプション引数（既存プロフィールとの後方互換を保つため）
  - `new_fields` をdictにまとめて `setattr` ループで既存レコードに適用 → フィールド追加が1箇所で完結する
  - 市場ポジション可視化はDB内の全UserProfileとの相対比較で算出。ユーザーが1人だけの場合はスキップ（len < 2）

- **動作確認**: ruff check ALL PASS、pytest 26 passed

- **残課題**: V3 Phase 2（企業情報閲覧ページ）

---

## 2026-05-14: Step 0 企業マスタ初期構築（全Phase）

- **実装内容**:
  - `pyproject.toml` — 依存パッケージ定義（anthropic, sqlalchemy, psycopg2-binary, pydantic-settings, httpx, pandas, beautifulsoup4, lxml、devにruff/pytest/mypy）
  - `docker-compose.yml` — PostgreSQL 16-alpine
  - `backend/config.py` — pydantic-settings による設定管理。全定数（モデル名・タイムアウト・sleep秒数）をここに集約
  - `backend/models.py` — SQLAlchemy 2.0 `Mapped`記法で Company / CompanyField / ProcessingLog を定義
  - `backend/database.py` — Lazy engine 初期化（テスト時にpsycopg2不要でインポートできる）、`get_session()` コンテキストマネージャ
  - `backend/exceptions.py` — GradMatchError / LLMResponseError / VerificationError / DataLoadError / PhaseInputError
  - `backend/seed/llm_generator.py` — Sonnet 4.6 で3バッチ×50社、除外リスト渡しで重複防止
  - `backend/seed/verifier.py` — httpx でURL検証、SSL証明書エラー時は verify=False で再試行、法人番号APIフック用TODOコメントあり
  - `backend/seed/enricher.py` — BeautifulSoup4 でHTML抽出 → Haiku 4.5 で tech_stack/hiring_roles/hq_address 抽出
  - `backend/seed/loader.py` — `pg_insert().on_conflict_do_update()` で official_url を衝突キーにした upsert
  - `backend/seed/run_all.py` — Phase間ゲートチェック（0行なら停止）付き統合実行
  - `prompts/company_generation.txt` — LLMプロンプトテンプレート（{count}/{excluded_companies}プレースホルダー）
  - `tests/conftest.py` — `tmp_data_dir` フィクスチャ（data/ を tmp_path にリダイレクト）
  - `tests/seed/test_verifier.py` — 9テストケース（ネットワークアクセスなし）
  - `README.md` — 環境構築・実行手順・法人番号API取得手順・コスト目安

- **設計判断**:
  - `database.py` の engine を Lazy 初期化にした。モジュールロード時に psycopg2 が不要になり、テストがDB接続なしで動く
  - `corporate_number` を nullable unique にした。PostgreSQL は NULL 同士を重複とみなさないため、NULL が複数あっても unique 制約に抵触しない
  - BeautifulSoup4 + lxml を採用した（trafilatura より依存が軽量で、HTMLパース精度も十分）
  - Phase 4 の upsert は `official_url` を衝突キーにした（法人番号API未承認のため corporate_number は当面 null）

- **動作確認**:
  - `ruff check .` → All checks passed
  - `pytest tests/seed/test_verifier.py -v` → 9/9 passed
  - `pip install -e ".[dev]"` → 正常完了

- **残課題**:
  - 法人番号公表サイト Web-API の App ID 取得後に `verifier.py` の TODO を実装する
  - Alembic 導入（Step 1 開始前に行う）
  - Step 0 の実際の実行（Docker PostgreSQL 起動 + `python -m backend.seed.run_all`）はまだ未実施

---

## 2026-05-20: Phase 2.5 法人番号公表サイト スクレイピング実装

- **実装内容**:
  - `backend/seed/houjin_lookup.py` — 新規作成。法人番号公表サイトへPOST検索、corporate_number / 登記住所 / 法人種別を取得
  - `backend/config.py` — `houjin_sleep_seconds: float = 2.0` 追加
  - `backend/seed/run_all.py` — Phase 2→3 の間に Phase 2.5 を挿入
  - `backend/seed/enricher.py` — INPUT を `verified.csv` → `validated.csv` に変更
  - `backend/seed/loader.py` — `_SOURCE_CONFIDENCE` 定数追加、`corporate_number` / `houjin_address` を Company upsert に反映
  - `backend/models.py` — CompanyField.source コメントを `houjin_scraping / github_api / edinet_api` に更新
  - `backend/seed/verifier.py` — 法人番号API TODO コメントを NOTE に書き換え
  - `tests/seed/test_houjin_lookup.py` — 17テストケース（ネットワークアクセスなし）

- **設計判断**:
  - 法人番号公表サイトは robots.txt なし、Web-API は App ID 申請制で利用不可のためスクレイピングで代替
  - フォーム POST: `kensaku-kekka.html` に CSRF トークン付きで POST → UTF-8 レスポンス
  - 廃業企業を除外するため `closeCkbx` を送信しない（ヒット = 現役登記法人）
  - 読み仮名+公式名称が連結されるケースに対応: `_extract_official_name()` で法人種別の出現位置から分割
  - 空 validated_rows でも CSV ヘッダーを出力するため `pd.DataFrame(rows, columns=val_columns)` で列を明示
  - `corporate_number` は文字列として CSV 保持（`.astype(str)` で型統一）

- **動作確認**:
  - `ruff check .` → All checks passed (3 auto-fixed)
  - `pytest tests/ -v` → 26/26 passed

- **残課題**:
  - 法人番号サイトのHTML構造（CSSセレクタ）が変わった場合は `_search_company()` のパース部分を修正する

---

## 2026-05-20: Step 0 初回実行・バグ修正・DB投入完了

- **実装内容**:
  - `docker-compose.yml` — ポートマッピングを `5432:5432` → `5433:5432` に変更（既存コンテナとの競合回避）
  - `backend/config.py` / `.env.example` — DATABASE_URL のドライバーを `psycopg2` → `psycopg` (psycopg3) に変更
  - `pyproject.toml` — `psycopg2-binary` → `psycopg[binary]>=3.2.0` に変更（Python 3.13 で psycopg2 モジュール欠損）
  - `alembic/env.py` — `.env` の DATABASE_URL を alembic に渡す設定
  - `alembic/versions/8c8816c4cded_initial.py` — 初期マイグレーション（3テーブル）
  - `backend/seed/llm_generator.py` — `_build_prompt` を `str.format()` → `.replace()` に変更（プロンプト内の `{` `}` が KeyError になる問題の修正）
  - `backend/seed/loader.py` — `corporate_number` を13桁数字のみ受け付けるバリデーション追加
  - `backend/seed/houjin_lookup.py` — `_search_company()` のカラム抽出バグを修正: 法人番号は `<td>` ではなく `<th>` に入っており `find_all('td')` では取れていなかった

- **設計判断**:
  - alembic コマンドは Anaconda グローバル Python を使うため `python -c "sys.argv=[...]; from alembic.config import main; main()"` 形式で実行
  - houjin テーブルの実際の HTML 構造: `<tr><th>法人番号</th><td>会社名</td><td>住所</td><td>変更履歴</td></tr>`
    → `row.find('th').get_text()` = 13桁番号、`cells[0]` = 会社名、`cells[1]` = 住所

- **動作確認**:
  - Phase 1: 76社生成 → candidates_raw.csv
  - Phase 2: URL検証 → verified.csv
  - Phase 2.5: 法人番号照合 → validated.csv（77社検証、13社除外）
  - Phase 3: 公式サイト補強 → enriched.csv（Haiku 4.5 API、一部 529 Overloaded でスキップ）
  - Phase 4: DB投入 → 76社成功・0件失敗
  - DB確認: 全76社投入、うち30社が正しい13桁法人番号を保持

- **残課題**:
  - 法人番号 `not_found` の46社は名寄せ失敗または非IT法人未登録（子会社等）
  - Phase 3 で 529 Overloaded になった企業は tech_stack が空 → 再実行で補完可能

---

## 2026-05-20: CompanyMetrics テーブル追加 + Phase 3a OpenWork スクレイピング実装

- **実装内容**:
  - `backend/models.py` — `CompanyMetrics` テーブル追加（company_id FK, openwork_score/review_count/overtime/leave_rate/salary 等）、`Company` に `metrics` backref 追加
  - `alembic/versions/ba147b093133_add_company_metrics.py` — migration 生成・適用
  - `backend/config.py` — `openwork_sleep_seconds: float = 4.0` 追加
  - `backend/seed/openwork_scraper.py` — Phase 3a 新規作成。httpx + BeautifulSoup で OpenWork をスクレイピング
  - `backend/seed/loader.py` — `_upsert_company_metrics()` / `load_openwork_metrics()` 追加
  - `backend/seed/run_all.py` — Phase 3a を Phase 3 と Phase 4 の間に挿入

- **設計判断**:
  - Playwright は不要。httpx + UA偽装で OpenWork に正常アクセスできることを確認済み
  - 1社ずつ `httpx.Client` を生成し `with` で閉じる。共用 Client は `WinError 10054`（接続リセット）後に DNS 解決が全社失敗する CASCADE BUG あり
  - 302 → `/search/addcompany` → 404 は企業未登録を意味する。`resp.url` チェックで空リストを返し `not_found` 扱いにする
  - CSS セレクタ: スコア=`p.fs-17`, 残業=`dt.d-ib`+`dd>span`, 年収=`th[平均年収]+td>span.fs-22`

- **動作確認**:
  - Phase 3a 実行: 12社取得 / 77社中（残65社は未登録or名寄せ失敗）
  - DB確認: CompanyMetrics 12件登録。MonotaRO(3.97)・オービック(3.72)・インテージテクノスフィア(3.65)等が上位
  - ruff check: pass（修正後）

- **残課題**:
  - 未取得65社の一部は検索キーワード最適化で取れる可能性あり

---

## 2026-05-21: Step 1+2+3 スコアリング + Streamlit UI 実装

- **実装内容**:
  - `data/my_profile.json` — 個人プロフィール設定（required_skills/bonus_skills/weights/hard_filters）
  - `backend/api/__init__.py` + `backend/api/scorer.py` — 5軸スコアリングエンジン（技術マッチ・WLB・企業規模・自社開発度・立地）
  - `pyproject.toml` — `streamlit>=1.40.0` 追加（pip install streamlit==1.57.0 済）
  - `app.py` — Streamlit UI（重みスライダー・ハードフィルター・企業一覧テーブル・詳細パネル）

- **設計判断**:
  - 技術マッチは Jaccard ではなく「包含率」（required 70% + bonus 30%）。広技術スタック企業を不当に下げないため
  - スコアリング時は DB 再クエリを避けるため `@st.cache_data(ttl=300)` で5分キャッシュ
  - SQLAlchemy ORM オブジェクトは Streamlit のキャッシュ境界を超えられないため、dict に変換してキャッシュし、疑似オブジェクト `_C` でスコア計算 API に渡す
  - 重みはスライダーで合計が 1.0 でなくても正規化して使用。`total=0` の場合のみエラー表示

- **動作確認**:
  - `ruff check backend/api/scorer.py app.py` → All checks passed
  - `streamlit run app.py --server.headless true --server.port 8501` → HTTP 200

- **残課題**:
  - ハードフィルター: メトリクス未取得企業は現在フィルターをパスする（データ不足で除外しない）仕様。今後 Green スクレイピング後に再検討
  - 企業数が増えたら `_fetch_companies()` の JOIN クエリ化でパフォーマンス改善余地あり

---

## 2026-05-21: Phase 3b Green スクレイピング + 除外リスト + 差分更新バッチ

- **実装内容**:
  - `backend/seed/llm_generator.py` — `_load_existing_names()` 追加。Phase 1 開始時に DB の既存企業名を除外リストに自動追加
  - `backend/seed/green_scraper.py` — Phase 3b 新規作成。`__NEXT_DATA__` JSON を利用して有効求人数・給与レンジ・リモート可否を取得
  - `backend/seed/loader.py` — `_upsert_green_metrics()` / `load_green_metrics()` 追加、GREEN_PATH 定数追加
  - `backend/seed/run_all.py` — Phase 3b を Phase 3a と Phase 4 の間に挿入
  - `backend/config.py` — `green_sleep_seconds: float = 3.0` 追加
  - `backend/seed/update_metrics.py` — 差分更新バッチ新規作成。`--top N` / `--openwork-only` / `--green-only` / `--stale-days` オプション対応

- **設計判断**:
  - Green は `__NEXT_DATA__` JSON を直接パースする（BeautifulSoup 不要、変更耐性が高い）
  - 検索URL: `GET /search?keyword={name}` → `defaultSearchJobOfferData.jobOffers[].company.{id,name}`
  - 給与は `clientJobOffers[].{minSalary,maxSalary}` から min/max を集約（万円単位）
  - リモート: all=True→"full", any=True→"partial", none=True→"none"
  - 除外リスト: DB 未初期化時も例外を catch して空リスト fallback → Phase 1 単体でも動く

- **動作確認**:
  - `ruff check` → All checks passed（6 auto-fixed）
  - `pytest` → 26/26 passed
  - `run_all` 再実行中（バックグラウンド）

- **残課題**:
  - Green の名寄せ精度は OpenWork より低い可能性（検索がキーワードマッチのため無関係な企業が混入しやすい）
  - `update_metrics.py --top 50` を週次 cron に登録して has_current_openings を鮮度維持できる

---

## 2026-05-21: 企業情報量拡充（Green企業基本情報 + OpenWorkサブスコア追加）

- **実装内容**:
  - `backend/models.py` — `CompanyMetrics` に 9 カラム追加: Green由来5つ（employee_count, average_age, capital_10k_yen, is_listed, founded_year）+ OpenWork サブスコア4つ（ow_score_treatment/morale/openness/growth）
  - `alembic/versions/ad2fa147311f_add_company_info_fields.py` — migration 生成・適用（down_revision=ba147b093133）
  - `backend/seed/green_scraper.py` — `_scrape_company_page()` に `client_data` 取得を追加し 5 フィールドを返却。`_empty_metrics()` / `_OUTPUT_COLUMNS` も更新
  - `backend/seed/openwork_scraper.py` — `_SUB_SCORE_LABELS` dict + dt ループでサブスコア取得を追加。`_OUTPUT_COLUMNS` に 4 カラム追加
  - `backend/seed/loader.py` — `_upsert_company_metrics`（OpenWork）に 4 サブスコアを追加。`_upsert_green_metrics`（Green）に `_float` ヘルパーと 5 フィールドを追加
  - `backend/api/scorer.py` — `_to_size_score(employee_count, review_count)` を新規追加。`calc_score` で `employee_count` を優先使用
  - `app.py` — `_fetch_companies()` に 5 フィールド追加。テーブルに「従業員数」列追加。詳細パネルに平均年齢・上場区分・設立年・Green リンクを追加
  - `backend/seed/update_metrics.py` — `DetachedInstanceError` 修正（セッション内で `(id, name)` タプル化）、Green の新 5 フィールドと OpenWork サブスコアを upsert に追加

- **設計判断**:
  - OpenWork サブスコアは `dt`/`dd` ペアで取得（`d-ib` クラスなし、span ラッパーなし）。値が `0.0〜5.0` 範囲内のみ採用
  - `is_listed` は `client_data["stock"]` が存在し非 None → True、None → False と判定
  - `founded_year` は `establishTimestamp`（UNIX秒）を `datetime.fromtimestamp(..., tz=UTC).year` で取得
  - `employee_count` が取れた場合は口コミ件数より信頼度が高いため規模スコア計算で優先
  - `update_metrics.py` の Company オブジェクトをセッション外で参照すると `DetachedInstanceError` になる。セッション内で `(c.id, c.name)` を抽出してから使う

- **動作確認**:
  - `ruff check` → All checks passed
  - `pytest` → 26/26 passed
  - `update_metrics --top 5` → 5社処理、エラーなし

- **バグ修正（実装後に発覚）**:
  - Green `_search_green` が壊れていた: `/search?keyword=` の SSR は常にデフォルト20社を返すため検索が機能しない。サイトマップ(`/sitemap/companies.xml`)から全4022社の ID を ThreadPoolExecutor で並列取得し、正規化名→ID のキャッシュファイル(`data/green_id_cache.json`)を一度だけ構築する方式に変更
  - Green `capital` フィールドが整数でなく `"15億27百万円"` 形式の文字列 → `_parse_capital_10k()` パーサーを追加
  - OpenWork `_SUB_SCORE_LABELS` の `"待遇満足度"` が誤り → 実際のラベルは `"待遇面の満足度"` に修正

- **動作確認**:
  - `ruff check` + `pytest 26/26` → pass
  - Green: 16社取得（ベネフィット・ワン, キーエンス, アイテック阪急阪神 等）
  - OpenWork: 5社の ow_score_treatment が正常に取得
  - DB: Company 134社, CompanyMetrics 30件, employee_count 16件, ow_score_treatment 5件

---

## 2026-05-27: V2 マッチングエンジン全実装（Phase 1〜4）

- **実装内容**:
  - `docker-compose.yml` — Postgres イメージを `postgres:16-alpine` → `pgvector/pgvector:pg16` に変更
  - `docker/init.sql` — `CREATE EXTENSION IF NOT EXISTS vector;` 追加（コンテナ初期化時に自動実行）
  - `pyproject.toml` — `pgvector>=0.3.0` を依存に追加
  - `backend/models.py` — V2用4テーブル追加: `CompanyDimensions`（26★項目+証拠テキスト）/ `CompanyVector`（VECTOR(10)）/ `UserProfile`（tech_level_score + dimension_weights）/ `MatchResult`（スナップショット）。Company に relationships 追加
  - `alembic/versions/f1e2d3c4b5a6_add_v2_tables.py` — 手動マイグレーション。VECTOR型はAlembic自動検出不可のため `op.execute()` で追加
  - `backend/seed/dimensions_extractor.py` — Phase 3c: Gemini 検索グラウンディング（4クエリ）+ Haiku 4.5 で26★項目JSON抽出、overall_confidence 算出、CompanyDimensions upsert
  - `backend/seed/vector_builder.py` — Phase 3d: CompanyDimensions + CompanyMetrics から10次元スコアを算術計算、CompanyVector upsert。LLM不要
  - `backend/seed/run_all.py` — Phase 3c（ディメンション抽出）と Phase 3d（ベクトル計算）を追加
  - `backend/api/profile_manager.py` — UserProfile DB管理: Sonnet 4.6 で tech_level_score 算出、hard_constraints + soft_preferences + tech_level_score から10次元重みベクトルをL1正規化生成
  - `pages/1_profile_setup.py` — 3ステップウィザード: スキル入力 → 条件設定（残業上限/リモート/勤務地/優先事項）→ 確認・保存（Sonnet 4.6 実務力評価）
  - `backend/api/matching_engine.py` — pgvector コサイン類似度クエリ（`1 - (dim_scores <=> :vec ::vector)`）でTOP50取得、tech_level によるリアリスティックスコア割引
  - `app.py` — 3タブ構成に改修: 「V2 理想企業」「V2 受けるべき企業」「V1 旧スコアリング」

- **設計判断**:
  - Alembic は VECTOR 型を自動検出できないため `--autogenerate` は使わず手動で `op.execute("ALTER TABLE ... ADD COLUMN dim_scores vector(10)")` を記述
  - pgvector の配列インデックスは1始まりのため SQL では `dim_scores[10]` が Python の `dim[9]`（開発環境次元）に対応
  - `_search_with_gemini()` は `settings.gemini_api_key` が空なら即 `""` を返す。Gemini 未設定でも Haiku 抽出だけで動作継続できる
  - `@st.cache_data` はモジュールレベルに定義が必要なため `_fetch_companies()` を `with tab_v1:` ブロック外に移動
  - UserProfile の `dimension_weights` は SQLAlchemy `mapped_column(Vector(10))` で定義（`Mapped[]` 型注釈は pgvector が未対応のためスキップ）

- **動作確認**:
  - `python -m py_compile app.py pages/1_profile_setup.py backend/api/matching_engine.py backend/api/profile_manager.py backend/seed/dimensions_extractor.py backend/seed/vector_builder.py` → エラーなし
  - `ruff check` → All checks passed（F401/C408/UP017 を修正）
  - `pytest tests/ -q` → 26/26 passed

- **残課題**:
  - Docker pgvector イメージへの切り替え後、既存 `postgres_data` volume との互換性確認が必要（`pg_dump` でバックアップ推奨）
  - Phase 3c/3d の実際の実行（`python -m backend.seed.dimensions_extractor`）は Gemini API キー設定後に実施
  - Phase 5: カバレッジ測定（★項目70%以上埋まりを目標）、tech_level_score キャリブレーション

---

## 2026-05-28: dimensions_extractor 並列化 + 全341社抽出完了 + V2 UI動作確認

- **実装内容**:
  - `backend/seed/dimensions_extractor.py` — ThreadPoolExecutor + Semaphore で並列化。`_GEMINI_SEMAPHORE(10)` / `_HAIKU_SEMAPHORE(2)` / `_thread_local` でスレッドセーフ化。`_search_category` は3クエリ並列化、`_process_single_company` で10カテゴリ並列化、`extract_all_companies` で3社並列化。`done_ids` で処理済みスキップ（冪等）
  - `backend/seed/vector_builder.py` — `selectinload` + セッション内処理で `DetachedInstanceError` 修正
  - `backend/api/matching_engine.py` — `_build_filter_set` / `_enrich_results` を `selectinload` + セッション内処理で `DetachedInstanceError` 修正。pgvector のサブスクリプト構文を `dim_scores::real[])[10]` に修正

- **設計判断**:
  - 直列処理（39時間見込み）→ 並列化で約2時間に短縮
  - Haiku セマフォ: 5→2（Anthropic 50 RPM 制限に対応）
  - `done_ids` は `overall_confidence >= 0.4` のみスキップ。低信頼度社は再実行対象にする設計
  - pgvector は `float4` 型のため `::float[]` キャストは不可。`::real[]` が正解

- **動作確認**:
  - 全341社のディメンション抽出完了（confidence≥0.4: 196社 = 57.5%）
  - CompanyVector 341社生成完了
  - Streamlit UI（http://localhost:8501）で理想企業ランキング50社表示確認

- **残課題**:
  - CompanyMetrics: 96/341社のみ（OW・年収・残業がNone多数）→ `update_metrics.py` 実行中
  - Anthropic 月額上限に達したため confidence<0.4の145社は6/1リセット後に再実行
  - Gemini月額上限（¥5,000）超過 → ¥20,000に引き上げ済み

---

## 2026-05-28: V2完了・データ充実（Phase 5）

- **実装内容**:
  - `update_metrics.py` 実行 — 245社のOpenWork/Greenスクレイピング。CompanyMetrics: 96社→160社
  - dimensions_extractor 再実行（低信頼度145社）— confidence≥0.4: 196社→215社（63%）
  - `vector_builder.py` 再実行 — CompanyMetrics充実後のベクトルを再計算

- **設計判断**:
  - confidence の構造的上限は約0.88（財務情報が非公開の企業は財務カテゴリが恒常的に低い）
  - OWスコアの7%カバレッジは構造的限界（未登録企業が大半）
  - これ以上の信頼度改善は新規データソース追加（V3でWantedly/Zenn/Qiita追加）で対応

- **動作確認**:
  - confidence≥0.4: 215社（63%）、平均0.458
  - CompanyMetrics: 160社（OWスコアあり24社・Green URLあり140社）
  - Streamlit V2 UI 正常動作確認

- **残課題（V3へ移行）**:
  - ユーザープロフィールの★項目未実装（志望業界・職種・MBTI・希望年収等）→ V3 Phase 1
  - 企業情報閲覧ページなし → V3 Phase 2
  - XAI（マッチング理由説明）なし → V3 Phase 3
