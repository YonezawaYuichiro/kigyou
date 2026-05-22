"""手動で企業URLを指定してDBに追加するスクリプト。

使い方:
    python -m backend.seed.manual_add https://hutzper.com/ https://example.com/
"""

import json
import logging
import sys
import time

import anthropic
import pandas as pd
from sqlalchemy import select

from backend.config import settings
from backend.database import get_session
from backend.models import Company
from backend.seed.enricher import _fetch_page_text
from backend.seed.loader import _upsert_company

logger = logging.getLogger(__name__)

_EXTRACT_PROMPT = """\
以下のウェブサイトのテキストから企業情報を抽出してください。

テキスト:
{text}

JSONのみ出力（説明文・コードブロック不要）:
{{
  "name": "正式企業名（株式会社含む）",
  "hq_prefecture": "大阪府",
  "estimated_category": "自社開発|SIer|メーカー情報子会社|その他",
  "llm_confidence": "high|medium|low",
  "tech_stack": ["Python", "AWS"],
  "hiring_roles": ["バックエンドエンジニア"],
  "hq_address": "住所またはnull"
}}"""


def _extract_company_info(client: anthropic.Anthropic, page_text: str) -> dict | None:
    """ページテキストから企業情報全体を Haiku で抽出する。"""
    try:
        prompt = _EXTRACT_PROMPT.format(text=page_text)
        resp = client.messages.create(
            model=settings.haiku_model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        return json.loads(raw)
    except Exception as e:
        logger.error("企業情報抽出失敗: %s", e)
        return None
    finally:
        time.sleep(settings.haiku_sleep_seconds)


def add_companies(urls: list[str]) -> None:
    """指定した URL の企業を DB に追加する。"""
    with get_session() as s:
        existing_urls = {c.official_url for c in s.scalars(select(Company)).all()}

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    added = 0

    for url in urls:
        url = url.rstrip("/") + "/"
        if url in existing_urls or url.rstrip("/") in existing_urls:
            print(f"スキップ（登録済み）: {url}")
            continue

        print(f"処理中: {url}")
        page_text = _fetch_page_text(url)
        if page_text is None:
            print(f"  [NG] ページ取得失敗: {url}")
            continue

        info = _extract_company_info(client, page_text)
        if info is None or not info.get("name"):
            print(f"  [NG] 企業情報の抽出失敗: {url}")
            continue

        row = pd.Series(
            {
                "name": info.get("name", ""),
                "official_url": url,
                "final_url": url,
                "hq_prefecture": info.get("hq_prefecture", ""),
                "hq_address": info.get("hq_address") or "",
                "estimated_category": info.get("estimated_category", "その他"),
                "llm_confidence": info.get("llm_confidence", "medium"),
                "tech_stack": json.dumps(info.get("tech_stack", []), ensure_ascii=False),
                "hiring_roles": json.dumps(info.get("hiring_roles", []), ensure_ascii=False),
                "corporate_number": "",
                "houjin_address": "",
                "data_source": "manual",
            }
        )

        try:
            with get_session() as s:
                _upsert_company(s, row)
            print(f"  [OK] 追加: {info['name']} ({url})")
            added += 1
        except Exception as e:
            print(f"  [NG] DB投入失敗: {url} - {e}")

    print(f"\n完了: {added}/{len(urls)}社を追加しました。")


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    if len(sys.argv) < 2:
        print("使い方: python -m backend.seed.manual_add <URL1> [URL2 ...]")
        sys.exit(1)
    add_companies(sys.argv[1:])
