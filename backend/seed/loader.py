"""Phase 4: enriched.csv / openwork_data.csv / green_data.csv をDBに投入する。"""

import json
import logging
import time
import uuid

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from backend.config import DATA_DIR, settings
from backend.database import get_session, init_db
from backend.exceptions import DataLoadError, PhaseInputError
from backend.models import Company, CompanyField, CompanyMetrics, ProcessingLog

logger = logging.getLogger(__name__)

INPUT_PATH = DATA_DIR / "enriched.csv"  # enricher.py の出力（validated.csv を補強したもの）
OPENWORK_PATH = DATA_DIR / "openwork_data.csv"
GREEN_PATH = DATA_DIR / "green_data.csv"

_CATEGORY_CHOICES = {"自社開発", "SIer", "メーカー情報子会社", "その他"}
_CONFIDENCE_CHOICES = {"high", "medium", "low"}

# data_source ごとの信頼度スコア
_SOURCE_CONFIDENCE: dict[str, float] = {
    "manual": 1.0,
    "houjin_scraping": 0.95,
    "official_site+github": 0.92,
    "github_api": 0.90,
    "official_site": 0.85,
    "llm_inferred": 0.6,
}
_DEFAULT_CONFIDENCE = 0.5


def _parse_json_list(raw: str) -> list:
    """CSV文字列化されたJSONリストをパースする。失敗時は空リストを返す。"""
    if not raw:
        return []
    try:
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


def _build_company_dict(row: pd.Series) -> dict:
    category = str(row.get("estimated_category", "その他"))
    confidence = str(row.get("llm_confidence", "low"))
    _corp_raw = str(row.get("corporate_number", "")).strip()
    corporate_number = _corp_raw if (_corp_raw.isdigit() and len(_corp_raw) == 13) else None
    # houjin_address（登記住所）を優先、なければ LLM 抽出の hq_address を使う
    houjin_address = str(row.get("houjin_address", "")) or None
    hq_address = houjin_address or str(row.get("hq_address", "")) or None
    return {
        "name": str(row.get("name", "")),
        "official_url": str(row.get("official_url", "")),
        "corporate_number": corporate_number,
        "hq_prefecture": str(row.get("hq_prefecture", "")),
        "hq_address": hq_address,
        "estimated_category": category if category in _CATEGORY_CHOICES else "その他",
        "tech_stack": _parse_json_list(str(row.get("tech_stack", "[]"))),
        "hiring_roles": _parse_json_list(str(row.get("hiring_roles", "[]"))),
        "llm_confidence": confidence if confidence in _CONFIDENCE_CHOICES else "low",
        "release_flag": False,
    }


def _upsert_company(session: Session, row: pd.Series) -> uuid.UUID:
    """official_url を衝突キーにして Company を upsert する。"""
    company_dict = _build_company_dict(row)
    stmt = (
        pg_insert(Company)
        .values(id=uuid.uuid4(), **company_dict)
        .on_conflict_do_update(
            index_elements=["official_url"],
            set_={
                "name": company_dict["name"],
                "corporate_number": company_dict["corporate_number"],
                "hq_prefecture": company_dict["hq_prefecture"],
                "hq_address": company_dict["hq_address"],
                "estimated_category": company_dict["estimated_category"],
                "tech_stack": company_dict["tech_stack"],
                "hiring_roles": company_dict["hiring_roles"],
                "llm_confidence": company_dict["llm_confidence"],
                "updated_at": func.now(),
            },
        )
        .returning(Company.id)
    )
    result = session.execute(stmt)
    return result.scalar_one()


def _save_company_fields(
    session: Session,
    company_id: uuid.UUID,
    row: pd.Series,
) -> None:
    """CompanyField レコードを登録/更新する。"""
    data_source = str(row.get("data_source", "llm_inferred"))
    confidence = _SOURCE_CONFIDENCE.get(data_source, _DEFAULT_CONFIDENCE)

    fields_to_save = [
        ("tech_stack", _parse_json_list(str(row.get("tech_stack", "[]")))),
        ("hiring_roles", _parse_json_list(str(row.get("hiring_roles", "[]")))),
        ("hq_address", str(row.get("hq_address", "")) or None),
    ]
    for field_name, raw_value in fields_to_save:
        stmt = (
            pg_insert(CompanyField)
            .values(
                id=uuid.uuid4(),
                company_id=company_id,
                field_name=field_name,
                source=data_source,
                confidence=confidence,
                raw_value=raw_value,
            )
            .on_conflict_do_nothing()
        )
        session.execute(stmt)


