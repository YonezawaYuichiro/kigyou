# 就活マッチングAI — DB設計・実装計画 最終版

## 正本ドキュメント
- `schema.sql` — DDL（PostgreSQL）
- `design_and_plan.md` — 設計判断・フェーズ計画
- `field_mapping.md` — feature_key 正本リスト

本ファイルは上記3ドキュメントをSQLAlchemy/既存コードに適合させた実装計画。

---

## アーキテクチャ（確定）

```
原本層 (Source of Truth)
  companies / job_roles / offices / industries / company_industries /
  tech_tags / company_tech / salary_records / revenue_records / company_texts
         ↓ ETL（dimensions_extractor / deep_dive / 手動入力）
特徴量層 (Feature Store)
  feature_definitions（マスタ）/ company_features（縦持ちEAV）
         ↓
ユーザー層
  users / user_preferences（EAV）
         ↓
ベクトル層（既存維持）
  company_vector（pgvector 10次元）/ match_result
```

**原則:** 5段階化・理念分解・正規化は特徴量層で行う。原本を捨てない。

---

## 確定した設計判断

| # | 判断 | 実装 |
|---|---|---|
| ① | ★〇✕ → default_weight | `feature_definitions.default_weight`：★=1.0 / 〇=0.5 / 再考✕=0.2 / 除外✕=未登録 |
| ② | 制度と実態の分離 | `has_official_actual=TRUE` の項目は `company_features` の同一行に `value_official` + `value_actual` を両列持つ（残業・有給・リモート・評価制度対象） |
| ③ | 技術スタックは多対多 | `tech_tags`(category付) + `company_tech`。固定one-hotカラム禁止。マッチングはJaccard |
| ④ | company×role粒度 | `job_roles` + `company_features.role_id`（NULL=全社）。v1は全社中心 |
| ⑤ | scale5はrubric必須 | `feature_definitions.rubric` に 5/3/1 の定義文。空欄のscale5は未完扱い |

**共通メタ:** `source` / `source_type`(official/review/estimated) / `as_of_date` / `confidence` / `is_estimated`

**必須追加（採用済み）:** `companies.has_relocation`（転勤）, `salary_records.includes_bonus`（賞与込み）, `company_completeness` VIEW

---

## SQLAlchemy適合メモ（schema.sqlからの変更点）

| schema.sql | SQLAlchemy実装 | 理由 |
|---|---|---|
| `BIGSERIAL PRIMARY KEY` | `UUID(as_uuid=True)` | 既存コードとの一貫性（Company等がUUID） |
| `CREATE TYPE source_type AS ENUM` | `mapped_column(String(20))` + CheckConstraint | Alembicとの相性 |
| `BIGINT[] desired_tags` | `mapped_column(JSON)` | SQLAlchemy配列サポートが複雑なため |
| `CREATE VIEW company_completeness` | Alembicの`op.execute()`でVIEW作成 | 既存パターン踏襲 |

---

## 新規テーブル（SQLAlchemy models.py）

### 原本層

