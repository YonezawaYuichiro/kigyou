"""Phase 3c: Gemini検索 + Haiku抽出で CompanyDimensions を埋める。

処理フロー:
  1. Company全件取得
  2. Gemini Google Search で4クエリ検索 → テキスト集約
  3. enricher._fetch_page_text() でHP直接取得
  4. 結合テキストをHaikuに渡し★26項目をJSON一括抽出
  5. overall_confidence を算出してDB upsert
  6. ProcessingLog に記録
"""

import json
import logging
import time
import uuid
from datetime import UTC, datetime

import anthropic
import httpx
import sqlalchemy as sa
from bs4 import BeautifulSoup

from backend.config import settings
from backend.database import get_session
from backend.exceptions import LLMResponseError
from backend.models import Company, CompanyDimensions, ProcessingLog

logger = logging.getLogger(__name__)

PHASE = "phase_3c_dimensions"
_DIMS_MAX_TOKENS = 3000
_SEARCH_TEXT_MAX = 4000  # Gemini検索結果の最大文字数（HP分と合わせてHaikuに渡す）
_HP_TEXT_MAX = 2000

_EXTRACTION_PROMPT = """\
以下は{company_name}に関する情報です（企業HP + Web検索結果）。
各項目を指定スキーマでJSONのみ出力してください（説明文・コードブロック不要）。

テキスト:
{text}

出力スキーマ（各フィールドの説明通りに値を設定すること）:
{{
  "new_biz_policy_score": <0.0-1.0 新規事業への積極度。言及なし=0.5>,
  "new_biz_policy_evidence": "<根拠テキスト50字以内。なければ空文字>",
  "competitive_advantage_score": <0.0-1.0 競合優位性の強さ>,
  "competitive_advantage_evidence": "<根拠テキスト50字以内>",
  "has_patent": <true/false/null>,
  "rd_ratio": <0.0-1.0 R&D費/売上比率。不明=null>,
  "rd_ratio_evidence": "<根拠テキスト50字以内>",
  "capex_ratio": <0.0-1.0 設備投資比率。不明=null>,
  "megatrend_alignment": ["AI","自動化","脱炭素","ロボット","クラウド" の中で該当するもの],
  "megatrend_score": <0.0-1.0 トレンド対応度>,
  "psychological_safety_score": <0.0-5.0 心理的安全性。不明=2.5>,
  "psychological_safety_evidence": "<根拠テキスト50字以内>",
  "evaluation_score": <0.0-1.0 評価制度の透明度・公平性>,
  "evaluation_system_type": "<成果主義|年功序列|混在|不明>",
  "career_track_diversity": <true/false/null 専門職ルートあり>,
  "skill_support_score": <0.0-1.0 スキルアップ支援の充実度>,
  "skill_support_items": ["資格取得補助","書籍購入費","研修制度" 等の該当項目],
  "junior_authority_score": <0.0-5.0 若手への裁量度。不明=2.5>,
  "junior_authority_evidence": "<根拠テキスト50字以内>",
  "has_coding_test": <true/false/null>,
  "interviewer_type": "<current_engineer|hr_only|mixed|unknown>",
  "tech_modernity_score": <0.0-5.0 技術スタックのモダン度>,
  "infra_cloud_score": <0.0-5.0 クラウド・コンテナ活用度>,
  "cicd_maturity_score": <0.0-5.0 CI/CD整備度>,
  "hw_sw_integration": <true/false/null エッジAI・IoT等のハード連携>,
  "data_platform_score": <0.0-5.0 データ基盤の充実度>,
  "tech_debt_culture_score": <0.0-5.0 技術的負債への向き合い度>,
  "tech_env_evidence": "<開発環境全般の根拠テキスト100字以内>",
  "low_confidence_fields": ["証拠が見つからなかったフィールド名"]
}}"""


