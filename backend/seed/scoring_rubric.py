"""Phase C: チェックリスト方式スコアリングのルーブリック定義。

LLMに「スコアを返せ」ではなく「yes/no/unsureで証拠を確認せよ」と指示し、
Pythonが定義済みの重み付きチェックリストからスコアを機械計算する。

これにより:
  - スコアの計算過程が完全に追跡可能
  - 「なぜ0.7か」を項目ごとに説明できる
  - LLMの判断範囲をyes/no/unsureに限定し判断ブレを減らす

使用方法:
  from backend.seed.scoring_rubric import CULTURE_CHECKLIST, compute_from_checklist
  results = {"失敗許容文化": "yes", "1on1実施": "unsure", ...}
  score = compute_from_checklist(results, CULTURE_CHECKLIST)
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckItem:
    """チェックリストの1項目。"""

    key: str
    weight: float  # 合計が1.0になるように定義
    prompt: str  # LLMへの質問文（テキスト中に証拠があるかをyes/no/unsureで答える）


# スコア変換テーブル（yes/no/unsure → 数値）
_SCORE_MAP = {"yes": 1.0, "unsure": 0.30, "no": 0.0}


def compute_from_checklist(results: dict[str, str], items: list[CheckItem]) -> float:
    """チェックリストの結果からスコアを機械計算する（0.0〜1.0）。

    Args:
        results: LLMが返したyes/no/unsureの辞書 {"key": "yes"|"no"|"unsure"}
        items:   CheckItemのリスト（weightの合計が1.0）

    Returns:
        0.0〜1.0の合成スコア
    """
    score = sum(
        item.weight * _SCORE_MAP.get(results.get(item.key, "unsure"), 0.30) for item in items
    )
    return round(score, 3)


def build_checklist_prompt(items: list[CheckItem], text: str) -> str:
    """チェックリストをLLMプロンプトに変換する。"""
    items_text = "\n".join(f'  "{item.key}": {item.prompt}' for item in items)
    keys_json = "{" + ", ".join(f'"{item.key}": "yes"|"no"|"unsure"' for item in items) + "}"
    return f"""以下のテキストを読み、各チェック項目について証拠が「ある(yes)」「ない(no)」「不明(unsure)」かをJSONで返してください。
スコアは返さず、yes/no/unsureのみで答えてください。

チェック項目:
{items_text}

テキスト:
{text[:4000]}

出力形式（JSONのみ、説明文なし）:
{keys_json}"""


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# dim[5] 社風・カルチャー チェックリスト
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CULTURE_CHECKLIST: list[CheckItem] = [
    CheckItem(
        "失敗許容文化",
        0.25,
        "「挑戦」「失敗を恐れない」「トライアンドエラー」など、失敗を許容する文化の記述があるか",
    ),
    CheckItem(
        "1on1実施",
        0.15,
        "定期的な1on1面談・マネージャーとの定期対話の記述があるか",
    ),
    CheckItem(
        "ボトムアップ意思決定",
        0.20,
        "現場エンジニアの発言権・ボトムアップな意思決定・現場主導の記述があるか",
    ),
    CheckItem(
        "情報共有文化",
        0.20,
        "Slack・社内Wiki・情報をオープンに共有する文化の記述があるか",
    ),
    CheckItem(
        "WLBへの言及",
        0.20,
        "「残業少ない」「ライフバランス重視」「有給取りやすい」等の記述があるか",
    ),
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# dim[4] エンジニア成長支援 チェックリスト
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
GROWTH_SUPPORT_CHECKLIST: list[CheckItem] = [
    CheckItem(
        "資格補助制度",
        0.20,
        "資格取得費用の補助・支援制度の記述があるか",
    ),
    CheckItem(
        "書籍購入補助",
        0.15,
        "書籍・学習教材の購入補助の記述があるか",
    ),
    CheckItem(
        "研修・勉強会",
        0.25,
        "社内勉強会・外部研修参加・輪読会・ハッカソン等の記述があるか",
    ),
    CheckItem(
        "メンター制度",
        0.20,
        "メンター制度・シニアエンジニアによるコードレビュー指導の記述があるか",
    ),
    CheckItem(
        "複線キャリアパス",
        0.20,
        "専門職（テックリード等）と管理職を選べる複線的なキャリアパスの記述があるか",
    ),
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# dim[6] キャリア成長（若手裁量）チェックリスト
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
JUNIOR_AUTHORITY_CHECKLIST: list[CheckItem] = [
    CheckItem(
        "若手リーダー機会",
        0.30,
        "若手・入社数年でリーダーやサブリーダーを担える機会の記述があるか",
    ),
    CheckItem(
        "新規案件への参加",
        0.25,
        "若手が新規プロジェクトや新規事業の立ち上げに参加できる記述があるか",
    ),
    CheckItem(
        "コードレビュー文化",
        0.25,
        "コードレビューが組織的に行われている・PR文化の記述があるか",
    ),
    CheckItem(
        "技術選定への参加",
        0.20,
        "若手・現場エンジニアが技術選定や採用技術の意思決定に関わる記述があるか",
    ),
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# dim[9] 開発環境（CI/CD・DevOps）チェックリスト
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DEV_ENV_CHECKLIST: list[CheckItem] = [
    CheckItem(
        "CI/CD整備",
        0.25,
        "CI/CD・自動テスト・自動デプロイパイプラインの記述があるか",
    ),
    CheckItem(
        "クラウド活用",
        0.25,
        "AWS・GCP・Azure等のクラウドサービスを積極活用している記述があるか",
    ),
    CheckItem(
        "コンテナ活用",
        0.20,
        "Docker・Kubernetes等のコンテナ技術を使用している記述があるか",
    ),
    CheckItem(
        "技術的負債への対応",
        0.15,
        "リファクタリング・技術的負債の解消を評価・推進する文化の記述があるか",
    ),
    CheckItem(
        "開発者体験への投資",
        0.15,
        "開発ツール・PCスペック・開発環境整備への投資・こだわりの記述があるか",
    ),
]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 各次元に対応するチェックリストのマッピング
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
DIM_CHECKLISTS: dict[int, list[CheckItem]] = {
    4: GROWTH_SUPPORT_CHECKLIST,  # エンジニア成長支援
    5: CULTURE_CHECKLIST,  # 社風・カルチャー
    6: JUNIOR_AUTHORITY_CHECKLIST,  # キャリア成長
    9: DEV_ENV_CHECKLIST,  # 開発環境
}