```python
class JobRole(Base):
    """company × role 粒度の受け皿。v1はNULL=全社中心。"""
    __tablename__ = "job_role"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    role_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # backend / frontend / ml / infra / embedded / data / qa
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)

class Office(Base):
    __tablename__ = "office"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    location: Mapped[str] = mapped_column(String(100), nullable=False)
    is_dev_site: Mapped[bool] = mapped_column(Boolean, default=False)

class IndustryMaster(Base):
    __tablename__ = "industry_master"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)

class CompanyIndustry(Base):
    __tablename__ = "company_industry"
    __table_args__ = (PrimaryKeyConstraint("company_id", "industry_id"),)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    industry_id: Mapped[int] = mapped_column(ForeignKey("industry_master.id"))

class TechTag(Base):
    __tablename__ = "tech_tag"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    # language / framework / cloud / mlops / data / hardware

class CompanyTech(Base):
    __tablename__ = "company_tech"
    # ① PG16: NULLS NOT DISTINCT で role_id=NULL（全社）行の重複を防ぐ
    __table_args__ = (
        UniqueConstraint("company_id", "tech_tag_id", "role_id",
                         postgresql_nulls_not_distinct=True),
        CheckConstraint(
            "source_type IN ('official','review','estimated')",
            name="ck_company_tech_source_type"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    tech_tag_id: Mapped[int] = mapped_column(ForeignKey("tech_tag.id"))
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_role.id"), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # "official" / "review" / "estimated"
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)

class SalaryRecord(Base):
    __tablename__ = "salary_record"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    # 初任給_学部 / 初任給_院 / 30歳平均 / 3年後 / 5年後 / 賞与込想定_30歳
    amount: Mapped[int | None] = mapped_column(Integer, nullable=True)     # 円
    includes_bonus: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class RevenueRecord(Base):
    __tablename__ = "revenue_record"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    # ② BigInteger必須: Integer上限は約21.4億円。中堅企業でもオーバーフロー
    revenue: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    operating_profit: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_profitable: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

class CompanyText(Base):
    """理念原文・弱み・求める人物像・口コミをkindで分類して一テーブルに統合。"""
    __tablename__ = "company_text"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # "values" / "weakness" / "persona" / "review"
    body: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
```

**Company テーブルへの追加（2カラム）:**
```python
has_relocation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # 転勤有無
engineer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)    # エンジニア比率算出用
```

### 特徴量層

```python
class FeatureDefinition(Base):
    __tablename__ = "feature_definition"
    # ③ method / direction のタイポを DB レベルで防止
    __table_args__ = (
        CheckConstraint(
            "method IN ('direct','scale5','bool','tag','onehot')",
            name="ck_feature_def_method"
        ),
        CheckConstraint(
            "direction IN ('high_good','low_good','neutral')",
            name="ck_feature_def_direction"
        ),
    )
    feature_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    method: Mapped[str] = mapped_column(String(20), nullable=False)
    # "direct" / "scale5" / "bool" / "tag" / "onehot"
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    value_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    direction: Mapped[str] = mapped_column(String(15), default="neutral")
    # "high_good" / "low_good" / "neutral"
    default_weight: Mapped[float] = mapped_column(Float, nullable=False)
    # ★=1.0 / 〇=0.5 / 再考✕=0.2
    has_official_actual: Mapped[bool] = mapped_column(Boolean, default=False)
    # Trueならばcompany_featureにvalue_official+value_actualの両列に入れる
    rubric: Mapped[str | None] = mapped_column(Text, nullable=True)
    # scale5必須: "5=... | 3=... | 1=..." 形式
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

class CompanyFeature(Base):
    """縦持ちEAV。UNIQUE(company_id, role_id, feature_key)。role_id=NULLは全社値。"""
    __tablename__ = "company_feature"
    # ① PG16: NULLS NOT DISTINCT で role_id=NULL（全社）行の重複を防ぐ
    # ③ source_type のタイポを DB レベルで防止
    __table_args__ = (
        UniqueConstraint("company_id", "role_id", "feature_key",
                         postgresql_nulls_not_distinct=True),
        CheckConstraint(
            "source_type IN ('official','review','estimated')",
            name="ck_company_feature_source_type"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    role_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("job_role.id"), nullable=True)
    feature_key: Mapped[str] = mapped_column(
        ForeignKey("feature_definition.feature_key"), nullable=False
    )

    # 値スロット
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 直接値 / scale5の生値 (NULL=不明。0=ゼロ値。絶対に混同しない)
    # ⑤ v1は feature_definition.value_min/max による固定正規化のみ。相対正規化はPhase 5以降
    value_normalized: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 0-1正規化後（マッチング用）
    value_official: Mapped[float | None] = mapped_column(Float, nullable=True)
    # has_official_actual=TRUEの項目: 制度・公称値
    value_actual: Mapped[float | None] = mapped_column(Float, nullable=True)
    # has_official_actual=TRUEの項目: 実態（口コミ/推定）

    # メタ（全値共通）
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    source_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # "official" / "review" / "estimated"
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
```

