# GradMatch-AI

新卒就活向け企業マッチング・推薦システム。
関西IT企業マスタから、ユーザーのプロフィール・希望条件・重み付けに基づいて
入社可能性とマッチ度を計算し、逆引き推薦する。

---

## 環境変数

`.env.example` をコピーして `.env` を作成し、各値を設定してください。

```bash
cp .env.example .env
```

| 変数名 | 必須 | 説明 |
|---|---|---|
| `ANTHROPIC_API_KEY` | **必須** | Anthropic Console で取得した API キー |
| `DATABASE_URL` | 任意 | デフォルト: `postgresql://gradmatch:gradmatch_dev@localhost:5432/gradmatch` |
| `LOG_LEVEL` | 任意 | `INFO` / `DEBUG` / `WARNING`（デフォルト: `INFO`） |
| `HOUJIN_API_ID` | 将来用 | 法人番号公表サイト Web-API の Application ID（現在未使用） |

---

## セットアップ

### 1. Python 仮想環境

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# 依存インストール
pip install -e ".[dev]"
```

### 2. PostgreSQL 起動（Docker）

```bash
docker compose up -d postgres

# 起動確認
docker compose ps
```

### 3. DB 接続確認

```bash
python -c "from backend.database import engine; conn = engine.connect(); print('DB接続OK'); conn.close()"
```

---

## Step 0: 企業マスタ構築

### 統合実行（推奨）

```bash
python -m backend.seed.run_all
```

Phase 1→2→3→4 を自動で実行します。各Phaseのゲートチェックにより、
前フェーズの出力が0件の場合は停止します。

### 各Phase単体実行

```bash
# Phase 1: LLMで関西IT企業候補を150社生成
# 注意: Anthropic APIのクレジットを消費します（Sonnet 4.6 × 3回）
python -m backend.seed.llm_generator
# → data/candidates_raw.csv

# Phase 2: 公式URLにHTTPアクセスして実在を検証
python -m backend.seed.verifier
# → data/verified.csv（通過）/ data/rejected.csv（除外）

# Phase 3: 公式サイトから技術情報を補強
# 注意: Anthropic APIのクレジットを消費します（Haiku 4.5 × 通過社数）
python -m backend.seed.enricher
# → data/enriched.csv

# Phase 4: PostgreSQLに投入
python -m backend.seed.loader
```

### 投入結果の確認

```bash
python -c "
from backend.database import get_session
from backend.models import Company
with get_session() as s:
    count = s.query(Company).count()
    print(f'Company: {count}社登録済み')
"
```

---

## テスト・品質チェック

```bash
# テスト（API/DB接続不要）
pytest tests/seed/test_verifier.py -v

# 全テスト
pytest

# Lint + フォーマットチェック
ruff check .
ruff format . --check

# 型チェック
mypy backend/

# まとめて実行（CIと同等）
ruff check . && ruff format . --check && pytest && mypy backend/
```

---

## 法人番号公表サイト Web-API（将来用）

Phase 2 では現在URLアクセスのみで実在確認を行っています。
法人番号公表サイト Web-API を使うと、企業名から法人番号（13桁）を取得でき、
データの精度が向上します。

### App ID 取得手順

1. 国税庁 法人番号公表サイトにアクセス
2. 「Web-API 利用申請」から申請（無料・登録不要ではなくアカウント登録が必要）
3. 取得した Application ID を `.env` に追記:
   ```
   HOUJIN_API_ID=あなたのAppID
   ```
4. `backend/seed/verifier.py` の `TODO` コメント箇所を実装する

---

## プロジェクト構成

```
企業分析AI/
├── backend/
│   ├── __init__.py
│   ├── config.py           # 環境変数・定数管理
│   ├── database.py         # DB接続・セッション管理
│   ├── exceptions.py       # カスタム例外
│   ├── models.py           # SQLAlchemy 2.0 モデル
│   └── seed/
│       ├── llm_generator.py    # Phase 1: LLM候補生成
│       ├── verifier.py         # Phase 2: URL検証
│       ├── enricher.py         # Phase 3: 情報補強
│       ├── loader.py           # Phase 4: DB投入
│       └── run_all.py          # 統合実行
├── prompts/
│   └── company_generation.txt  # LLMプロンプトテンプレート
├── data/                   # CSVキャッシュ（.gitignore済み）
├── tests/
│   └── seed/
│       └── test_verifier.py
├── docker-compose.yml
├── pyproject.toml
├── .env                    # 秘密情報（コミット禁止）
└── .env.example
```

---

## DB テーブル概要

| テーブル | 説明 |
|---|---|
| `company` | 企業マスタ（official_url でユニーク、corporate_number は将来用） |
| `company_field` | 各フィールドの情報源・信頼度メタデータ |
| `processing_log` | 各Phase の実行ログ（エラー調査用） |

---

## コスト目安（Step 0 実行時）

| Phase | モデル | 想定コスト |
|---|---|---|
| Phase 1 | Sonnet 4.6 × 3回 | 約$0.10〜$0.30 |
| Phase 3 | Haiku 4.5 × 〜100社 | 約$0.05〜$0.10 |
| **合計** | | **約$0.15〜$0.40** |

月額上限 $10 に対して十分な余裕があります。
