"""Phase 3: 公式サイトから技術情報・採用情報を補強する。"""

import json
import logging
import time

import anthropic
import httpx
import pandas as pd
from bs4 import BeautifulSoup

from backend.config import DATA_DIR, settings
from backend.database import get_session
from backend.exceptions import LLMResponseError, PhaseInputError
from backend.models import ProcessingLog

logger = logging.getLogger(__name__)

INPUT_PATH = DATA_DIR / "validated.csv"
OUTPUT_PATH = DATA_DIR / "enriched.csv"

_ENRICHMENT_PROMPT = """\
以下は{company_name}のウェブサイトのテキストです。
次の3項目をJSONで抽出してください。不明な場合は空配列またはnullにしてください。

- tech_stack: 使用技術・プログラミング言語のリスト（例: ["Python", "AWS"]）
- hiring_roles: 採用職種のリスト（例: ["バックエンドエンジニア"]）
- hq_address: 本社住所（都道府県を含む完全な住所、不明な場合はnull）

テキスト:
{text}

JSONのみを出力し、説明文は付けない:
{{"tech_stack": [], "hiring_roles": [], "hq_address": null}}"""


def _fetch_page_text(url: str) -> str | None:
    """URLからHTMLを取得し、テキストを抽出する。取得失敗時はNoneを返す。"""
    try:
        headers = {"User-Agent": settings.http_user_agent}
        resp = httpx.get(
            url, timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        # script/style タグを除去してテキスト抽出
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text[: settings.html_text_max_chars]
    except Exception as e:
        logger.warning("ページ取得失敗 %s: %s", url, e)
        return None


def _parse_enrichment_response(raw: str) -> dict:
    """LLMレスポンスからJSON辞書を抽出する。失敗時は LLMResponseError を raise。"""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]) if len(lines) > 2 else text
    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMResponseError(f"JSONパース失敗: {e}") from e
    if not isinstance(result, dict):
        raise LLMResponseError(f"期待する型はdict、実際は{type(result)}")
    return result


def _extract_fields_with_llm(
    client: anthropic.Anthropic,
    page_text: str,
    company_name: str,
) -> dict:
    """Haiku 4.5 で tech_stack / hiring_roles / hq_address を抽出する。"""
    start = time.monotonic()
    raw_response: str | None = None
    try:
        prompt = _ENRICHMENT_PROMPT.format(company_name=company_name, text=page_text)
        response = client.messages.create(
            model=settings.haiku_model,
            max_tokens=settings.haiku_max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_response = response.content[0].text
        fields = _parse_enrichment_response(raw_response)
        duration_ms = int((time.monotonic() - start) * 1000)
        _save_log(company_name, "success", duration_ms)
        return fields
    except LLMResponseError as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("%s 抽出失敗: %s", company_name, e)
        _save_log(company_name, "failure", duration_ms, str(e), raw_response)
        return {}
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("%s API呼び出し失敗: %s", company_name, e)
        _save_log(company_name, "failure", duration_ms, str(e), raw_response)
        return {}
    finally:
        time.sleep(settings.haiku_sleep_seconds)


def _save_log(
    target: str,
    status: str,
    duration_ms: int,
    error_message: str | None = None,
    raw_response: str | None = None,
) -> None:
    try:
        with get_session() as session:
            session.add(
                ProcessingLog(
                    phase="phase_3_enrich",
                    status=status,
                    target_company=target,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    raw_response=raw_response,
                )
            )
    except Exception as log_err:
        logger.warning("ProcessingLog 保存失敗: %s", log_err)


def enrich_companies() -> None:
    """Phase 3: verified.csv の各企業を公式サイトから情報補強し enriched.csv に保存する。"""
    if not INPUT_PATH.exists():
        raise PhaseInputError(f"{INPUT_PATH} が存在しません。Phase 2.5 を先に実行してください。")

    df_in = pd.read_csv(INPUT_PATH, encoding="utf-8-sig", keep_default_na=False)
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    enriched_rows: list[dict] = []

    for _, row in df_in.iterrows():
        name = str(row.get("name", ""))
        url = str(row.get("final_url", row.get("official_url", "")))
        logger.info("補強中: %s", name)

        page_text = _fetch_page_text(url)
        if page_text is None:
            # ページ取得失敗: LLM推定値をそのまま引き継ぐ
            enriched_row = dict(row)
            enriched_row["data_source"] = "llm_inferred"
            enriched_rows.append(enriched_row)
            continue

        fields = _extract_fields_with_llm(client, page_text, name)

        enriched_row = dict(row)
        # LLMが抽出できた項目は official_site 扱い、できなければ llm_inferred を維持
        if fields.get("tech_stack"):
            enriched_row["tech_stack"] = json.dumps(fields["tech_stack"], ensure_ascii=False)
            enriched_row["data_source"] = "official_site"
        else:
            enriched_row["data_source"] = "llm_inferred"

        if fields.get("hiring_roles"):
            enriched_row["hiring_roles"] = json.dumps(fields["hiring_roles"], ensure_ascii=False)

        if fields.get("hq_address"):
            enriched_row["hq_address"] = fields["hq_address"]
        else:
            enriched_row.setdefault("hq_address", "")

        enriched_rows.append(enriched_row)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame(enriched_rows)
    df_out.fillna("").to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    logger.info("Phase 3 完了: %d社 → %s", len(df_out), OUTPUT_PATH)
    print(f"[Phase 3] {len(df_out)}社を {OUTPUT_PATH} に保存しました。")


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    enrich_companies()
