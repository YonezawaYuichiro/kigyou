# GradMatch-AI Version 2 実装計画

## Context

現在のV1は5軸の加重和スコアリング（ルールベース）で精度が低い。根本原因は3つ：
1. **企業データが貧弱**（OpenWork評価点・残業時間・場所程度しかない）
2. **ユーザー実力を資格名でしか測れない**（実務経験レベルが分からない）
3. **「入れる企業」と「良い企業」の区別がない**（無謀なエントリーを防げない）

V2では多次元ベクトルによるマッチング + 対話型ユーザープロファイリング + LLM出力の正確性検証で解決する。

---

## V2 アーキテクチャ概要

```
[Geminiウェブ検索] ─── 企業名 + 複数クエリ ──→ 検索結果テキスト
[企業HP直接取得] ────────────────────────────→ HPテキスト
                                          ↓ Haiku で構造化抽出
                              CompanyDimensions（26★項目 + 証拠テキスト）
                                          ↓ 正確性検証（信頼度スコア）
                                          ↓ 算術計算
                                CompanyVector（10次元スコア・pgvector）
                                          ↓ コサイン類似度
[ユーザー対話UI] → Haiku解釈 → UserProfile（tech_level_score + 10次元重み）
                                          ↓
                       MatchResult（理想スコア + 現実的スコア）
                                          ↓
              Streamlit: 「理想企業」タブ ＋ 「受けるべき企業」タブ
```

### 検索・LLM役割分担

| 役割 | ツール | 理由 |
|------|--------|------|
| 企業情報ウェブ検索 | Gemini（既存: `llm_generator.py` の `_call_gemini_once` を拡張） | Google検索グラウンディングで最新情報を取得 |
| 構造化JSON抽出 | Haiku 4.5 | コスト効率、単純な抽出タスク |
| ユーザースキル解釈・複雑な推論 | Sonnet 4.6 | 精度が重要な場面 |

### 10次元スコアベクトル定義

| dim | 意味 | 主なソース |
|-----|------|----------|
| [0] | 開発拠点の魅力度 | Company.hq_prefecture |
| [1] | ビジョン・新規事業積極度 | Haiku抽出スコア |
| [2] | ビジネスモデル堅牢性（競合優位性・特許） | Haiku抽出スコア |
| [3] | 財務健全性（R&D比率・設備投資） | Haiku抽出値 |
| [4] | 業界トレンド適合度 | Gemini検索 + Haiku抽出 |
| [5] | 心理的安全性・カルチャー | Haiku抽出スコア |
| [6] | キャリア成長支援（評価・裁量・スキル補助） | Haiku抽出スコア |
| [7] | 待遇・WLB | CompanyMetrics（既存）流用 |
| [8] | 採用プロセス透明度（実技試験・面接官属性） | Haiku抽出 |
| [9] | 開発環境モダン度（スタック・CI/CD・データ基盤） | Haiku抽出スコア |

各次元は0.0〜1.0に正規化。

---

## Phase 0: CLAUDE.md 更新（即座に実施）

CLAUDE.md の以下セクションを更新：
- **進捗状況** に Step 1〜4 の代わりにV2フェーズを反映
- **技術スタック** に `Google Gemini API`（web検索用）と `pgvector` を追記
- **モデルの使い分け原則** に Gemini の役割を追記

---

## Phase 1: DBスキーマ拡張（Week 1〜2）

**目的**: 既存データを壊さずV2テーブルを追加

### `docker-compose.yml`
```yaml
# 変更前
image: postgres:16-alpine
# 変更後
image: pgvector/pgvector:pg16
```
初期化SQL volume を追加: `CREATE EXTENSION IF NOT EXISTS vector;`

**注意**: 既存 `postgres_data` volumeとの互換性のため `pg_dump` でバックアップしてからイメージ変更。

### `backend/models.py` に追加する4テーブル

