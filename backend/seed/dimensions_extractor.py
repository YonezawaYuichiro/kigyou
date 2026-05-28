"""Phase 3c: カテゴリ別 Gemini検索 + Haiku抽出で CompanyDimensions と CompanyField を埋める。

処理フロー:
  1. Company全件取得
  2. 9カテゴリ × 3クエリの Gemini Google Search で情報収集
  3. カテゴリごとに Haiku で ★/○ 項目を抽出
  4. ★ 項目 → CompanyDimensions（既存列）にupsert
  5. ○ 項目 → CompanyField EAV にupsert（llm_inferred 行を毎回差し替え）
  6. overall_confidence（全カテゴリ平均）を記録
"""

import json
import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime

import anthropic
import httpx
import sqlalchemy as sa
from bs4 import BeautifulSoup

from backend.config import settings
from backend.database import get_session
from backend.exceptions import LLMResponseError
from backend.models import Company, CompanyDimensions, CompanyField, ProcessingLog

logger = logging.getLogger(__name__)

# 並列処理用セマフォ（API レート制限対策）
_GEMINI_SEMAPHORE = threading.Semaphore(10)
_HAIKU_SEMAPHORE = threading.Semaphore(2)
_thread_local = threading.local()


def _get_anthropic_client() -> anthropic.Anthropic:
    """スレッドローカルな Anthropic クライアントを返す。"""
    if not hasattr(_thread_local, "client"):
        _thread_local.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    return _thread_local.client


PHASE = "phase_3c_dimensions"
_HAIKU_MAX_TOKENS = 1500
_SEARCH_TEXT_MAX_PER_QUERY = 1000  # 3クエリ × 1000字 = 3000字/カテゴリ
_HP_TEXT_MAX = 1500

# 9カテゴリ × 3クエリ（{name} プレースホルダーを .format() で埋める）
_CATEGORY_QUERIES: dict[str, list[str]] = {
    "governance": [
        "{name} 本社 開発拠点 配属先 エンジニア 勤務地",
        "{name} 事業所 オフィス 関西 大阪 東京 開発センター",
        "{name} 設立 会社概要 エンジニア比率 技術者",
    ],
    "vision_strategy": [
        "{name} 新規事業 立ち上げ 経営戦略",
        "{name} 中期経営計画 AI 投資 事業方針",
        "{name} グローバル展開 海外拠点 企業理念 ビジョン",
    ],
    "business_model": [
        "{name} 競合優位性 強み 差別化 理由",
        "{name} 特許 知的財産 技術 参入障壁",
        "{name} コア事業 収益源 SaaS 受託 ビジネスモデル",
    ],
    "finance": [
        "{name} 研究開発費 R&D 売上比率 投資",
        "{name} 設備投資 インフラ投資 CapEx",
        "{name} 売上 業績 成長率 資金調達",
    ],
    "industry_market": [
        "{name} AI 自動化 脱炭素 市場 メガトレンド",
        "{name} 市場規模 成長予測 業界動向",
        "{name} 競合企業 比較 弱み 課題 法規制",
    ],
    "culture_org": [
        "{name} 心理的安全性 カルチャー 社風 挑戦",
        "{name} 組織構造 意思決定 ボトムアップ 開発チーム",
        "{name} 口コミ 退職 離職傾向 openwork 社員",
    ],
    "career_hr": [
        "{name} 評価制度 成果主義 年功序列 フィードバック",
        "{name} キャリアパス 専門職 管理職 複線化",
        "{name} スキルアップ 資格補助 書籍 研修 メンター 若手 裁量",
    ],
    "wlb": [
        "{name} 残業時間 月平均 みなし残業 有給休暇",
        "{name} リモートワーク 在宅 実施率 フレックス",
        "{name} 給与 昇給 住宅補助 寮 社宅 福利厚生",
    ],
    "recruiting": [
        "{name} コーディングテスト 技術試験 採用",
        "{name} 面接 現場エンジニア 選考フロー ステップ",
        "{name} インターン 早期選考 新卒採用 オファー",
    ],
    "tech_env": [
        "{name} 技術スタック 言語 フレームワーク エンジニアブログ",
        "{name} クラウド AWS Azure コンテナ CI/CD",
        "{name} データ基盤 MLOps ハードウェア IoT エッジAI 技術的負債",
    ],
}

