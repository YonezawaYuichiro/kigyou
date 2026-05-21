# 実装履歴ログ

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
