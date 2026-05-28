"""意図翻訳エンジン（V3 Phase 4）。

自然言語で書いた就活の「理想像」を Sonnet 4.6 が解釈し、
10次元重みベクトルに変換する。ユーザーがスライダーを操作しなくても
直感的にマッチング重みを調整できる。

使い方:
    weights, explanation = translate_intent(
        "AIを使った開発がしたい、若手でも裁量が大きい会社が良い",
        base_weights=[0.1] * 10
    )
"""

import json
import logging
import time

import anthropic

from backend.config import settings

logger = logging.getLogger(__name__)

_DIM_DESCRIPTIONS = """
dim[0] 立地スコア        : 関西圏・主要都市への近さ。勤務地を関西に限定したい人は高く
dim[1] ビジョン積極度     : 新規事業・チャレンジ文化の強さ。スタートアップ志向なら高く
dim[2] ビジネスモデル堅牢性: 競合優位性・特許・収益安定性。大手・老舗志向なら高く
dim[3] 財務健全性        : R&D投資・設備投資比率。技術投資に積極的な会社が好きなら高く
dim[4] 業界トレンド適合度  : AI・DX・グリーン等のメガトレンドへの対応度
dim[5] 組織カルチャー     : 心理的安全性・風通しの良さ。働きやすさを重視するなら高く
dim[6] キャリア成長支援   : 評価制度・複線キャリア・スキル支援・若手裁量。成長志向なら高く
dim[7] 待遇・WLB        : 残業の少なさ・有給消化・リモート率。プライベート重視なら高く
dim[8] 採用透明度        : コーディングテスト有無・エンジニア面接。実力評価される採用が好きなら高く
dim[9] 開発環境モダン度   : モダン技術スタック・CI/CD・クラウド活用度。技術志向なら高く
"""

_TRANSLATE_PROMPT = """\
あなたは新卒就活の専門家AIです。
ユーザーの「理想の就職先」の自由記述を読んで、10次元の重みベクトルに変換してください。

【10次元の定義】
{dim_descriptions}

【現在の基準重み（参考）】
{base_weights}

【ユーザーの入力】
{free_text}

【変換ルール】
- 合計が1.0になるよう正規化すること
- ユーザーが言及していない次元は基準重みを維持する
- 強調している次元は基準重みの1.5〜2.5倍まで引き上げてよい
- ネガティブな言及（「残業したくない」等）は対応次元を上げる（WLBを重視として解釈）

JSONのみ出力（説明文不要）:
{{
  "weights": [dim0, dim1, dim2, dim3, dim4, dim5, dim6, dim7, dim8, dim9],
  "explanation": "どの次元をなぜ変更したかを100字以内で"
}}"""


def translate_intent(free_text: str, base_weights: list[float]) -> tuple[list[float], str]:
    """自然言語の就活理想像を10次元重みベクトルに変換する。

    Args:
        free_text: ユーザーの自由記述（例:「AIを使った開発がしたい、若手でも裁量が大きい会社」）
        base_weights: 現在の重みベクトル（フォールバックに使用）

    Returns:
        (weights, explanation): 正規化済みの10次元重み + Sonnetの説明文
    """
    if not free_text.strip():
        return base_weights, ""

    prompt = _TRANSLATE_PROMPT.format(
        dim_descriptions=_DIM_DESCRIPTIONS,
        base_weights=", ".join(f"dim[{i}]={w:.3f}" for i, w in enumerate(base_weights)),
        free_text=free_text.strip(),
    )

    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        response = client.messages.create(
            model=settings.sonnet_model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        time.sleep(settings.sonnet_sleep_seconds)

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw

        result = json.loads(raw)
        weights_raw: list[float] = [float(v) for v in result.get("weights", base_weights)]
        explanation: str = str(result.get("explanation", ""))

        if len(weights_raw) != 10:
            logger.warning("重み次元数が不正: %d", len(weights_raw))
            return base_weights, ""

        # L1 正規化（Sonnetが正規化を忘れた場合に備えて）
        total = sum(weights_raw)
        if total <= 0:
            return base_weights, ""
        normalized = [round(w / total, 4) for w in weights_raw]

        return normalized, explanation

    except Exception as e:
        logger.warning("意図翻訳失敗: %s", e)
        return base_weights, f"翻訳失敗: {e}"