**completeness は VIEW（保存不要）:**
マイグレーションの `upgrade()` 内で `op.execute("""CREATE VIEW company_completeness AS ...""")` として作成。schema.sqlのVIEW定義を流用。

### ユーザー層

```python
class UserPreference(Base):
    """企業側company_featureと対称。weight=NULLならdefault_weightを使用。"""
    __tablename__ = "user_preference"
    __table_args__ = (UniqueConstraint("user_profile_id", "feature_key"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_profile_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_profile.id", ondelete="CASCADE")
    )
    feature_key: Mapped[str] = mapped_column(
        ForeignKey("feature_definition.feature_key"), nullable=False
    )
    desired_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    desired_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    desired_max: Mapped[float | None] = mapped_column(Float, nullable=True)
    # ⑥ JSON型のためFKが効かない。書き込み時にアプリ側でtech_tag.id実在チェックを必須とする
    desired_tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # tag系feature用: 希望するtech_tag.id のリスト
    weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    # NULL → feature_definitions.default_weight を使用
    is_hard_filter: Mapped[bool] = mapped_column(Boolean, default=False)
```

---

## 既存テーブルの扱い

| テーブル | 方針 |
|---|---|
| `Company` | 保持。`has_relocation` / `engineer_count` を追加。`tech_stack(JSON)` / `estimated_category(String)` は移行後に廃止 |
| `CompanyField` | 保持（raw dump sink。company_featureとは役割が異なる）|
| `CompanyMetrics` | **漸進廃止** → salary_record + company_feature に移行後DROP |
| `CompanyDimensions` | **漸進廃止** → company_feature に移行後DROP |
| `CompanyVector` | 保持（pgvectorマッチングは継続） |
| `UserProfile` | 保持。`dimension_weights(Vector)` はuser_preference移行後に廃止 |
| `MatchResult` | 保持 |
| `ProcessingLog` | 保持 |

---

## マッチング2段構成（④ 役割を明記）

```
Stage 1 — 粗い絞り込み（高速）
  pgvector cosine_similarity(company_vector 10次元, user_dim_weights 10次元)
  → 全社から上位 K=50 社を候補に絞る

Stage 2 — 再ランク + 説明（精密）
  company_feature の加重スコア（下記計算式）で K 社を再ランク
  → 総合スコア + 寄与TOP3 項目の内訳を返す
```

**company_vector と company_feature の関係（一貫性の保証）:**

vector_builder.py は company_feature EAV を読んでベクトルを生成する。
特徴量が更新されたら必ずベクトルを再計算する（deep_dive.pyのStep 5）。

| 次元 | 主な feature_key |
|---|---|
| dim[0] 自社開発度 | industry_master（inhouse_saas / sier / ses 等） |
| dim[1] 新規事業 | f:new_biz_activeness |
| dim[2] 技術鮮度 | tech_tag（category=lang/fw） + github_activity_score |
| dim[3] 安定性 | listing_type + turnover_3yr + salary_age30 |
| dim[4] 成長支援 | f:young_autonomy + has_specialist_track + f:training_quality |
| dim[5] カルチャー | f:psychological_safety + avg_tenure |
| dim[6] 若手キャリア | f:young_autonomy + f:bottom_up_degree |
| dim[7] WLB | annual_holiday_days + overtime_hours + paid_leave_usage_pct |
| dim[8] 採用評価度 | has_coding_test + field_engineer_joins |
| dim[9] 開発環境 | f:mlops_maturity + f:tech_debt_culture + github_activity_score |

---

## マッチング計算式（確定 — design_and_plan.md §3）

