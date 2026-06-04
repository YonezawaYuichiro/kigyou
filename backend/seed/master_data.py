"""Phase 0: マスタデータ投入スクリプト

投入対象:
  1. industry_master  (IT業界カテゴリ)
  2. tech_tag         (技術タグ: 言語/FW/クラウド/MLOps/データ/DevOps/ハード)
  3. feature_definition (全採用項目: ★=1.0 / 〇=0.5 / 再考✕=0.2、scale5はrubric必須)

実行: python -m backend.seed.master_data

完了の目安:
  - 全採用項目が定義行として存在する
  - method / direction / default_weight / rubric(scale5) に空欄がない
"""

import logging

import sqlalchemy as sa

from backend.database import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ============================================================
# 1. 業界マスタ
# ============================================================
INDUSTRIES: list[str] = [
    "SIer（システムインテグレーター）",
    "自社サービス（BtoB SaaS）",
    "自社サービス（BtoC）",
    "受託開発",
    "SES（システムエンジニアリングサービス）",
    "コンサルティング・ITサービス",
    "通信・キャリア・インフラ",
    "ゲーム・エンターテイメント",
    "EC・リテールテック",
    "フィンテック・金融系",
    "ヘルステック・医療IT",
    "エドテック・教育IT",
    "製造業（メーカー）社内IT・DX",
    "広告テック・マーケティング",
    "AI・機械学習専業",
    "クラウド・データセンター",
    "セキュリティ",
    "モビリティ・交通・物流テック",
    "不動産テック",
    "農業・環境テック",
]


# ============================================================
# 2. 技術タグマスタ
# ============================================================
# (name, category)
TECH_TAGS: list[tuple[str, str]] = [
    # language
    ("Python", "language"),
    ("JavaScript", "language"),
    ("TypeScript", "language"),
    ("Go", "language"),
    ("Rust", "language"),
    ("Java", "language"),
    ("Kotlin", "language"),
    ("Swift", "language"),
    ("C++", "language"),
    ("C#", "language"),
    ("Ruby", "language"),
    ("Scala", "language"),
    ("R", "language"),
    ("Julia", "language"),
    ("PHP", "language"),
    ("Dart", "language"),
    ("C", "language"),
    # framework
    ("React", "framework"),
    ("Vue.js", "framework"),
    ("Angular", "framework"),
    ("Next.js", "framework"),
    ("Nuxt.js", "framework"),
    ("Svelte", "framework"),
    ("FastAPI", "framework"),
    ("Django", "framework"),
    ("Flask", "framework"),
    ("Spring Boot", "framework"),
    ("Ruby on Rails", "framework"),
    ("NestJS", "framework"),
    ("Flutter", "framework"),
    ("PyTorch", "framework"),
    ("TensorFlow", "framework"),
    ("JAX", "framework"),
    ("LangChain", "framework"),
    ("LlamaIndex", "framework"),
    # cloud
    ("AWS", "cloud"),
    ("GCP", "cloud"),
    ("Azure", "cloud"),
    ("Cloudflare", "cloud"),
    ("Vercel", "cloud"),
    ("Heroku", "cloud"),
    ("DigitalOcean", "cloud"),
    ("Oracle Cloud", "cloud"),
    # mlops
    ("MLflow", "mlops"),
    ("Kubeflow", "mlops"),
    ("Weights & Biases", "mlops"),
    ("Apache Airflow", "mlops"),
    ("Prefect", "mlops"),
    ("Ray", "mlops"),
    ("Triton Inference Server", "mlops"),
    ("ONNX", "mlops"),
    ("DVC", "mlops"),
    ("Feast", "mlops"),
    # data / database
    ("PostgreSQL", "data"),
    ("MySQL", "data"),
    ("MongoDB", "data"),
    ("Redis", "data"),
    ("Elasticsearch", "data"),
    ("Apache Kafka", "data"),
    ("Apache Spark", "data"),
    ("Snowflake", "data"),
    ("BigQuery", "data"),
    ("Amazon Redshift", "data"),
    ("dbt", "data"),
    ("Databricks", "data"),
    ("Hadoop", "data"),
    ("ClickHouse", "data"),
    # devops
    ("Docker", "devops"),
    ("Kubernetes", "devops"),
    ("Terraform", "devops"),
    ("Ansible", "devops"),
    ("GitHub Actions", "devops"),
    ("Jenkins", "devops"),
    ("CircleCI", "devops"),
    ("ArgoCD", "devops"),
    ("Prometheus", "devops"),
    ("Grafana", "devops"),
    ("Datadog", "devops"),
    ("New Relic", "devops"),
    # hardware / embedded
    ("NVIDIA GPU", "hardware"),
    ("エッジAI", "hardware"),
    ("IoT", "hardware"),
    ("FPGA", "hardware"),
    ("ASIC", "hardware"),
    ("ロボティクス", "hardware"),
    ("組み込みLinux", "hardware"),
    ("ROS", "hardware"),
]