# カテゴリ別 Haiku プロンプト（{{/}} でリテラルの中括弧をエスケープ）
_CATEGORY_PROMPTS: dict[str, str] = {
    "governance": """\
{company_name}の拠点情報・基本ガバナンスに関する情報を抽出し、JSONのみ出力してください（コードブロック不要）。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{}},
  "circle": {{
    "dev_location": "<実際の開発チームの拠点・配属先の場所を一文で。本社と異なる場合は明記。なければ null>",
    "engineer_ratio": "<全従業員に占めるエンジニアの割合の目安。例: 約60%。なければ null>",
    "company_history": "<設立からの主な歩みを一文で。なければ null>"
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "vision_strategy": """\
{company_name}のビジョン・経営戦略に関する情報を抽出し、JSONのみ出力してください（コードブロック不要）。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "new_biz_policy_score": <0.0-1.0 新規事業への積極度。言及なし=0.5>,
    "new_biz_policy_evidence": "<根拠テキスト60字以内。なければ空文字>"
  }},
  "circle": {{
    "mission_alignment": "<企業理念の浸透度を一文で。なければ null>",
    "medium_term_plan": "<中期経営計画の主要テーマ。なければ null>",
    "global_expansion": "<海外展開の状況を一文で。なければ null>"
  }},
  "confidence": <この抽出の信頼度 0.0-1.0>,
  "low_confidence_fields": ["証拠が見つからなかったフィールド名"]
}}""",
    "business_model": """\
{company_name}のビジネスモデル・競合優位性に関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "competitive_advantage_score": <0.0-1.0 競合優位性の強さ>,
    "competitive_advantage_evidence": "<根拠テキスト60字以内>",
    "has_patent": <true/false/null>
  }},
  "circle": {{
    "core_business_type": "<SaaS/受託/SI/組み込み/ハードウェア/その他>",
    "target_market": "<BtoB-製造/BtoB-金融/BtoC/BtoBtoC/その他>",
    "billing_model": "<継続課金/プロジェクト型/ハイブリッド/不明>"
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "finance": """\
{company_name}の財務状況・研究開発投資に関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "rd_ratio": <0.0-1.0 R&D費/売上比率。不明=null>,
    "rd_ratio_evidence": "<根拠テキスト60字以内>",
    "capex_ratio": <0.0-1.0 設備投資比率。不明=null>
  }},
  "circle": {{
    "revenue_trend": "<成長/横ばい/縮小/不明>",
    "profitability_evidence": "<収益性の根拠一文。なければ null>",
    "funding_history": "<資金調達履歴の概要。なければ null>"
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "industry_market": """\
{company_name}の業界動向・市場環境に関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "megatrend_alignment": ["AI","自動化","脱炭素","ロボット","クラウド","医療DX","スマートシティ" の中で該当するもの],
    "megatrend_score": <0.0-1.0 業界トレンドへの対応度>
  }},
  "circle": {{
    "market_size_trend": "<業界・市場の成長傾向を一文で>",
    "regulatory_impact": "<法規制・政策の影響を一文で。なければ null>",
    "main_competitors": "<主な競合企業名をカンマ区切りで。なければ null>",
    "company_weakness": "<自社の弱みや課題を一文で。なければ null>"
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "culture_org": """\
{company_name}の組織カルチャー・心理的安全性に関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "psychological_safety_score": <0.0-5.0 心理的安全性。不明=2.5>,
    "psychological_safety_evidence": "<根拠テキスト60字以内>"
  }},
  "circle": {{
    "org_structure_type": "<開発部門独立/事業部付き/マトリクス/不明>",
    "decision_style": "<ボトムアップ/トップダウン/混在/不明>",
    "turnover_tendency": "<退職傾向・離職理由の特徴を一文で。なければ null>",
    "age_structure": "<シニア/若手のバランス。例: 30代中心・若手比率高め。なければ null>",
    "internal_communication": "<社内コミュニケーション手段・活発度。例: Slack活用・技術共有会あり。なければ null>",
    "management_tech_understanding": "<経営陣の技術理解度・現場との距離感を一文で。なければ null>"
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "career_hr": """\
{company_name}の人事制度・キャリアパスに関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "evaluation_score": <0.0-1.0 評価制度の透明度・公平性>,
    "evaluation_system_type": "<成果主義|年功序列|混在|不明>",
    "career_track_diversity": <true/false/null 専門職ルートあり>,
    "skill_support_score": <0.0-1.0 スキルアップ支援の充実度>,
    "skill_support_items": ["資格取得補助","書籍購入費","研修制度","カンファレンス参加費" 等],
    "junior_authority_score": <0.0-5.0 若手への裁量度。不明=2.5>,
    "junior_authority_evidence": "<根拠テキスト60字以内>"
  }},
  "circle": {{
    "eval_feedback_freq": "<月次/四半期/半期/年次/不明>",
    "job_rotation_risk": "<高/中/低/不明>",
    "training_system": "<研修・育成体制の概要を一文で。なければ null>",
    "mentor_system": "<メンター制度・コードレビュー体制を一文で。なければ null>",
    "avg_tenure_years": <平均勤続年数（数値）。不明=null>
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "wlb": """\
{company_name}の待遇・働き方・福利厚生に関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{}},
  "circle": {{
    "deemed_overtime_hours": <みなし残業時間数（整数）。なければ null>,
    "avg_overtime_actual": <月平均実残業時間数（整数）。口コミ・OpenWork等から。なければ null>,
    "salary_growth_evidence": "<入社3-5年後の昇給実態を一文で。なければ null>",
    "paid_leave_rate": <有給取得率 0.0-1.0。なければ null>,
    "remote_actual_rate": <リモートワーク実施率 0.0-1.0。なければ null>,
    "flex_system": <true/false/null フレックスタイム制あり>,
    "housing_support": "<住宅補助・寮・社宅の概要を一文で。なければ null>"
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "recruiting": """\
{company_name}の採用プロセス・選考に関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "has_coding_test": <true/false/null>,
    "interviewer_type": "<current_engineer|hr_only|mixed|unknown>"
  }},
  "circle": {{
    "selection_steps": <選考ステップ数（整数）。なければ null>,
    "intern_fast_track": <true/false/null インターン経由の早期選考あり>,
    "offer_quality": "<オファー面談の質を一文で。なければ null>",
    "candidate_profile": "<求める人物像・カルチャーフィットの要件を一文で。なければ null>",
    "interview_openness": "<逆質問への対応・技術的な質問への誠実さを一文で。なければ null>"
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
    "tech_env": """\
{company_name}の開発環境・技術力に関する情報を抽出し、JSONのみ出力してください。

テキスト:
{text}

出力スキーマ:
{{
  "star": {{
    "tech_modernity_score": <0.0-5.0 技術スタックのモダン度>,
    "infra_cloud_score": <0.0-5.0 クラウド・コンテナ活用度>,
    "cicd_maturity_score": <0.0-5.0 CI/CD整備度>,
    "hw_sw_integration": <true/false/null エッジAI・IoT等のハード連携>,
    "data_platform_score": <0.0-5.0 データ基盤の充実度>,
    "tech_debt_culture_score": <0.0-5.0 技術的負債への向き合い度>,
    "tech_env_evidence": "<開発環境全般の根拠テキスト100字以内>"
  }},
  "circle": {{
    "agile_maturity": "<スクラム定着/試験的導入/ウォーターフォール/不明>",
    "dev_experience_quality": "<開発者体験（PC支給・ツール等）を一文で。なければ null>",
    "oss_contribution": <true/false/null OSS貢献・カンファレンス協賛あり>,
    "tech_blog_active": <true/false/null 技術ブログ活発>
  }},
  "confidence": <0.0-1.0>,
  "low_confidence_fields": ["..."]
}}""",
}


def _search_category(company_name: str, category: str) -> str:
    """カテゴリの3クエリを並列Gemini検索してテキストを集約する。Gemini未設定時は空文字。"""
    if not settings.gemini_api_key:
        return ""
    from google import genai
    from google.genai import types

    queries = [q.format(name=company_name) for q in _CATEGORY_QUERIES[category]]

    def _single_query(query: str) -> str:
        gclient = genai.Client(api_key=settings.gemini_api_key)
        with _GEMINI_SEMAPHORE:
            try:
                response = gclient.models.generate_content(
                    model=settings.gemini_model,
                    contents=query,
                    config=types.GenerateContentConfig(
                        tools=[types.Tool(google_search=types.GoogleSearch())]
                    ),
                )
                return response.text[:_SEARCH_TEXT_MAX_PER_QUERY] if response.text else ""
            except Exception as e:
                logger.warning("Gemini検索失敗 [%s/%s]: %s", category, query[:30], e)
                return ""

    with ThreadPoolExecutor(max_workers=3) as q_pool:
        results = list(q_pool.map(_single_query, queries))

    return "\n\n".join(r for r in results if r)


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


def _extract_category(
    text: str,
    category: str,
    company_name: str,
) -> tuple[dict, dict, float]:
    """カテゴリ専用プロンプトで Haiku 抽出。(star_dict, circle_dict, confidence) を返す。"""
    prompt = _CATEGORY_PROMPTS[category].format(company_name=company_name, text=text[:5000])
    start = time.monotonic()
    raw: str | None = None
    client = _get_anthropic_client()
    try:
        with _HAIKU_SEMAPHORE:
            response = client.messages.create(
                model=settings.haiku_model,
                max_tokens=_HAIKU_MAX_TOKENS,
                messages=[{"role": "user", "content": prompt}],
            )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise LLMResponseError(f"期待する型はdict、実際は{type(result)}")
        star = result.get("star") or {}
        circle = result.get("circle") or {}
        confidence = float(result.get("confidence", 0.5))
        duration_ms = int((time.monotonic() - start) * 1000)
        _save_log(f"{company_name}/{category}", "success", duration_ms)
        return star, circle, confidence
    except (json.JSONDecodeError, LLMResponseError) as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.warning("%s [%s] 抽出失敗: %s", company_name, category, e)
        _save_log(f"{company_name}/{category}", "failure", duration_ms, str(e), raw)
        return {}, {}, 0.0
    except Exception as e:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.error("%s [%s] API失敗: %s", company_name, category, e)
        _save_log(f"{company_name}/{category}", "failure", duration_ms, str(e), raw)
        return {}, {}, 0.0


def _upsert_dimensions(
    company_id: str | uuid.UUID,
    dims: dict,
    sources: list[str],
    overall_confidence: float,
) -> None:
    """CompanyDimensions を INSERT OR UPDATE する（★ 項目のみ）。"""
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
            "overall_confidence": round(overall_confidence, 3),
            "low_confidence_fields": dims.get("low_confidence_fields"),
            "search_sources_used": sources,
            "last_extracted_at": datetime.now(UTC),
        }

        if existing:
            for k, v in values.items():
                setattr(existing, k, v)
        else:
            session.add(CompanyDimensions(company_id=company_id, **values))