```
1. ハードフィルタ: is_hard_filter=TRUE の条件を満たさない企業を除外
2. 項目別サブスコア (0-1):
   - direct/scale5: direction考慮でギャップを0-1変換
   - bool: 一致=1 / 不一致=0
   - tag: Jaccard(希望tags, 企業tags)
   - has_official_actual=TRUE: value_actual を confidence 重み優先、
                               なければ value_official
3. 加重平均:
   Score = Σ(weight_i × subscore_i) / Σ(weight_i)
   weight_i = user_preference.weight if not NULL else feature_definition.default_weight
   ※ NULL(不明)項目は分子・分母ともに除外
4. completeness_ratio を別途提示（0扱いにしない）
5. 寄与TOP項目を内訳として返す（説明可能性）
```

---

## 開発フェーズ（design_and_plan.md §4 を採用）

### Phase 0: スキーマ確定 & 特徴量カタログ整備（設計の核）
- `alembic revision` で新テーブルをすべて作成
- `backend/seed/master_data.py`（新規）で以下を投入:
  - `industry_master`（初期業界マスタ）
  - `tech_tag`（初期技術タグマスタ）
  - `feature_definitions`（field_mapping.mdの全★〇項目）
    → **scale5は全部 rubric を書き切る**。default_weightも全件付与。空欄禁止
- **完了の目安:** 全採用項目が定義行として存在し、method/direction/weight/rubric に空欄がない

### Phase 1: 1社を手動入力（原本層検証）
- 実在1社をSQLで手動投入（出典・confidence・as_of付き）。取れない項目はNULL。
- `SELECT * FROM company_completeness WHERE company_id = X` で網羅率を確認
- **完了の目安:** 「どこにも入らないデータ」「全項目NULLで無意味な定義」を洗い出し、field_mapping.mdと feature_definitions を補正する
- ← リストの過不足は1社埋めて初めて分かる

### Phase 2: 原本 → 特徴量変換パイプライン
- `dimensions_extractor.py` と `deep_dive.py` を新テーブルへ書き込むよう改修
- 正規化（min/max → 0-1）、scale5化、official/actualの合成ルールを実装
- **完了の目安:** 1社分の `company_feature` が値・value_normalized・メタ込みで生成される

### Phase 3: ユーザー側preferences
- `user_preferences` 入力UI/API（希望値+weight+hard_filter）
- weight未指定時はdefault_weightを継承するロジック
- **完了の目安:** 1ユーザー分の希望が企業側と同じfeature_key体系で表現できる

### Phase 4: マッチングスコア
- ハードフィルタ → 加重平均 → 説明内訳、を実装
- **完了の目安:** 1ユーザー×1社で総合スコアと寄与内訳が出る。weightを変えると順位が動く

### Phase 5: スケール検証
- 2社目以降を追加し、`industry_master` / `tech_tag` マスタを育てる
- **完了の目安:** 複数社ランキングが破綻なく出て、新業界・新技術の追加でスキーマ変更が発生しない

---

## 変更対象ファイル

| ファイル | Phase | 変更内容 |
|---|---|---|
| `backend/models.py` | 0 | 新テーブル10本追加、Company に2カラム追加 |
| `alembic/versions/XXXX_v5_two_layer.py` | 0 | 新規マイグレーション（VIEW含む） |
| `backend/seed/master_data.py` | 0 | **新規**: マスタデータ・feature_definitions 投入スクリプト |
| `backend/seed/dimensions_extractor.py` | 2 | 新テーブルへ書き込み + 旧テーブルへのフォールバック削除 |
| `backend/seed/deep_dive.py` | 2 | GitHub/ブログ結果を company_feature に保存 |
| `backend/seed/vector_builder.py` | 2 | company_feature EAVからベクトル計算 |
| `backend/api/matching_engine.py` | 4 | user_preferences + 上記計算式に切り替え |

---

## 検証コマンド

```bash
# Phase 0
alembic upgrade head
python -m backend.seed.master_data
# → feature_definitions の件数・rubric空欄を確認

# Phase 1（手動SQL投入後）
# SELECT * FROM company_completeness;

# Phase 2
python -m backend.seed.deep_dive --name "GrapeCity"
# SELECT feature_key, value_numeric, value_official, value_actual,
#        confidence, source_type FROM company_feature WHERE company_id=...

# 品質チェック（全フェーズ共通）
ruff check backend/
```