def _search_with_gemini(company_name: str) -> str:
    """Gemini Google Search グラウンディングで4クエリを検索してテキストを集約する。"""
    if not settings.gemini_api_key:
        return ""
    from google import genai
    from google.genai import types

    queries = [
        f"{company_name} 技術スタック 開発環境 エンジニアブログ",
        f"{company_name} 評価制度 キャリアパス 若手 裁量 口コミ",
        f"{company_name} 研究開発費 財務 事業戦略 新規事業",
        f"{company_name} 心理的安全性 社風 エンジニア カルチャー",
    ]
    client = genai.Client(api_key=settings.gemini_api_key)
    results: list[str] = []

    for query in queries:
        try:
            response = client.models.generate_content(
                model=settings.gemini_model,
                contents=query,
                config=types.GenerateContentConfig(
                    tools=[types.Tool(google_search=types.GoogleSearch())]
                ),
            )
            if response.text:
                results.append(response.text[: _SEARCH_TEXT_MAX // len(queries)])
            time.sleep(settings.sonnet_sleep_seconds)
        except Exception as e:
            logger.warning("Gemini検索失敗 '%s': %s", query, e)

    return "\n\n".join(results)


def _fetch_hp_text(url: str) -> str:
    """企業HPからテキストを抽出する。失敗時は空文字列。"""
    try:
        headers = {"User-Agent": settings.http_user_agent}
        resp = httpx.get(
            url, timeout=settings.http_timeout_seconds, headers=headers, follow_redirects=True
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return soup.get_text(separator=" ", strip=True)[:_HP_TEXT_MAX]
    except Exception as e:
        logger.warning("HP取得失敗 %s: %s", url, e)
        return ""


def _extract_dims_with_haiku(client: anthropic.Anthropic, text: str, company_name: str) -> dict:
    """Haiku 4.5 で★26項目をJSON一括抽出する。失敗時は空dict。"""
    start = time.monotonic()
    raw: str | None = None
    try:
        prompt = _EXTRACTION_PROMPT.format(company_name=company_name, text=text[:6000])
        response = client.messages.create(
            model=settings.haiku_model,
            max_tokens=_DIMS_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise LLMResponseError(f"期待する型はdict、実際は{type(result)}")
        duration_ms = int((time.monotonic() - start) * 1000)
        _save_log(company_name, "success", duration_ms)
        return result
    except (json.JSONDecodeError, LLMResponseError) as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("%s Haiku抽出失敗: %s", company_name, e)
        _save_log(company_name, "failure", duration_ms, str(e), raw)
        return {}
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("%s API呼び出し失敗: %s", company_name, e)
        _save_log(company_name, "failure", duration_ms, str(e), raw)
        return {}
    finally:
        time.sleep(settings.haiku_sleep_seconds)


def _compute_confidence(dims: dict) -> float:
    """証拠テキストの充実度から overall_confidence を 0.0-1.0 で算出する。"""
    evidence_fields = [
        "new_biz_policy_evidence",
        "psychological_safety_evidence",
        "junior_authority_evidence",
        "tech_env_evidence",
        "competitive_advantage_evidence",
    ]
    filled = sum(1 for f in evidence_fields if dims.get(f))
    base = filled / len(evidence_fields)
    low_conf_count = len(dims.get("low_confidence_fields") or [])
    penalty = low_conf_count * 0.05
    return round(max(0.0, min(1.0, base - penalty)), 3)


def _upsert_dimensions(company_id: uuid.UUID, dims: dict, sources: list[str]) -> None:
    """CompanyDimensions を INSERT OR UPDATE する。"""
    confidence = _compute_confidence(dims)
    with get_session() as session:
        existing = session.execute(
            sa.select(CompanyDimensions).where(CompanyDimensions.company_id == company_id)
        ).scalar_one_or_none()

        values = {
            "new_biz_policy_score": dims.get("new_biz_policy_score"),
            "new_biz_policy_evidence": dims.get("new_biz_policy_evidence") or None,
            "competitive_advantage_score": dims.get("competitive_advantage_score"),
            "competitive_advantage_evidence": dims.get("competitive_advantage_evidence") or None,
            "has_patent": dims.get("has_patent"),
            "rd_ratio": dims.get("rd_ratio"),
            "rd_ratio_evidence": dims.get("rd_ratio_evidence") or None,
            "capex_ratio": dims.get("capex_ratio"),
            "megatrend_alignment": dims.get("megatrend_alignment"),
            "megatrend_score": dims.get("megatrend_score"),
            "psychological_safety_score": dims.get("psychological_safety_score"),
            "psychological_safety_evidence": dims.get("psychological_safety_evidence") or None,
            "evaluation_score": dims.get("evaluation_score"),
            "evaluation_system_type": dims.get("evaluation_system_type"),
            "career_track_diversity": dims.get("career_track_diversity"),
            "skill_support_score": dims.get("skill_support_score"),
            "skill_support_items": dims.get("skill_support_items"),
            "junior_authority_score": dims.get("junior_authority_score"),
            "junior_authority_evidence": dims.get("junior_authority_evidence") or None,
            "has_coding_test": dims.get("has_coding_test"),
            "interviewer_type": dims.get("interviewer_type"),
            "tech_modernity_score": dims.get("tech_modernity_score"),
            "infra_cloud_score": dims.get("infra_cloud_score"),
            "cicd_maturity_score": dims.get("cicd_maturity_score"),
            "hw_sw_integration": dims.get("hw_sw_integration"),
            "data_platform_score": dims.get("data_platform_score"),
            "tech_debt_culture_score": dims.get("tech_debt_culture_score"),
            "tech_env_evidence": dims.get("tech_env_evidence") or None,
            "extraction_model": settings.haiku_model,
            "overall_confidence": confidence,
            "low_confidence_fields": dims.get("low_confidence_fields"),
            "search_sources_used": sources,
            "last_extracted_at": datetime.now(UTC),
        }

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            session.add(CompanyDimensions(company_id=company_id, **values))


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
                    phase=PHASE,
                    status=status,
                    target_company=target,
                    duration_ms=duration_ms,
                    error_message=error_message,
                    raw_response=raw_response,
                )
            )
    except Exception as e:
        logger.warning("ProcessingLog 保存失敗: %s", e)


def extract_all_companies(limit: int | None = None) -> None:
    """全企業のCompanyDimensionsを抽出・更新する。limit を指定すると処理数を制限できる。"""
    with get_session() as session:
        companies: list[Company] = session.execute(sa.select(Company)).scalars().all()

    if limit:
        companies = companies[:limit]

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    logger.info("ディメンション抽出開始: %d社", len(companies))

    for i, company in enumerate(companies, 1):
        logger.info("[%d/%d] %s", i, len(companies), company.name)

        search_text = _search_with_gemini(company.name)
        hp_text = _fetch_hp_text(company.official_url)
        combined = f"[検索結果]\n{search_text}\n\n[公式HP]\n{hp_text}".strip()

        if not combined:
            logger.warning("%s: テキスト取得失敗、スキップ", company.name)
            continue

        sources = [company.official_url]
        dims = _extract_dims_with_haiku(client, combined, company.name)

        if dims:
            _upsert_dimensions(company.id, dims, sources)
            logger.info("  → confidence=%.2f", _compute_confidence(dims))
        else:
            logger.warning("  → 抽出失敗")

    logger.info("ディメンション抽出完了")
    print(f"[Phase 3c] {len(companies)}社のディメンション抽出完了")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=settings.log_level)
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    extract_all_companies(limit=limit_arg)
