# GradMatch-AI

新卒就活向け企業マッチング・推薦システム。関西IT企業マスタから、ユーザーのプロフィール・希望条件・重み付けに基づいて入社可能性とマッチ度を計算し、逆引き推薦する。

## 🔴 必ず守るルール（最優先）

### 開発フロー
1. **コードを書く前に必ず既存コードを読む**。変更対象ファイルと、それを参照しているファイルを最低限viewする
2. **3ファイル以上の変更は計画提示→承認→実装の順**。いきなり編集しない
3. **DBスキーマ変更は必ず承認を取る**。models.pyの修正は計画書として先に提示
4. **コミット前に必ずruff check と pytest を通す**
5. **実装が完了したら `.claude/implementation-log.md` に追記する**。何を作ったか・なぜその設計にしたかを残す

### 絶対にやらないこと
- `.env` ファイルをコミット、または内容をログ・コード・コメントに残さない
- `ANTHROPIC_API_KEY` を含む文字列を絶対に出力・echo しない
- `data/` ディレクトリ配下のCSVをgitに含めない（個人情報・APIコスト浪費の温床）
- マイグレーションファイルを削除・改変しない
- `requirements.txt` や `pyproject.toml` に新しい依存を勝手に追加しない（提案して承認を待つ）
- 動作未確認のコードに「動作確認済み」と言わない

### 言語ルール
- 日本語のコメント・docstring・ログメッセージはOK（むしろ推奨）
- 変数名・関数名・クラス名は英語
- コミットメッセージは日本語可、ただしprefix（feat/fix/refactor/docs/test）は英語

---

## プロジェクト構成

```
企業分析AI/
├── backend/
│   ├── models.py           # SQLAlchemy 2.0 モデル
│   ├── database.py         # DB接続・セッション管理
│   ├── config.py           # 環境変数・設定（pydantic-settings）
│   ├── seed/               # Step 0: 企業マスタ構築スクリプト群
│   │   ├── llm_generator.py    # Phase 1: LLM候補生成
│   │   ├── verifier.py         # Phase 2: URL検証
│   │   ├── enricher.py         # Phase 3: 情報補強
│   │   ├── loader.py           # Phase 4: DB投入
│   │   └── run_all.py          # 統合実行
│   └── api/                # Step 1以降: FastAPI ルーティング
├── prompts/                # LLM用プロンプトテンプレート
├── data/                   # CSVキャッシュ（.gitignore済み）
├── tests/                  # pytest
├── docker-compose.yml      # PostgreSQL用
├── .env                    # 秘密情報（コミット禁止）
└── pyproject.toml
```

---

## 技術スタック

- **Python 3.11+**
- **FastAPI** + **Pydantic v2**
- **SQLAlchemy 2.0**（同期版でOK、Step 0段階では非同期不要）
- **PostgreSQL 16**（Dockerで起動）
- **Anthropic API**: `claude-sonnet-4-6` / `claude-haiku-4-5`
  - Sonnet 4.6: 推論・生成（候補生成、スコアリング、根拠生成）
  - Haiku 4.5: 単純抽出・分類（採用ページ解析、URL検証補助）
- **httpx**: 外部API呼び出し
- **pandas**: CSV処理
- **ruff**: linter + formatter（black/isort/flake8を統合）
- **pytest** + **pytest-mock**

### モデルの使い分け原則
| タスク | モデル | 理由 |
|---|---|---|
| 推論・生成・根拠説明 | Sonnet 4.6 | バランス型 |
| 単純な抽出・分類 | Haiku 4.5 | コスト効率 |
| Opus 4.7 | **使わない** | 過剰スペック・コスト高 |

---

## 開発コマンド

### 環境セットアップ
```bash
# 仮想環境作成（Windows）
python -m venv venv
venv\Scripts\activate

# 依存インストール
pip install -r requirements.txt

# DB起動
docker compose up -d postgres

# DB停止
docker compose down
```

### 実行
```bash
# Step 0: 企業マスタ構築（全Phase）
python -m backend.seed.run_all

# 各Phase単体実行
python -m backend.seed.llm_generator
python -m backend.seed.verifier
python -m backend.seed.enricher
python -m backend.seed.loader

# FastAPI起動（Step 1以降）
uvicorn backend.main:app --reload --port 8000
```

### テスト・品質チェック
```bash
# テスト全体
pytest

# 単一テスト
pytest tests/seed/test_verifier.py -v

# Lint + Format
ruff check . --fix
ruff format .

# 型チェック
mypy backend/
```

---

## コーディング規約

### Python全般
- **型ヒント必須**。関数引数・戻り値すべてにアノテーション
- **Pydantic v2構文**を使用。`BaseModel`継承、`Field()`でバリデーション
- **f-string推奨**。`%`演算子や`.format()`は使わない
- **pathlib.Path使用**。`os.path`は使わない（Windowsパス対応のため）
- **関数1つ40行以内**。超えたらヘルパー関数に分割
- **マジックナンバー禁止**。定数は `config.py` または上部に定義