def _upsert_circle_fields(company_id: str | uuid.UUID, circle_data: dict) -> None:
    """○ 項目を company_field EAV テーブルに保存する。既存の llm_inferred 行を差し替え。"""
    if not circle_data:
        return
    with get_session() as session:
        session.execute(
            sa.delete(CompanyField).where(
                CompanyField.company_id == company_id,
                CompanyField.source == "llm_inferred",
            )
        )
        for field_name, raw_value in circle_data.items():
            if raw_value is None:
                continue
            stored = raw_value if isinstance(raw_value, (dict, list)) else {"value": raw_value}
            session.add(
                CompanyField(
                    company_id=company_id,
                    field_name=field_name,
                    source="llm_inferred",
                    confidence=0.7,
                    raw_value=stored,
                )
            )


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


def _process_single_company(company_id: str, name: str, url: str | None) -> None:
    """1社分の全カテゴリを並列処理してDBに保存する。"""
    hp_text = _fetch_hp_text(url)
    all_star: dict = {}
    all_circle: dict = {}
    confidences: list[float] = []
    lock = threading.Lock()

    def _process_one_category(category: str) -> None:
        search_text = _search_category(name, category)
        combined = f"[Webサーチ]\n{search_text}\n\n[公式HP]\n{hp_text}".strip()
        if not combined:
            logger.warning("  [%s] テキスト取得失敗、スキップ", category)
            return
        star, circle, conf = _extract_category(combined, category, name)
        with lock:
            all_star.update(star)
            all_circle.update(circle)
            if conf > 0:
                confidences.append(conf)

    with ThreadPoolExecutor(max_workers=10) as cat_pool:
        futures = {cat_pool.submit(_process_one_category, cat): cat for cat in _CATEGORY_QUERIES}
        for future in as_completed(futures):
            cat = futures[future]
            try:
                future.result()
            except Exception as e:
                logger.warning("%s [%s] エラー: %s", name, cat, e)

    overall_conf = round(sum(confidences) / len(confidences), 3) if confidences else 0.0
    _upsert_dimensions(company_id, all_star, [url or ""], overall_conf)
    _upsert_circle_fields(company_id, all_circle)
    logger.info("  ✓ %s overall_confidence=%.2f ○件数=%d件", name, overall_conf, len(all_circle))


