-- ============================================================
-- 就活マッチングAI  DBスキーマ（最終版 v1 / PostgreSQL）
-- 3層構成: 原本層(canonical) + 特徴量層(feature store) + ユーザー層
-- 設計判断: default_weight / official-actual 2スロット / techタグ多対多 /
--           role粒度 / scale5 rubric必須 / NULL維持 + completeness
-- ============================================================

-- ---------- 共通ENUM ----------
CREATE TYPE source_type     AS ENUM ('official', 'review', 'estimated');
CREATE TYPE feature_method  AS ENUM ('direct', 'onehot', 'scale5', 'bool', 'tag');
CREATE TYPE value_direction AS ENUM ('high_good', 'low_good', 'neutral');

-- ============================================================
-- 原本層 (Source of Truth) : 人間が読める正規化データ
-- ============================================================

CREATE TABLE companies (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    founded_year    INT,
    listing_type    TEXT,            -- 上場/未上場/グループ
    employee_count  INT,
    engineer_count  INT,             -- エンジニア比率算出用
    hq_location     TEXT,
    has_relocation  BOOLEAN,         -- ★必須追加: 転勤の有無
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE job_roles (             -- ④company×role粒度の受け皿
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT REFERENCES companies(id),
    role_type   TEXT,                -- backend/frontend/ml/infra/embedded...
    title       TEXT
);

CREATE TABLE offices (
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT REFERENCES companies(id),
    name        TEXT,
    location    TEXT,
    is_dev_site BOOLEAN DEFAULT FALSE -- 実際の開発拠点か
);

-- 業界 (multi-hotの正規化: 固定カラムにしない)
CREATE TABLE industries (
    id   BIGSERIAL PRIMARY KEY,
    name TEXT UNIQUE NOT NULL
);
CREATE TABLE company_industries (
    company_id  BIGINT REFERENCES companies(id),
    industry_id BIGINT REFERENCES industries(id),
    PRIMARY KEY (company_id, industry_id)
);

-- ③技術タグ: 固定one-hot禁止 → マスタ + 多対多 + role単位
CREATE TABLE tech_tags (
    id       BIGSERIAL PRIMARY KEY,
    name     TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL           -- language/framework/cloud/mlops/data/hardware
);
CREATE TABLE company_tech (
    id           BIGSERIAL PRIMARY KEY,
    company_id   BIGINT REFERENCES companies(id),
    tech_tag_id  BIGINT REFERENCES tech_tags(id),
    role_id      BIGINT REFERENCES job_roles(id),  -- NULL=全社
    source       TEXT,
    source_type  source_type,
    confidence   REAL,
    as_of_date   DATE
);
CREATE UNIQUE INDEX uq_company_tech
    ON company_tech (company_id, tech_tag_id, COALESCE(role_id, 0));

-- 給与: 1値ではなく区分付き複数レコード
CREATE TABLE salary_records (
    id             BIGSERIAL PRIMARY KEY,
    company_id     BIGINT REFERENCES companies(id),
    category       TEXT NOT NULL,    -- 初任給_学部/初任給_院/30歳平均/3年後/5年後
    amount         INT,             -- 円
    includes_bonus BOOLEAN,         -- 想定年収=賞与込みか
    year           INT,
    source         TEXT,
    source_type    source_type,
    confidence     REAL
);

CREATE TABLE revenue_records (
    id               BIGSERIAL PRIMARY KEY,
    company_id       BIGINT REFERENCES companies(id),
    year             INT,
    revenue          BIGINT,
    operating_profit BIGINT,
    is_profitable    BOOLEAN
);

-- 理念原文 / 自社の弱み / 求める人物像 / 口コミ をまとめて保持
CREATE TABLE company_texts (
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT REFERENCES companies(id),
    kind        TEXT,                -- values/weakness/persona/review
    body        TEXT,
    source      TEXT,
    source_type source_type
);

-- ============================================================
-- 特徴量層 (Feature Store) : 原本から派生したマッチング用データ
-- ============================================================

-- ①default_weight / ⑤rubric / ②official-actrual / direction を集約する定義マスタ
CREATE TABLE feature_definitions (
    feature_key         TEXT PRIMARY KEY,
    display_name        TEXT NOT NULL,
    category            TEXT,                 -- 章番号/分類
    method              feature_method NOT NULL,
    unit                TEXT,
    value_min           REAL,
    value_max           REAL,
    direction           value_direction DEFAULT 'neutral',
    default_weight      REAL NOT NULL,        -- ★=1.0 / 〇=0.5 / 再考✕=0.2
    has_official_actual BOOLEAN DEFAULT FALSE,-- ②制度/実態の2スロットを使うか
    rubric              TEXT                  -- ⑤scale5の定義文(5/3/1)
);

-- 縦持ち: 観点追加でマイグレーション不要
CREATE TABLE company_features (
    id               BIGSERIAL PRIMARY KEY,
    company_id       BIGINT REFERENCES companies(id),
    role_id          BIGINT REFERENCES job_roles(id),  -- NULL=全社
    feature_key      TEXT REFERENCES feature_definitions(feature_key),
    -- 値スロット
    value_numeric    REAL,   -- 直接値/scale5の生値
    value_normalized REAL,   -- 0-1正規化後(マッチング用)
    value_official   REAL,   -- ②制度(公称)
    value_actual     REAL,   -- ②実態(口コミ/推定)
    -- メタ(全値共通)
    confidence       REAL,
    source           TEXT,
    source_type      source_type,
    as_of_date       DATE,
    is_estimated     BOOLEAN DEFAULT FALSE
);
CREATE UNIQUE INDEX uq_company_features
    ON company_features (company_id, COALESCE(role_id, 0), feature_key);

-- データ網羅率: NULLを0扱いしないためのペナルティ可視化
CREATE VIEW company_completeness AS
SELECT c.id AS company_id,
       count(cf.id) FILTER (
           WHERE cf.value_numeric IS NOT NULL
              OR cf.value_official IS NOT NULL
              OR cf.value_actual  IS NOT NULL
       ) AS filled,
       (SELECT count(*) FROM feature_definitions WHERE default_weight > 0) AS applicable,
       round(
           count(cf.id) FILTER (
               WHERE cf.value_numeric IS NOT NULL
                  OR cf.value_official IS NOT NULL
                  OR cf.value_actual  IS NOT NULL
           )::numeric
           / NULLIF((SELECT count(*) FROM feature_definitions WHERE default_weight > 0), 0)
       , 2) AS completeness_ratio
FROM companies c
LEFT JOIN company_features cf ON cf.company_id = c.id
GROUP BY c.id;

-- ============================================================
-- ユーザー層 : 企業側と対称 + weight(個別化の本体)
-- ============================================================

CREATE TABLE users (
    id         BIGSERIAL PRIMARY KEY,
    name       TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE user_preferences (
    id             BIGSERIAL PRIMARY KEY,
    user_id        BIGINT REFERENCES users(id),
    feature_key    TEXT REFERENCES feature_definitions(feature_key),
    desired_value  REAL,             -- 単一希望値(scale5/直接値)
    desired_min    REAL,
    desired_max    REAL,
    desired_tags   BIGINT[],         -- tag系: 希望するtech_tag_id配列
    weight         REAL,             -- NULLならfeature_definitions.default_weightを使用
    is_hard_filter BOOLEAN DEFAULT FALSE,  -- 必須条件(満たさない企業は除外)
    UNIQUE (user_id, feature_key)
);

-- ============================================================
-- feature_definitions シード例 (各method/rubric/official-actual/weight)
-- ※全項目はfield_mapping.mdを正本に同形式で投入する
-- ============================================================
INSERT INTO feature_definitions
(feature_key, display_name, category, method, unit, value_min, value_max, direction, default_weight, has_official_actual, rubric) VALUES
('starting_salary_master','初任給(院卒)','8.待遇','direct','円',180000,400000,'high_good',1.0,FALSE,NULL),
('salary_age30','30歳平均年収(賞与込)','8.待遇','direct','円',3000000,12000000,'high_good',1.0,FALSE,NULL),
('overtime_hours','月平均残業','8.待遇','direct','時間',0,80,'low_good',0.5,TRUE,NULL),
('remote_rate','リモート実施率','8.待遇','direct','%',0,100,'high_good',0.5,TRUE,NULL),
('turnover_3yr','3年離職率','7.人事','direct','%',0,60,'low_good',0.5,FALSE,NULL),
('has_specialist_track','専門職ルートの有無','7.人事','bool',NULL,0,1,'high_good',1.0,FALSE,NULL),
('young_autonomy','若手への裁量権','7.人事','scale5',NULL,1,5,'high_good',1.0,FALSE,
 '5=入社1-2年でリーダー/新規案件を任される実例多数 | 3=2-3年で一部裁量 | 1=年功で若手は補助中心'),
('psychological_safety','心理的安全性','6.組織','scale5',NULL,1,5,'high_good',1.0,FALSE,
 '5=失敗を許容し挑戦を歓迎する制度/文化が明確 | 3=チーム依存 | 1=減点主義・萎縮の口コミ多数'),
('tech_debt_culture','技術的負債への向き合い','10.技術','scale5',NULL,1,5,'high_good',1.0,FALSE,
 '5=リファクタを評価制度で評価 | 3=有志ベース | 1=新機能優先で放置の口コミ'),
('charging_model','課金モデル','3.事業','tag',NULL,NULL,NULL,'neutral',0.5,FALSE,NULL),
('stability','企業の安定性(proxy)','4.財務','scale5',NULL,1,5,'high_good',0.2,FALSE,
 '自己資本比率/黒字継続/売上推移から算出 | 5=長期黒字+高自己資本 | 1=赤字継続+資金繰り懸念'),
('diversity','ダイバーシティ/男女比','6.組織','scale5',NULL,1,5,'neutral',0.2,FALSE,
 '低weightの任意軸 | 5=多様性指標が高水準 | 1=極端な偏り');
