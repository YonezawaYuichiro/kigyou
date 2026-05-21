"""Phase 1: Anthropic API で関西IT企業候補を生成し CSV に保存する。"""

import json
import logging
import time

import anthropic
import pandas as pd

from backend.config import DATA_DIR, PROMPTS_DIR, settings
from backend.database import get_session
from backend.exceptions import LLMResponseError
from backend.models import ProcessingLog

logger = logging.getLogger(__name__)

OUTPUT_PATH = DATA_DIR / "candidates_raw.csv"
PROMPT_PATH = PROMPTS_DIR / "company_generation.txt"

_CSV_COLUMNS = [
    "name",
    "official_url",
    "hq_prefecture",
    "estimated_category",
    "llm_confidence",
    "tech_stack",
    "hiring_roles",
]


def _load_prompt_template() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _build_prompt(template: str, excluded_names: list[str]) -> str:
    excluded_str = "\n".join(f"- {n}" for n in excluded_names) if excluded_names else "（なし）"
    return template.replace("{count}", str(settings.phase1_companies_per_batch)).replace(
        "{excluded_companies}", excluded_str
    )


def _parse_llm_response(raw: str) -> list[dict]:
    """JSON配列を抽出してパースする。失敗時は LLMResponseError を raise。"""
    text = raw.strip()
    # コードブロック除去
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMResponseError(f"JSONパース失敗: {e}") from e
    if not isinstance(result, list):
        raise LLMResponseError(f"期待する型はlist、実際は{type(result)}")
    return result


def _save_log(
    phase: str,
    status: str,
    duration_ms: int,
    error_message: str | None = None,
    raw_response: str | None = None,
) -> None:
    try:
        with get_session() as session:
            session.add(
                ProcessingLog(
                    phase=phase,
                    status=status,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    raw_response=raw_response,
                )
            )
    except Exception as log_err:
        logger.warning("ProcessingLog 保存失敗: %s", log_err)


def _call_llm_once(
    client: anthropic.Anthropic,
    prompt: str,
    batch_num: int,
) -> list[dict]:
    """1回のLLM呼び出しを実行する。失敗時は空リストを返す。"""
    start = time.monotonic()
    raw_response: str | None = None
    phase = "phase_1_generate"
    try:
        response = client.messages.create(
            model=settings.sonnet_model,
            max_tokens=settings.phase1_max_tokens,
            temperature=settings.phase1_temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_response = response.content[0].text
        companies = _parse_llm_response(raw_response)
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info("バッチ%d: %d社取得", batch_num, len(companies))
        _save_log(phase, "success", duration_ms, raw_response=raw_response)
        return companies
    except LLMResponseError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("バッチ%d JSONパース失敗: %s", batch_num, e)
        _save_log(phase, "failure", duration_ms, str(e), raw_response)
        return []
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("バッチ%d API呼び出し失敗: %s", batch_num, e)
        _save_log(phase, "failure", duration_ms, str(e), raw_response)
        return []
    finally:
        time.sleep(settings.sonnet_sleep_seconds)


def _normalize_row(row: dict) -> dict:
    """JSON列を文字列化し、CSVに保存できる形式に変換する。"""
    return {
        "name": str(row.get("name", "")),
        "official_url": str(row.get("official_url", "")),
        "hq_prefecture": str(row.get("hq_prefecture", "")),
        "estimated_category": str(row.get("estimated_category", "")),
        "llm_confidence": str(row.get("llm_confidence", "low")),
        "tech_stack": json.dumps(row.get("tech_stack", []), ensure_ascii=False),
        "hiring_roles": json.dumps(row.get("hiring_roles", []), ensure_ascii=False),
    }


def _load_existing_names() -> list[str]:
    """DBに登録済みの企業名を返す（除外リストに使用）。"""
    try:
        from sqlalchemy import select

        from backend.models import Company

        with get_session() as session:
            names = session.scalars(select(Company.name)).all()
            return list(names)
    except Exception as e:
        logger.warning("既存企業名の取得失敗（DBなし or 未初期化）: %s", e)
        return []


def generate_candidates() -> None:
    """Phase 1: LLMで企業候補を生成し data/candidates_raw.csv に保存する。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    template = _load_prompt_template()

    all_companies: list[dict] = []
    excluded_names: list[str] = _load_existing_names()
    if excluded_names:
        logger.info("除外リスト: DB登録済み %d社", len(excluded_names))

    for batch_num in range(1, settings.phase1_batch_count + 1):
        logger.info("バッチ %d/%d 開始", batch_num, settings.phase1_batch_count)
        prompt = _build_prompt(template, excluded_names)
        companies = _call_llm_once(client, prompt, batch_num)
        for c in companies:
            if c.get("name") and c["name"] not in excluded_names:
                all_companies.append(_normalize_row(c))
                excluded_names.append(c["name"])

    df = pd.DataFrame(all_companies, columns=_CSV_COLUMNS)
    df.fillna("").to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    logger.info("Phase 1 完了: %d社 → %s", len(df), OUTPUT_PATH)
    print(f"[Phase 1] {len(df)}社を {OUTPUT_PATH} に保存しました。")


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    generate_candidates()