### 命名規則
- 関数・変数: `snake_case`
- クラス: `PascalCase`
- 定数: `UPPER_SNAKE_CASE`
- プライベート: `_leading_underscore`
- DBテーブル: 単数形（`company`、`user_profile`）

### エラーハンドリング
- LLM API・HTTP呼び出しは**必ずtry/exceptで囲む**
- 失敗時は `ProcessingLog` テーブルに記録して継続
- 致命的エラーのみ例外を再raise
- カスタム例外は `backend/exceptions.py` に定義

### LLM呼び出しの規約
```python
# ✅ Good: モデル選択を明示、リトライ・ログあり
response = anthropic_client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=8000,
    temperature=0.7,
    messages=[{"role": "user", "content": prompt}],
)
time.sleep(1)  # レート制限対策

# ❌ Bad: モデル直書き、sleep忘れ、ログ無し
client.create(model="claude-3", messages=[...])
```

### CSV/データ処理
- 文字エンコーディングは **明示的に `utf-8-sig`** （Excelとの互換性）
- 区切り文字はカンマ、改行は `\n`
- 空欄は `None` ではなく空文字列 `""` で統一（pandasの`NaN`は明示的に処理）

---

## データモデルの中核概念

### Company
企業マスタ。`corporate_number`（法人番号13桁）でユニーク識別する想定だが、法人番号API承認待ちのため**当面は `official_url` でユニーク**とする。

### CompanyField
各フィールドのメタデータ（情報源・信頼度）。スコア計算時に「LLM推定値だけに依存したスコア」を検出するために使用。

### UserProfile
ユーザープロフィール。**変更履歴を `ProfileChangeLog` で追跡**することで、「資格取得後にスコアがどう変動したか」を可視化する。

### ScoreSnapshot
スコア計算結果の履歴。プロフィール変更・重み変更・企業情報更新のたびにスナップショットを残し、時系列グラフ表示の元データにする。

### PriorityWeights
ユーザーが調整する重み。合計1.0に正規化（validatorで保証）。5項目：
- `tech_growth`（技術成長性・MLOps適性）
- `wlb`（ワークライフバランス）
- `company_size`（企業規模・安定性）
- `self_developed`（自社開発度・資本独立性）
- `location`（勤務地・京阪神優先）

---

## 環境変数

`.env`ファイル（コミット禁止）に以下を記載。`.env.example`にダミー値で公開。

```bash
# Anthropic API
ANTHROPIC_API_KEY=sk-ant-...

# Database
DATABASE_URL=postgresql://gradmatch:gradmatch_dev@localhost:5432/gradmatch

# 法人番号API（承認待ち、将来用）
# HOUJIN_API_ID=

# ログレベル
LOG_LEVEL=INFO
```

---

## 進捗状況（最終更新: 2026-05-14）

- [x] 計画書作成
- [x] データモデル設計
- [x] **Step 0: 企業マスタ初期構築（完了）**
- [ ] Step 1: ユーザープロフィール画面
- [ ] Step 2: スコアリングエンジン
- [ ] Step 3: 逆引き推薦
- [ ] Step 4: 企業追加・補強

---

## 想定外を防ぐためのチェックリスト

新しい機能を実装する前に、以下を確認：

1. [ ] この機能はどのStepに属するか？スコープ外を勝手に実装していないか？
2. [ ] 既存の `models.py` のテーブルで足りるか？新規追加が必要か？
3. [ ] LLMを使う場合、Sonnet/Haikuのどちらが適切か？
4. [ ] エラーハンドリングは ProcessingLog に記録されるか？
5. [ ] 単体テストは書けるか？外部API依存はmockできるか？

---

## トラブルシューティング

- **Docker Postgresに接続できない**: `docker compose ps` でstatus確認、ポート5432が他のプロセスに使われていないか `netstat -ano | findstr 5432`
- **Anthropic APIエラー**: `.env` の `ANTHROPIC_API_KEY` を確認、Anthropic Consoleで月次予算上限に達していないか確認
- **文字化け**: CSVは `utf-8-sig` で読み書きする
- **Windowsでパスエラー**: `pathlib.Path` を使う、`/` ではなく `Path` オブジェクトで組み立てる

---

## 実装履歴の記録ルール

実装が完了するたびに `.claude/implementation-log.md` に追記する。
記録はフェーズ・機能単位で行い、過去の判断を後から追えるようにする。

### 記録フォーマット
```md
## YYYY-MM-DD: <タイトル>
- **実装内容**: 作成・変更したファイル（箇条書き）
- **設計判断**: なぜその実装にしたか（代替案との比較など）
- **動作確認**: ruff/pytest の結果、手動確認した内容
- **残課題**: TODO・既知の制限事項
```

### 記録のタイミング
- Phase・機能単位の実装完了時
- 設計方針や技術選定を変更した時
- バグ修正で原因が非自明だった時

---

## このCLAUDE.md自体の運用

- このファイルは300行以内を維持する
- タスク固有の長いルールは `.claude/rules/` に分離する
- フェーズ進捗の更新時、合わせてこのファイルの「進捗状況」も更新する
- ルール追加時は「なぜ必要か」を1行で残す