# ============================================================
# 3. 特徴量定義マスタ
# ============================================================
# 各行: (feature_key, display_name, category, method, unit,
#         value_min, value_max, direction, default_weight,
#         has_official_actual, rubric)
#
# ★=1.0 / 〇=0.5 / 再考✕=0.2
# scale5のrubricは "5=... | 3=... | 1=..." 形式（空欄禁止）

FEATURE_DEFINITIONS: list[tuple] = [
    # ------------------------------------------------------------------
    # 8. 待遇（給与 ★ → ✕再考で採用）
    # ------------------------------------------------------------------
    (
        "starting_salary_master",
        "初任給（院卒・月額）",
        "8.待遇・働き方",
        "direct",
        "円",
        180_000.0,
        400_000.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    (
        "salary_age30",
        "30歳平均年収（賞与込）",
        "8.待遇・働き方",
        "direct",
        "円",
        3_000_000.0,
        12_000_000.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    # ------------------------------------------------------------------
    # 2. ビジョン・戦略 ★
    # ------------------------------------------------------------------
    (
        "f:new_biz_activeness",
        "新規事業の立ち上げ方針",
        "2.ビジョン・戦略",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=年間複数の新規事業立ち上げ実績があり、予算と専任チームが割り当てられている"
            " | 3=既存事業の延長で新機能や新サービスを年1〜2本出している"
            " | 1=新規事業の話題は出るが承認プロセスが重く、直近3年で立ち上げ実績がない"
        ),
    ),
    # ------------------------------------------------------------------
    # 3. ビジネスモデル ★
    # ------------------------------------------------------------------
    (
        "f:competitive_advantage",
        "競合優位性",
        "3.ビジネスモデル",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=特許・独自技術・参入障壁が高く競合が模倣困難な護城河を持つ"
            " | 3=差別化要素はあるが競合も類似製品を持つ"
            " | 1=差別化が不明確で価格競争または受託依存"
        ),
    ),
    (
        "f:tech_barrier",
        "技術参入障壁",
        "3.ビジネスモデル",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=特許群・独自アルゴリズム・大量データ蓄積など模倣困難な技術的護城河を持つ"
            " | 3=技術力はあるが同等の企業が複数存在する"
            " | 1=汎用技術のみで差別化の技術的根拠が薄い"
        ),
    ),
    # ------------------------------------------------------------------
    # 4. 財務 ★
    # ------------------------------------------------------------------
    (
        "rnd_ratio",
        "R&D比率",
        "4.財務",
        "direct",
        "%",
        0.0,
        30.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    # ------------------------------------------------------------------
    # 5. 業界動向 ★
    # ------------------------------------------------------------------
    (
        "f:trend_fit",
        "メガトレンド適合度",
        "5.業界動向",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=AI・脱炭素・自動化等の複数メガトレンドの中心プレーヤーであり"
            "市場成長が5年以上見込める"
            " | 3=トレンドに乗っているが主力事業は成熟市場"
            " | 1=衰退市場または規制リスクが高い分野が主力"
        ),
    ),
    # ------------------------------------------------------------------
    # 6. 組織・カルチャー ★
    # ------------------------------------------------------------------
    (
        "f:psychological_safety",
        "心理的安全性",
        "6.組織・カルチャー",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=失敗を許容し挑戦を歓迎する制度・文化が明確で、"
            "口コミにも肯定的なエピソードが多数"
            " | 3=チームや上司によって差があり、一部で萎縮の声がある"
            " | 1=減点主義・報告を恐れる・挑戦より安全志向という口コミが支配的"
        ),
    ),
    # ------------------------------------------------------------------
    # 7. 人事・評価・キャリア ★
    # ------------------------------------------------------------------
    (
        "f:young_autonomy",
        "若手への裁量権",
        "7.人事・評価・キャリア",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=入社1〜2年でリーダーや新規プロジェクト担当の実例が複数あり、"
            "裁量の大きさを口コミが支持"
            " | 3=2〜3年目で一部の裁量が与えられるが、主要判断は上位者が担う"
            " | 1=年功制が根強く、若手は補助業務や確認作業が中心という口コミが多い"
        ),
    ),
    (
        "has_specialist_track",
        "専門職ルート（スペシャリスト・エキスパート職）の有無",
        "7.人事・評価・キャリア",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    (
        "f:skill_support_quality",
        "スキルアップ支援の充実度",
        "7.人事・評価・キャリア",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=資格取得補助・書籍購入補助・カンファレンス費用全額支給・"
            "社内勉強会が整備され利用実績が高い"
            " | 3=制度はあるが年間上限が低いまたは利用率が低い"
            " | 1=OJTのみで自己学習の支援制度がない"
        ),
    ),
    (
        "f:evaluation_system",
        "評価制度の公正度・透明性",
        "7.人事・評価・キャリア",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=成果主義が明確な基準で運用され、評価への異議申し立て制度があり透明性が高い"
            " | 3=評価基準はあるが上司裁量の余地が大きい"
            " | 1=評価基準が不明確または年功制が強く努力が報われにくいという口コミが多い"
        ),
    ),
    # ------------------------------------------------------------------
    # 9. 採用・選考 ★
    # ------------------------------------------------------------------
    (
        "has_coding_test",
        "コーディングテスト・技術課題の有無",
        "9.採用・選考",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    (
        "field_engineer_joins",
        "面接に現場エンジニアが同席するか",
        "9.採用・選考",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    # ------------------------------------------------------------------
    # 10. 開発環境・技術力 ★
    # ------------------------------------------------------------------
    (
        "has_data_lake",
        "データレイク・DWHの有無",
        "10.開発環境・技術力",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    (
        "f:mlops_maturity",
        "MLOps・CI-CD整備度",
        "10.開発環境・技術力",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=CI/CDが完全自動化され、MLモデルのバージョン管理・"
            "デプロイパイプラインが本番稼働中"
            " | 3=CI/CDは導入済みだがML部分は手動またはスクリプト管理"
            " | 1=テストや自動化が未整備でデプロイが属人的"
        ),
    ),
    (
        "f:hw_sw_integration",
        "ハード×ソフト連携度",
        "10.開発環境・技術力",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=エッジAI・IoT・ロボティクス等でハードとソフトの共同設計が日常業務であり専任チームが存在"
            " | 3=ソフトウェア主体だがハードとのI/Fを扱う場面がある"
            " | 1=純ソフトウェア開発でハードへの関与がほぼない"
            "（志望者の志向に合わせて評価）"
        ),
    ),
    (
        "f:data_platform_maturity",
        "データ基盤の整備度",
        "10.開発環境・技術力",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=データレイク・DWH・BIが整備され、全社データ活用の基盤が本番稼働している"
            " | 3=一部部署でデータ基盤があるが全社統一はされていない"
            " | 1=Excelや個別DBが点在し、統合的なデータ分析基盤がない"
        ),
    ),
    (
        "f:tech_debt_culture",
        "技術的負債への向き合い方",
        "10.開発環境・技術力",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        1.0,
        False,
        (
            "5=リファクタリングが評価制度で評価され、"
            "定期的な技術負債返済スプリントが制度化されている"
            " | 3=有志ベースのリファクタはあるが制度的な後押しはない"
            " | 1=新機能優先で負債を放置しているという口コミが複数ある"
        ),
    ),
    # ==================================================================
    # 〇 items (default_weight=0.5)
    # ==================================================================
    # 2. ビジョン 〇
    (
        "f:tech_investment_direction",
        "技術投資方針",
        "2.ビジョン・戦略",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=AI・クラウドネイティブ化・ハードウェア刷新など複数の先端投資が"
            "中期計画に明記され実行中"
            " | 3=特定領域への投資はあるが全社的な技術戦略は不明確"
            " | 1=技術投資の言及が少なくコスト削減志向が強い"
        ),
    ),
    # 3. ビジネスモデル 〇
    (
        "charging_model",
        "課金モデル",
        "3.ビジネスモデル",
        "tag",
        None,
        None,
        None,
        "neutral",
        0.5,
        False,
        None,
    ),
    # 4. 財務 〇
    (
        "capex_ratio",
        "設備投資比率（対売上）",
        "4.財務",
        "direct",
        "%",
        0.0,
        20.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    # 5. 業界動向 〇
    (
        "f:market_growth",
        "市場成長性",
        "5.業界動向",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=参入市場のTAMが年率10%以上成長しており、自社シェア拡大余地が大きい"
            " | 3=市場は安定成長（年率3〜10%）で長期的な縮小リスクは低い"
            " | 1=市場が成熟または縮小傾向にあり代替技術による置き換えリスクがある"
        ),
    ),
    (
        "f:regulation_impact",
        "規制・政策の追い風度",
        "5.業界動向",
        "scale5",
        None,
        1.0,
        5.0,
        "neutral",
        0.5,
        False,
        (
            "5=政府方針・規制強化が自社ビジネスの追い風となっており、"
            "公共調達や補助金を活用中"
            " | 3=規制の影響はニュートラルで大きな制約もない"
            " | 1=規制強化リスクが高く、事業モデルへの直接的な脅威がある"
        ),
    ),
    # 6. 組織・カルチャー 〇
    (
        "f:bottom_up_degree",
        "ボトムアップ度（現場の提案が通る度合い）",
        "6.組織・カルチャー",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=現場提案が制度的に採用される仕組みがあり、"
            "実際に製品・制度改善に反映された実例が多い"
            " | 3=意見は言えるが決定は上位層が行うことが多い"
            " | 1=トップダウンが強く現場の意見が通りにくいという口コミが多い"
        ),
    ),
    (
        "f:text_comm_culture",
        "テキスト・非同期コミュニケーション文化",
        "6.組織・カルチャー",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=非同期コミュニケーションが定着しており、"
            "Slack/Notionでの意思決定ドキュメント化が文化として根付いている"
            " | 3=ツールは整備されているが口頭や会議依存の部分が多い"
            " | 1=メールや口頭中心でテキストでの知識蓄積が乏しい"
        ),
    ),
    (
        "f:exec_field_distance",
        "経営陣と現場の距離（低いほど良い）",
        "6.組織・カルチャー",
        "scale5",
        None,
        1.0,
        5.0,
        "low_good",
        0.5,
        False,
        (
            "1（最良）=CEOや役員が現場エンジニアと定期的に1on1・全員MTGを実施し"
            "現場の声が経営判断に反映される"
            " | 3=経営と現場の交流機会が年数回ある"
            " | 5（最悪）=経営陣との接点がほぼなく現場に情報が降りてこない"
        ),
    ),
    (
        "avg_age",
        "従業員平均年齢",
        "6.組織・カルチャー",
        "direct",
        "歳",
        25.0,
        50.0,
        "neutral",
        0.5,
        False,
        None,
    ),
    # 7. 人事・評価・キャリア 〇
    (
        "review_freq_per_year",
        "評価頻度（回/年）",
        "7.人事・評価・キャリア",
        "direct",
        "回",
        1.0,
        12.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    (
        "f:unwanted_rotation_risk",
        "不本意ジョブローテーションリスク（低いほど良い）",
        "7.人事・評価・キャリア",
        "scale5",
        None,
        1.0,
        5.0,
        "low_good",
        0.5,
        False,
        (
            "1（良好）=職種・チームの希望が尊重され、"
            "一方的な異動がない文化が確立されている"
            " | 3=ローテーションはあるが事前に相談・同意のプロセスがある"
            " | 5（問題）=不本意な部署異動が頻繁で、"
            "希望を無視した転勤・職種変更の口コミが多い"
        ),
    ),
    (
        "f:training_quality",
        "研修・育成体制の充実度",
        "7.人事・評価・キャリア",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=体系的な新人研修・技術研修があり、"
            "メンター制度と組み合わさって定着率が高い"
            " | 3=OJT中心で体系的な研修は少ないが基礎的な育成はある"
            " | 1=研修が実質ないまたは形式的で現場に放り込まれる口コミが多い"
        ),
    ),
    (
        "has_mentor",
        "メンター制度の有無",
        "7.人事・評価・キャリア",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    (
        "turnover_3yr",
        "3年離職率",
        "7.人事・評価・キャリア",
        "direct",
        "%",
        0.0,
        60.0,
        "low_good",
        0.5,
        False,
        None,
    ),
    (
        "avg_tenure",
        "平均勤続年数",
        "7.人事・評価・キャリア",
        "direct",
        "年",
        1.0,
        25.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    # 8. 待遇・働き方 〇
    (
        "overtime_hours",
        "月平均残業時間（制度/実態2スロット）",
        "8.待遇・働き方",
        "direct",
        "時間",
        0.0,
        80.0,
        "low_good",
        0.5,
        True,  # has_official_actual
        None,
    ),
    (
        "paid_leave_usage_pct",
        "有給取得率（制度/実態2スロット）",
        "8.待遇・働き方",
        "direct",
        "%",
        0.0,
        100.0,
        "high_good",
        0.5,
        True,  # has_official_actual
        None,
    ),
    (
        "remote_rate",
        "リモート実施率（制度/実態2スロット）",
        "8.待遇・働き方",
        "direct",
        "%",
        0.0,
        100.0,
        "high_good",
        0.5,
        True,  # has_official_actual
        None,
    ),
    (
        "has_flextime",
        "フレックスタイム制の有無",
        "8.待遇・働き方",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    (
        "f:leave_ease",
        "有給の取りやすさ（実態）",
        "8.待遇・働き方",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=有給取得を推奨する文化があり、上司に気を使わず取れるという口コミが多数"
            " | 3=取れるが職場の雰囲気や繁忙期に依存する"
            " | 1=有給を取りにくい雰囲気・取ると白い目で見られるという口コミが複数ある"
        ),
    ),
    (
        "annual_holiday_days",
        "年間休日数",
        "8.待遇・働き方",
        "direct",
        "日",
        100.0,
        130.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    (
        "has_housing_support",
        "住宅補助・寮・社宅の有無",
        "8.待遇・働き方",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    # 9. 採用・選考 〇
    (
        "interview_rounds",
        "選考ステップ数",
        "9.採用・選考",
        "direct",
        "回",
        1.0,
        8.0,
        "neutral",
        0.5,
        False,
        None,
    ),
    (
        "has_early_route",
        "インターン経由の早期優遇ルートの有無",
        "9.採用・選考",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    (
        "salary_disclosed",
        "オファー面談での年収開示",
        "9.採用・選考",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    (
        "f:reverse_q_sincerity",
        "逆質問への誠実さ",
        "9.採用・選考",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=逆質問に現場担当者が具体的・率直に答え、"
            "ネガティブ面も正直に話してくれるという体験談が多い"
            " | 3=答えてはくれるが用意された回答が多い"
            " | 1=逆質問をはぐらかされた・非常に表面的な回答しか得られなかった"
            "という体験談が目立つ"
        ),
    ),
    # 10. 開発環境・技術力 〇
    (
        "f:dev_process_maturity",
        "開発プロセスの成熟度",
        "10.開発環境・技術力",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=スクラムまたはカンバンが適切に運用され、"
            "スプリントレビュー・レトロスペクティブが文化として定着"
            " | 3=アジャイルの名前はあるが形式的で実態はウォーターフォールに近い"
            " | 1=開発プロセスが不明確で場当たり的な開発が多いという口コミが目立つ"
        ),
    ),
    (
        "f:dev_experience_quality",
        "開発者体験の質（マシン・ツール・環境）",
        "10.開発環境・技術力",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=高スペックPC・最新ツール・快適な開発環境が整備されており"
            "エンジニアの生産性に投資している"
            " | 3=標準的な環境は整っているが一部古い機器やツールが残る"
            " | 1=開発環境が貧弱で作業効率の悪さを指摘する口コミが複数ある"
        ),
    ),
    (
        "oss_blog_freq",
        "技術ブログ更新・OSS貢献頻度",
        "10.開発環境・技術力",
        "direct",
        "本/月",
        0.0,
        50.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    (
        "github_activity_score",
        "GitHub 開発活動スコア（公開リポジトリ・スター・更新頻度の複合指標）",
        "10.開発環境・技術力",
        "direct",
        "pts",
        0.0,
        100.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    # ==================================================================
    # 再考✕ items (default_weight=0.2): 低weightで採用
    # ==================================================================
    (
        "stability",
        "企業安定性（自己資本比率・黒字継続等のproxy）",
        "4.財務",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.2,
        False,
        (
            "5=長期的に黒字継続かつ自己資本比率が高く、財務リスクが低い"
            " | 3=利益は出ているが成長のための負債があり安定と成長のバランス型"
            " | 1=赤字継続または資金繰りリスクが高く倒産・事業撤退の懸念がある"
        ),
    ),
    (
        "diversity",
        "ダイバーシティ・多様性",
        "6.組織・カルチャー",
        "scale5",
        None,
        1.0,
        5.0,
        "neutral",
        0.2,
        False,
        (
            "5=女性管理職比率・外国籍社員・障がい者雇用など多様性指標が高水準で"
            "インクルーシブな文化が証拠付きで示されている"
            " | 3=数値目標はあるが達成途上"
            " | 1=同質的な組織構成で多様性への取り組みが希薄"
        ),
    ),
    # ==================================================================
    # gap 対応追加（field_mapping.md 未実装項目）
    # ==================================================================
    # 3. ビジネスモデル ★
    (
        "patent_count",
        "特許件数",
        "3.ビジネスモデル",
        "direct",
        "件",
        0.0,
        500.0,
        "high_good",
        1.0,
        False,
        None,
    ),
    # 3. ビジネスモデル 〇 tag 型（マッチングはJaccard / Phase 5）
    (
        "business_domain",
        "コア事業ドメイン",
        "3.ビジネスモデル",
        "tag",
        None,
        None,
        None,
        "neutral",
        0.5,
        False,
        None,
    ),
    (
        "target_market",
        "ターゲット市場（BtoB/BtoC等）",
        "3.ビジネスモデル",
        "tag",
        None,
        None,
        None,
        "neutral",
        0.5,
        False,
        None,
    ),
    # 6. 組織・カルチャー 〇 — 組織構造の独立度
    (
        "organization_type",
        "開発チームの独立度",
        "6.組織・カルチャー",
        "scale5",
        None,
        1.0,
        5.0,
        "high_good",
        0.5,
        False,
        (
            "5=開発チームが事業部から独立し技術戦略を自律的に決定できる"
            " | 3=事業部付きだが開発の裁量がある"
            " | 1=開発は事業部の下請け的役割で技術選定も制約が多い"
        ),
    ),
    # 8. 待遇・働き方 〇 — みなし残業
    (
        "has_fixed_ot",
        "みなし残業（固定残業代）制度の有無",
        "8.待遇・働き方",
        "bool",
        None,
        0.0,
        1.0,
        "low_good",  # ない方が良い（残業代が見えにくくなるため）
        0.5,
        False,
        None,
    ),
    (
        "fixed_ot_hours",
        "みなし残業の時間数",
        "8.待遇・働き方",
        "direct",
        "時間",
        0.0,
        80.0,
        "low_good",
        0.5,
        False,
        None,
    ),
    # 9. 採用・選考 〇
    (
        "placement_guaranteed",
        "配属先・職種の確約の有無",
        "9.採用・選考",
        "bool",
        None,
        0.0,
        1.0,
        "high_good",
        0.5,
        False,
        None,
    ),
    # 2. ビジョン・戦略 〇 — グローバル展開
    (
        "f:global_expansion",
        "グローバル展開度",
        "2.ビジョン・戦略",
        "scale5",
        None,
        1.0,
        5.0,
        "neutral",
        0.3,
        False,
        (
            "5=海外拠点で実際に開発が行われ英語が日常的に使われる"
            " | 3=海外展開はあるが開発は国内中心"
            " | 1=国内専業で海外事業が全くない"
        ),
    ),
]


def run() -> None:
    """マスタデータを投入する。既存行は ON CONFLICT DO NOTHING でスキップ。"""
    try:
        with get_session() as session:
            _insert_industries(session)
            _insert_tech_tags(session)
            _insert_feature_definitions(session)
            log.info("master_data: 投入完了")
    except Exception:
        log.exception("master_data: ロールバック")
        raise


def _insert_industries(session) -> None:
    for name in INDUSTRIES:
        session.execute(
            sa.text(
                "INSERT INTO industry_master (name) VALUES (:name) ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name},
        )
    log.info("industry_master: %d 件処理", len(INDUSTRIES))


def _insert_tech_tags(session) -> None:
    for name, category in TECH_TAGS:
        session.execute(
            sa.text(
                "INSERT INTO tech_tag (name, category) VALUES (:name, :category)"
                " ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "category": category},
        )
    log.info("tech_tag: %d 件処理", len(TECH_TAGS))


def _insert_feature_definitions(session) -> None:
    cols = (
        "feature_key, display_name, category, method, unit,"
        " value_min, value_max, direction, default_weight,"
        " has_official_actual, rubric"
    )
    for row in FEATURE_DEFINITIONS:
        (
            fkey,
            display,
            cat,
            method,
            unit,
            vmin,
            vmax,
            direction,
            weight,
            has_oa,
            rubric,
        ) = row
        session.execute(
            sa.text(
                f"INSERT INTO feature_definition ({cols})"  # noqa: S608
                " VALUES (:fkey, :display, :cat, :method, :unit,"
                "         :vmin, :vmax, :direction, :weight,"
                "         :has_oa, :rubric)"
                " ON CONFLICT (feature_key) DO UPDATE SET"
                "   display_name = EXCLUDED.display_name,"
                "   default_weight = EXCLUDED.default_weight,"
                "   rubric = EXCLUDED.rubric,"
                "   has_official_actual = EXCLUDED.has_official_actual"
            ),
            {
                "fkey": fkey,
                "display": display,
                "cat": cat,
                "method": method,
                "unit": unit,
                "vmin": vmin,
                "vmax": vmax,
                "direction": direction,
                "weight": weight,
                "has_oa": has_oa,
                "rubric": rubric,
            },
        )
    log.info("feature_definition: %d 件処理", len(FEATURE_DEFINITIONS))

    # 検証: scale5でrubricが空のものを警告
    result = session.execute(
        sa.text(
            "SELECT feature_key FROM feature_definition"
            " WHERE method = 'scale5' AND (rubric IS NULL OR rubric = '')"
            " AND is_active"
        )
    )
    missing = [r[0] for r in result]
    if missing:
        log.warning("scale5なのにrubricが空: %s", missing)
    else:
        log.info("rubric検証OK: scale5全件にrubricあり")


if __name__ == "__main__":
    run()