def _save_log(status: str, duration_ms: int, error_message: str | None = None) -> None:
    try:
        with get_session() as session:
            session.add(
                ProcessingLog(
                    phase="phase_4_load",
                    status=status,
                    duration_ms=duration_ms,
                    error_message=error_message,
                )
            )
    except Exception as log_err:
        logger.warning("ProcessingLog 保存失敗: %s", log_err)


def _upsert_company_metrics(
    session: Session,
    company_id: uuid.UUID,
    row: pd.Series,
) -> None:
    """CompanyMetrics を company_id でupsert（既存なら上書き）する。"""

    def _float(col: str) -> float | None:
        v = row.get(col, "")
        try:
            return float(v) if str(v) not in ("", "nan", "None") else None
        except (ValueError, TypeError):
            return None

    def _int(col: str) -> int | None:
        v = row.get(col, "")
        try:
            return int(float(v)) if str(v) not in ("", "nan", "None") else None
        except (ValueError, TypeError):
            return None

    stmt = (
        pg_insert(CompanyMetrics)
        .values(
            id=uuid.uuid4(),
            company_id=company_id,
            openwork_score=_float("openwork_score"),
            openwork_review_count=_int("openwork_review_count"),
            avg_overtime_hours=_float("avg_overtime_hours"),
            paid_leave_rate=_float("paid_leave_rate"),
            avg_annual_salary=_int("avg_annual_salary"),
            ow_score_treatment=_float("ow_score_treatment"),
            ow_score_morale=_float("ow_score_morale"),
            ow_score_openness=_float("ow_score_openness"),
            ow_score_growth=_float("ow_score_growth"),
            openwork_url=str(row.get("openwork_url", "")) or None,
        )
        .on_conflict_do_update(
            index_elements=["company_id"],
            set_={
                "openwork_score": _float("openwork_score"),
                "openwork_review_count": _int("openwork_review_count"),
                "avg_overtime_hours": _float("avg_overtime_hours"),
                "paid_leave_rate": _float("paid_leave_rate"),
                "avg_annual_salary": _int("avg_annual_salary"),
                "ow_score_treatment": _float("ow_score_treatment"),
                "ow_score_morale": _float("ow_score_morale"),
                "ow_score_openness": _float("ow_score_openness"),
                "ow_score_growth": _float("ow_score_growth"),
                "openwork_url": str(row.get("openwork_url", "")) or None,
                "scraped_at": func.now(),
            },
        )
    )
    session.execute(stmt)


def load_openwork_metrics() -> None:
    """openwork_data.csv を読み込み CompanyMetrics テーブルに upsert する。"""
    if not OPENWORK_PATH.exists():
        logger.info("openwork_data.csv が存在しないため Phase 3a スキップ")
        return

    df_ow = pd.read_csv(OPENWORK_PATH, encoding="utf-8-sig", keep_default_na=False)
    # openwork_scraping で取得できた行のみ処理
    df_ow = df_ow[df_ow["openwork_source"] == "openwork_scraping"]
    success, failure = 0, 0

    for _, row in df_ow.iterrows():
        name = str(row.get("name", ""))
        official_url = str(row.get("official_url", ""))
        start = time.monotonic()
        try:
            with get_session() as session:
                # official_url で Company を引く
                from sqlalchemy import select

                company = session.scalar(
                    select(Company).where(Company.official_url == official_url)
                )
                if company is None:
                    logger.warning("Company 未登録のためスキップ: %s", name)
                    continue
                _upsert_company_metrics(session, company.id, row)
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info("OpenWork 投入完了: %s", name)
            _save_log("success", duration_ms)
            success += 1
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("OpenWork 投入失敗: %s - %s", name, e)
            _save_log("failure", duration_ms, f"{name}: {e}")
            failure += 1

    logger.info("CompanyMetrics 投入完了: 成功 %d社 / 失敗 %d社", success, failure)
    print(f"[Phase 4 OpenWork] 成功: {success}社 / 失敗: {failure}社")


