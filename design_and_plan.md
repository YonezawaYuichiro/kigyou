# 就活マッチングAI — 設計確定版 & 開発計画

関連: `schema.sql`（DDL）, `field_mapping.md`（項目→保存方式の正本）

---

## 1. アーキテクチャ（確定）

3層で分離する。「DB＝特徴量ベクトル」にしないことが最重要。

- **原本層 (canonical)** … 人間が読める正規化データ。出典・時点を保持し監査可能。
- **特徴量層 (feature store)** … 原本から派生した正規化値・5段階・タグベクトル。マッチング専用。
- **ユーザー層** … 企業側と対称な希望値 ＋ **weight（個別化の本体）**。

派生（5段階化・理念分解・正規化）は必ず原本を残した上で行う。原本を捨てるとルール変更時に再計算できず、推薦根拠も説明できない。

---

## 2. 確定した設計判断（5＋必須追加）

| # | 判断 | スキーマ上の実装 |
|---|---|---|
| ① | ★〇✕ を **default_weight** に変換 | `feature_definitions.default_weight`：★=1.0 / 〇=0.5 / 再考✕=0.2 / 除外✕=登録しない。ユーザーが `user_preferences.weight` で上書き |
| ② | 制度と実態を分離 | `has_official_actual=TRUE` の項目は `value_official` と `value_actual`(+confidence) を両方保持。残業・有給・リモート・評価制度・フレックスが対象 |
| ③ | 技術スタックは多対多 | `tech_tags`(category付) ＋ `company_tech`。固定one-hotカラムは作らない。マッチングはJaccard |
| ④ | company×role 粒度 | `job_roles` ＋ `company_features.role_id` / `company_tech.role_id`（NULL=全社）。v1は全社中心、技術と裁量だけrole対応の余地を残す |
| ⑤ | 定性5段階は rubric 必須 | `feature_definitions.rubric` に 5/3/1 の定義文。心理的安全性・技術的負債・若手裁量・競合優位性など |

**全値共通メタ**：`source` / `source_type`(official/review/estimated) / `as_of_date` / `confidence` / `is_estimated`。

**必須追加（採用済み）**：`turnover_3yr`（独立数値）, `companies.has_relocation`（転勤）, `salary_records.includes_bonus`（賞与込み想定年収）, NULL維持＋`company_completeness`ビュー。

**再考✕（低weight 0.2 で採用）**：安定性proxy、ダイバーシティ。賞与は30歳平均年収の参照源として利用。採用人数推移は任意。

---

## 3. マッチング計算（設計）

1. **ハードフィルタ**：`is_hard_filter=TRUE` の希望を満たさない企業を除外（例：転勤NG必須）。
2. **項目別サブスコア（0–1）**：
   - direct / scale5 … `direction` を考慮し、希望値とのギャップを 0–1 に変換
   - bool … 一致=1 / 不一致=0
   - tag … `Jaccard(希望tags, 企業tags)`
   - official/actual … `value_actual` を confidence で重み優先、無ければ `value_official`
3. **加重平均**：weight（ユーザー上書き or default_weight）で集約。
   `Score = Σ(weightᵢ × subscoreᵢ) / Σ(weightᵢ)`（NULL項目は分子・分母とも除外）
4. **NULL の扱い**：欠損項目はスコア計算から外し、`completeness_ratio` を別途提示してペナルティを可視化（0扱いにしない）。
5. **説明可能性**：総合スコアに加え、寄与の大きい項目の内訳（なぜ高い/低い）を返す。

---

## 4. 開発計画（フェーズ）

最初に1社を徹底パーソナライズする方針と整合させた段階構成。各フェーズに「完了の目安＝検証」を置く。

**Phase 0｜スキーマ確定 & 特徴量カタログ整備**（設計の核）
- `schema.sql` を適用。
- `field_mapping.md` の全★〇項目を `feature_definitions` に投入。**scale5は全部 rubric を書き切る**。default_weight も全件付与。
- 完了の目安：全採用項目が定義行として存在し、method/direction/weight/rubric に空欄がない。

**Phase 1｜1社を手で徹底入力（原本層）**
- 実在1社を、出典・confidence・as_of付きで原本層に埋める。取れない項目は **NULLのまま**残す。
- 完了の目安：`company_completeness` で網羅率を確認し、「どこにも入らないデータ」「全項目NULLで無意味な定義」を洗い出して項目リストを補正。← リストの過不足は埋めて初めて分かる。

**Phase 2｜原本 → 特徴量への変換**
- 正規化（min/max → 0–1）、scale5化、tagベクトル化、official/actual の合成ルールを実装。
- 完了の目安：1社分の `company_features` が値・正規化値・メタ込みで生成される。

**Phase 3｜ユーザー側 preferences**
- `user_preferences` 入力（希望値＋weight＋hard_filter）。weight未指定時は default_weight を継承。
- 完了の目安：1ユーザー分の希望が企業側と同じ feature_key 体系で表現できる。

**Phase 4｜マッチングスコア**
- ハードフィルタ → 加重平均 → 説明内訳、を実装。
- 完了の目安：1ユーザー×1社で総合スコアと寄与内訳が出る。weightを変えると順位が動くことを確認。

**Phase 5｜スケール検証**
- 2社目以降を追加し、`industries` / `tech_tags` マスタを育てる。
- 完了の目安：複数社ランキングが破綻なく出て、新業界・新技術の追加でスキーマ変更が発生しないこと。

---

## 5. 既知のリスク / 次の論点
- 口コミ依存の scale5（心理的安全性等）は confidence が低くなりがち。情報源の質をどう担保するか。
- role 粒度を本格採用すると入力コストが跳ねる。v1は全社、需要を見てrole化。
- 正規化の min/max を業界横断で固定するか業界内相対にするか（年収は業界で水準が違う）。