```python
class CompanyDimensions(Base):
    """★26項目 + 各項目の証拠テキスト（正確性検証用）"""
    company_id: FK(Company) UNIQUE
    # ビジョン
    new_biz_policy_score: Float         # 0.0-1.0
    new_biz_policy_evidence: Text       # 根拠テキスト（正確性確認用）
    # ビジネスモデル
    competitive_advantage_score: Float
    competitive_advantage_evidence: Text
    has_patent: Boolean
    # 財務
    rd_ratio: Float                     # null許容
    rd_ratio_evidence: Text
    capex_ratio: Float
    # 業界
    megatrend_alignment: JSON           # ["AI", "自動化"] 等
    megatrend_score: Float
    # カルチャー
    psychological_safety_score: Float   # 0.0-5.0
    psychological_safety_evidence: Text
    # 人事・キャリア
    evaluation_score: Float
    evaluation_system_type: String      # 成果主義|年功序列|混在|不明
    career_track_diversity: Boolean
    skill_support_score: Float
    skill_support_items: JSON
    junior_authority_score: Float       # 0.0-5.0
    junior_authority_evidence: Text
    # 採用
    has_coding_test: Boolean            # null許容
    interviewer_type: String            # current_engineer|hr_only|mixed|unknown
    # 開発環境
    tech_modernity_score: Float         # 0.0-5.0
    infra_cloud_score: Float
    cicd_maturity_score: Float
    hw_sw_integration: Boolean
    data_platform_score: Float
    tech_debt_culture_score: Float
    tech_env_evidence: Text             # 開発環境全般の根拠テキスト
    # メタ・信頼度
    extraction_model: String
    overall_confidence: Float           # 0.0-1.0（後述の正確性検証で算出）
    low_confidence_fields: JSON         # 信頼度が低いフィールドリスト
    search_sources_used: JSON           # 使用したURL/検索クエリリスト
    last_extracted_at: DateTime

class CompanyVector(Base):
    """10次元スコアベクトル（pgvector）"""
    company_id: FK(Company) UNIQUE
    dim_scores: VECTOR(10)
    model_version: String               # "v2.0"
    created_at: DateTime

class UserProfile(Base):
    """my_profile.jsonの移行先 + 対話UI入力"""
    session_id: String UNIQUE
    tech_skills: JSON
    qualifications: JSON
    project_experience: Text            # 自由記述
    architecture_experience: JSON
    tech_level_score: Float             # Haiku算出 0.0-1.0
    tech_level_rationale: Text
    hard_constraints: JSON              # {max_overtime, remote, prefectures}
    soft_preferences: JSON
    dimension_weights: VECTOR(10)       # ユーザーの10次元重みベクトル

class MatchResult(Base):
    """マッチング結果スナップショット"""
    user_profile_id: FK(UserProfile)
    company_id: FK(Company)
    ideal_score: Float
    realistic_score: Float
    hard_filter_passed: Boolean
    rank_ideal: Integer
    rank_realistic: Integer
    score_breakdown: JSON
    computed_at: DateTime
```

`pyproject.toml` に `pgvector` を追加（実施前に承認）。

Alembicマイグレーション:
```bash
alembic revision --autogenerate -m "add_v2_tables"
# VECTOR型は手動で追記:
# op.execute("CREATE EXTENSION IF NOT EXISTS vector")
# op.execute("ALTER TABLE company_vector ADD COLUMN dim_scores vector(10)")
```

---

## Phase 2: ディメンション抽出パイプライン（Week 3〜4）

**目的**: 既存企業の `CompanyDimensions` + `CompanyVector` を埋める

### 情報収集: Gemini web検索の活用

既存 `llm_generator.py` の `_call_gemini_once` パターンを参考に、各企業について複数クエリで検索:
```python
queries = [
    f"{company_name} 技術スタック 開発環境 エンジニア",
    f"{company_name} 評価制度 キャリアパス 若手 裁量",
    f"{company_name} 心理的安全性 カルチャー 口コミ",
    f"{company_name} 研究開発費 財務 事業戦略",
]
```
取得したテキスト + 企業HPテキスト（`enricher.py:_fetch_page_text()` 再利用）を結合してHaikuに渡す。

### 新規ファイル: `backend/seed/dimensions_extractor.py`

```
処理フロー:
1. Company 全件取得
2. Gemini で複数クエリ検索 → テキスト集約
3. enricher.py の _fetch_page_text() でHP直接取得
4. テキストを結合し Haiku で★26項目をJSON一括抽出
5. CompanyDimensions にupsert
6. ProcessingLog に記録（enricher.py の _save_log パターン踏襲）
```