def extract_all_companies(limit: int | None = None) -> None:
    """全企業を並列処理する。処理済み企業はスキップ。"""
    with get_session() as session:
        rows = session.execute(sa.select(Company.id, Company.name, Company.official_url)).all()
        done_ids = {
            str(r[0])
            for r in session.execute(
                sa.select(CompanyDimensions.company_id).where(
                    CompanyDimensions.overall_confidence >= 0.4
                )
            ).all()
        }

    companies = [(str(r.id), r.name, r.official_url) for r in rows if str(r.id) not in done_ids]
    if limit:
        companies = companies[:limit]

    total = len(companies)
    logger.info(
        "ディメンション抽出開始（並列処理）: %d社（処理済み %d社スキップ）",
        total,
        len(done_ids),
    )

    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=3) as company_pool:
        futures = {
            company_pool.submit(_process_single_company, cid, name, url): (cid, name)
            for cid, name, url in companies
        }
        for i, future in enumerate(as_completed(futures), 1):
            cid, name = futures[future]
            try:
                future.result()
                completed += 1
            except Exception as e:
                failed += 1
                logger.error("[%d/%d] %s 失敗: %s", i, total, name, e)
            if i % 10 == 0 or i == total:
                logger.info("進捗: %d/%d完了（失敗%d）", completed, total, failed)

    logger.info("ディメンション抽出完了: 成功%d社、失敗%d社", completed, failed)
    print(f"[Phase 3c] {completed}社完了、{failed}社失敗")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=settings.log_level)
    limit_arg = int(sys.argv[1]) if len(sys.argv) > 1 else None
    extract_all_companies(limit=limit_arg)