def _upsert_green_metrics(
    session: Session,
    company_id: uuid.UUID,
    row: pd.Series,
) -> None:
    """green_data.csv の行を CompanyMetrics に upsert する（既存 OpenWork データを上書きしない）。"""

    def _bool(col: str) -> bool | None:
        v = row.get(col, "")
        if str(v).lower() in ("true", "1"):
            return True
        if str(v).lower() in ("false", "0"):
            return False
        return None

    def _int(col: str) -> int | None:
        v = row.get(col, "")
        try:
            return int(float(v)) if str(v) not in ("", "nan", "None") else None
        except (ValueError, TypeError):
            return None

    def _float(col: str) -> float | None:
        v = row.get(col, "")
        try:
            return float(v) if str(v) not in ("", "nan", "None") else None
        except (ValueError, TypeError):
            return None

    remote = str(row.get("remote_work_policy", "")) or None

    stmt = (
        pg_insert(CompanyMetrics)
        .values(
            id=uuid.uuid4(),
            company_id=company_id,
            new_grad_salary_min=_int("salary_min"),
            new_grad_salary_max=_int("salary_max"),
            remote_work_policy=remote,
            has_current_openings=_bool("has_current_openings"),
            green_url=str(row.get("green_url", "")) or None,
            employee_count=_int("employee_count"),
            average_age=_float("average_age"),
            capital_10k_yen=_int("capital_10k_yen"),
            is_listed=_bool("is_listed"),
            founded_year=_int("founded_year"),
        )
        .on_conflict_do_update(
            index_elements=["company_id"],
            set_={
                "new_grad_salary_min": _int("salary_min"),
                "new_grad_salary_max": _int("salary_max"),
                "remote_work_policy": remote,
                "has_current_openings": _bool("has_current_openings"),
                "green_url": str(row.get("green_url", "")) or None,
                "employee_count": _int("employee_count"),
                "average_age": _float("average_age"),
                "capital_10k_yen": _int("capital_10k_yen"),
                "is_listed": _bool("is_listed"),
                "founded_year": _int("founded_year"),
                "scraped_at": func.now(),
            },
        )
    )
    session.execute(stmt)


def load_green_metrics() -> None:
    """green_data.csv を読み込み CompanyMetrics テーブルに upsert する。"""
    if not GREEN_PATH.exists():
        logger.info("green_data.csv が存在しないため Phase 3b スキップ")
        return

    df_gr = pd.read_csv(GREEN_PATH, encoding="utf-8-sig", keep_default_na=False)
    df_gr = df_gr[df_gr["green_source"] == "green_scraping"]
    success, failure = 0, 0

    for _, row in df_gr.iterrows():
        name = str(row.get("name", ""))
        official_url = str(row.get("official_url", ""))
        start = time.monotonic()
        try:
            with get_session() as session:
                from sqlalchemy import select

                company = session.scalar(
                    select(Company).where(Company.official_url == official_url)
                )
                if company is None:
                    logger.warning("Company 未登録のためスキップ: %s", name)
                    continue
                _upsert_green_metrics(session, company.id, row)
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info("Green 投入完了: %s", name)
            _save_log("success", duration_ms)
            success += 1
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("Green 投入失敗: %s - %s", name, e)
            _save_log("failure", duration_ms, f"{name}: {e}")
            failure += 1

    logger.info("Green CompanyMetrics 投入完了: 成功 %d社 / 失敗 %d社", success, failure)
    print(f"[Phase 4 Green] 成功: {success}社 / 失敗: {failure}社")


def load_companies() -> None:
    """Phase 4: enriched.csv を読み込み Company / CompanyField テーブルに upsert する。"""
    if not INPUT_PATH.exists():
        raise PhaseInputError(f"{INPUT_PATH} が存在しません。Phase 3 を先に実行してください。")

    init_db()

    df_in = pd.read_csv(INPUT_PATH, encoding="utf-8-sig", keep_default_na=False)
    success_count = 0
    failure_count = 0

    for _, row in df_in.iterrows():
        name = str(row.get("name", ""))
        start = time.monotonic()
        try:
            with get_session() as session:
                company_id = _upsert_company(session, row)
                _save_company_fields(session, company_id, row)
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info("投入完了: %s", name)
            _save_log("success", duration_ms)
            success_count += 1
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("投入失敗: %s - %s", name, e)
            _save_log("failure", duration_ms, f"{name}: {e}")
            failure_count += 1

    logger.info("Phase 4 完了: 成功 %d社 / 失敗 %d社", success_count, failure_count)
    print(f"[Phase 4] 成功: {success_count}社 / 失敗: {failure_count}社")
    if failure_count > 0:
        raise DataLoadError(f"{failure_count}社のDB投入に失敗しました。ログを確認してください。")


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    load_companies()