Haiku抽出スキーマ（プロンプトに埋め込む）:
```json
{
  "new_biz_policy_score": "0.0-1.0 新規事業積極性（証拠なし=0.5）",
  "new_biz_policy_evidence": "根拠となる文章を50文字以内で引用",
  "competitive_advantage_score": "0.0-1.0",
  "has_patent": "boolean",
  "rd_ratio": "0.0-1.0 または null（不明）",
  "megatrend_alignment": ["AI", "自動化", "脱炭素", "その他"],
  "psychological_safety_score": "0.0-5.0",
  "psychological_safety_evidence": "根拠テキスト",
  "evaluation_system_type": "成果主義|年功序列|混在|不明",
  "career_track_diversity": "boolean",
  "junior_authority_score": "0.0-5.0",
  "has_coding_test": "boolean または null",
  "interviewer_type": "current_engineer|hr_only|mixed|unknown",
  "tech_modernity_score": "0.0-5.0",
  "infra_cloud_score": "0.0-5.0",
  "cicd_maturity_score": "0.0-5.0",
  "data_platform_score": "0.0-5.0",
  "tech_debt_culture_score": "0.0-5.0",
  "tech_env_evidence": "開発環境全般の根拠テキスト",
  "low_confidence_fields": ["証拠が見つからなかったフィールド名リスト"]
}
```

### LLM出力の正確性検証

`overall_confidence` スコアを以下で算出:
```python
def compute_confidence(dims: dict) -> float:
    """
    証拠テキストが空のフィールド数でペナルティ。
    情報源が多いほど信頼度アップ。
    """
    evidence_fields = [
        "new_biz_policy_evidence",
        "psychological_safety_evidence",
        "junior_authority_evidence",
        "tech_env_evidence",
    ]
    filled = sum(1 for f in evidence_fields if dims.get(f))
    base = filled / len(evidence_fields)  # 0.0-1.0
    null_penalty = len(dims.get("low_confidence_fields", [])) * 0.05
    return max(0.0, min(1.0, base - null_penalty))
```

UIでは `overall_confidence < 0.4` の企業に「情報不足」バッジを表示。

### 新規ファイル: `backend/seed/vector_builder.py`

`CompanyDimensions` + `CompanyMetrics` の数値から10次元スコアを算術計算してupsert。LLM呼び出し不要。

主要計算式:
```python
dim[7] = 既存 scorer.py のWLBスコア計算ロジックをそのまま移植
dim[9] = (tech_modernity + infra_cloud + cicd_maturity + data_platform + tech_debt_culture) / 25.0
# 各次元は 0.0-1.0 にclamp
```

コスト試算: Gemini検索 + Haiku ≈ $0.001/社 × 100社 = $0.10（月$10制約内）

---

## Phase 3: ユーザープロフィール DB移行 + 対話UI（Week 5〜6）

### 新規ファイル: `backend/api/profile_manager.py`

- `load_or_create_profile(session_id)` → `UserProfile`（DBから取得、なければ `my_profile.json` をfallback import）
- `assess_tech_level(project_experience, skills)` → Sonnet 4.6 呼び出し → `tech_level_score`
- `compute_dimension_weights(hard_constraints, soft_preferences, tech_level_score)` → VECTOR(10)

重み計算ロジック:
```python
weights = [0.1] * 10  # 均等スタート
weights[7] += (1 - max_overtime / 60) * 0.3   # WLB加重
weights[9] += tech_level_score * 0.3            # 技術力高い人→開発環境重視
weights[6] += junior_preference * 0.2           # 若手裁量重視
weights = [w / sum(weights) for w in weights]   # L1正規化
```

### 新規ファイル: `pages/1_profile_setup.py`（Streamlit multipage）

3ステップウィザード:
1. スキル・個人開発経験入力（自由記述） → Sonnet 4.6 で `tech_level_score` 算出・根拠表示
2. 条件設定（絶対条件 / 妥協できる条件）
3. 確認 → DBに保存 → 自動でマッチング実行

### 新規ファイル: `prompts/user_level_assessment.txt`

Sonnet向けプロンプト。評価軸:
- 実装経験の複雑度（シングルページ=0.2 / フルスタック=0.5 / マイクロサービス=0.8）
- スタックのモダン度
- インターン期間・実務経験
- OSS/技術ブログ等の発信

---

## Phase 4: マッチングエンジン + UI改修（Week 7〜8）

### 新規ファイル: `backend/api/matching_engine.py`

```python
def compute_matches(user_profile: UserProfile, session) -> list[MatchResult]:
    # Step 1: ハードフィルタ（既存 scorer.py の _passes_hard_filters を流用）
    # Step 2: pgvectorコサイン類似度クエリ
    sql = """
        SELECT company_id,
               1 - (dim_scores <=> :user_weights::vector) AS ideal_score,
               dim_scores[9] AS tech_demand
        FROM company_vector cv
        JOIN company c ON cv.company_id = c.id
        WHERE c.id = ANY(:filtered_ids)
        ORDER BY ideal_score DESC LIMIT 50
    """
    # Step 3: realistic_score = ideal_score × 合格可能性
    for r in results:
        gap = max(0, r.tech_demand - user_profile.tech_level_score)
        r.realistic_score = r.ideal_score * (1 - gap * 0.5)
    # Step 4: MatchResultをDBに保存（スナップショット）
```

### `app.py` 改修

- 2タブ構成: 「理想企業ランキング（ideal_score順）」「受けるべき企業（realistic_score順）」
- スコア内訳: 既存棒グラフ → 10次元レーダーチャート
- 「情報不足」バッジ（`overall_confidence < 0.4`）を企業カードに表示
- 証拠テキスト表示: 詳細ページで各スコアの根拠を展開表示

---

## Phase 5: データ充実・チューニング（Week 9〜10）

- **カバレッジ測定**: ★項目の埋まり率70%以上を目標
- **tech_level_scoreキャリブレーション**: 実際の就活結果との照合で割引係数調整
- **技術ブログ追加ソース**: Zenn/Qiita の企業アカウント検索を Gemini クエリに追加
- **月次更新トラック**: `update_metrics.py` 実行後に `vector_builder.py` を自動再実行

---

## 既存コードの再利用一覧

| 既存コード | V2での利用方法 |
|-----------|---------------|
| `scorer.py:_passes_hard_filters()` | `matching_engine.py` Step 1でそのまま使用 |
| `scorer.py` WLBスコア計算 | `vector_builder.py` の `dim[7]` 計算に移植 |
| `scorer.py:_to_size_score()` | 補助関数として継続利用 |
| `enricher.py:_fetch_page_text()` | `dimensions_extractor.py` の情報源として再利用 |
| `enricher.py:_save_log()` パターン | `dimensions_extractor.py` でも同様に踏襲 |
| `llm_generator.py:_call_gemini_once()` | `dimensions_extractor.py` のウェブ検索に拡張 |
| `llm_generator.py:_parse_llm_response()` | `dimensions_extractor.py` でJSON解析に再利用 |
| `CompanyField` EAV構造 | 〇項目（低優先度）の格納先として継続利用 |
| `CompanyMetrics` 全フィールド | `dim[7]` 計算の入力として継続利用 |

---

## コスト試算（月額上限 $10）

| 処理 | モデル | 単価 | 月間量 | 月額 |
|------|--------|------|--------|------|
| ディメンション抽出（Gemini検索 + Haiku抽出） | Gemini + Haiku | $0.001/社 | 10社 | $0.01 |
| ユーザースキル評価 | Sonnet 4.6 | $0.003/回 | 50回 | $0.15 |
| 就職分析UI（既存機能） | Haiku 4.5 | $0.001/回 | 100回 | $0.10 |
| 企業候補生成（四半期バッチ） | Gemini + Sonnet | $2.00/バッチ | 0.33/月 | $0.66 |
| **合計** | | | | **~$0.92/月** |

---

## 実施順序とCLAUDE.md更新

**最初に実施**: Phase 0 として CLAUDE.md を更新（技術スタック・進捗状況反映）

各Phaseの完了確認（検証手順）:

- **Phase 1**: `docker compose up -d` → `\dt` で4新テーブル確認
- **Phase 2**: `python -m backend.seed.dimensions_extractor` を1社に試行 → DB確認 + 証拠テキストが入っていること
- **Phase 3**: `streamlit run app.py` → profile_setup.pyが表示、DB保存できること
- **Phase 4**: 2タブ表示確認・理想/現実的ランキングに差があること
- **Phase 5**: カバレッジ70%以上確認
