"""差分メトリクス更新バッチ。

- scraped_at が 90 日以上前 or CompanyMetrics 未登録の企業を対象に
  OpenWork + Green を再スクレイピングして DB を更新する。
- --openwork-only / --green-only フラグで片方のみ実行できる。
- --top N フラグで has_current_openings をスコア上位 N 社のみ更新できる。

使い方:
    python -m backend.seed.update_metrics            # 全更新（90日超え）
    python -m backend.seed.update_metrics --top 50  # 上位50社の求人有無のみ更新
    python -m backend.seed.update_metrics --openwork-only
"""

import argparse
import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.sql import func

from backend.config import settings
from backend.database import get_session
from backend.models import Company, CompanyMetrics
from backend.seed.green_scraper import _HEADERS as _GREEN_HEADERS
from backend.seed.green_scraper import _find_best_match as _green_best_match
from backend.seed.green_scraper import _scrape_company_page as _green_scrape
from backend.seed.green_scraper import _search_green
from backend.seed.openwork_scraper import _HEADERS as _OW_HEADERS
from backend.seed.openwork_scraper import _find_best_match as _ow_best_match
from backend.seed.openwork_scraper import _scrape_company_page as _ow_scrape
from backend.seed.openwork_scraper import _search_openwork

logger = logging.getLogger(__name__)

_STALE_DAYS = 90


def _stale_companies(session, days: int = _STALE_DAYS) -> list[Company]:
    """scraped_at が days 日以上前 or CompanyMetrics 未登録の Company リストを返す。"""
    cutoff = datetime.now(tz=UTC) - timedelta(days=days)
    # 未登録
    no_metrics = session.scalars(
        select(Company).where(~Company.id.in_(select(CompanyMetrics.company_id)))
    ).all()
    # 古い
    stale = session.scalars(
        select(Company)
        .join(CompanyMetrics, Company.id == CompanyMetrics.company_id)
        .where(CompanyMetrics.scraped_at < cutoff)
    ).all()
    seen: set[uuid.UUID] = set()
    result = []
    for c in list(no_metrics) + list(stale):
        if c.id not in seen:
            seen.add(c.id)
            result.append(c)
    return result


def _top_companies(session, n: int) -> list[Company]:
    """CompanyMetrics.openwork_score 降順で上位 n 社を返す。"""
    rows = session.scalars(
        select(Company)
        .join(CompanyMetrics, Company.id == CompanyMetrics.company_id)
        .order_by(CompanyMetrics.openwork_score.desc().nullslast())
        .limit(n)
    ).all()
    return list(rows)


def _update_openwork(company_id: uuid.UUID, company_name: str) -> None:
    """1社分の OpenWork データを再取得して DB を更新する。"""
    kwargs = {
        "headers": _OW_HEADERS,
        "timeout": settings.http_timeout_seconds * 3,
        "follow_redirects": True,
    }
    try:
        with httpx.Client(**kwargs) as client:
            candidates = _search_openwork(client, company_name)
            best = _ow_best_match(company_name, candidates)
            if best is None:
                logger.info("△ %s → OpenWork 未登録（スキップ）", company_name)
                return
            metrics = _ow_scrape(client, best["url"])

        with get_session() as session:
            stmt = (
                pg_insert(CompanyMetrics)
                .values(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    openwork_score=metrics["openwork_score"],
                    openwork_review_count=metrics["openwork_review_count"],
                    avg_overtime_hours=metrics["avg_overtime_hours"],
                    paid_leave_rate=metrics["paid_leave_rate"],
                    avg_annual_salary=metrics["avg_annual_salary"],
                    ow_score_treatment=metrics.get("ow_score_treatment"),
                    ow_score_morale=metrics.get("ow_score_morale"),
                    ow_score_openness=metrics.get("ow_score_openness"),
                    ow_score_growth=metrics.get("ow_score_growth"),
                    openwork_url=best["url"],
                )
                .on_conflict_do_update(
                    index_elements=["company_id"],
                    set_={
                        "openwork_score": metrics["openwork_score"],
                        "openwork_review_count": metrics["openwork_review_count"],
                        "avg_overtime_hours": metrics["avg_overtime_hours"],
                        "paid_leave_rate": metrics["paid_leave_rate"],
                        "avg_annual_salary": metrics["avg_annual_salary"],
                        "ow_score_treatment": metrics.get("ow_score_treatment"),
                        "ow_score_morale": metrics.get("ow_score_morale"),
                        "ow_score_openness": metrics.get("ow_score_openness"),
                        "ow_score_growth": metrics.get("ow_score_growth"),
                        "openwork_url": best["url"],
                        "scraped_at": func.now(),
                    },
                )
            )
            session.execute(stmt)
        logger.info("✓ OpenWork 更新: %s score=%s", company_name, metrics["openwork_score"])
    except Exception as e:
        logger.error("OpenWork 更新失敗 %s: %s", company_name, e)
    time.sleep(settings.openwork_sleep_seconds)


def _update_green(company_id: uuid.UUID, company_name: str) -> None:
    """1社分の Green データを再取得して DB を更新する。"""
    kwargs = {
        "headers": _GREEN_HEADERS,
        "timeout": settings.http_timeout_seconds * 3,
        "follow_redirects": True,
    }
    try:
        with httpx.Client(**kwargs) as client:
            candidates = _search_green(client, company_name)
            best = _green_best_match(company_name, candidates)
            if best is None:
                logger.info("△ %s → Green 未登録（スキップ）", company_name)
                return
            data = _green_scrape(client, best["id"])

        with get_session() as session:
            stmt = (
                pg_insert(CompanyMetrics)
                .values(
                    id=uuid.uuid4(),
                    company_id=company_id,
                    new_grad_salary_min=data["salary_min"],
                    new_grad_salary_max=data["salary_max"],
                    remote_work_policy=data["remote_work_policy"],
                    has_current_openings=data["has_current_openings"],
                    green_url=data["green_url"] or None,
                    employee_count=data.get("employee_count"),
                    average_age=data.get("average_age"),
                    capital_10k_yen=data.get("capital_10k_yen"),
                    is_listed=data.get("is_listed"),
                    founded_year=data.get("founded_year"),
                )
                .on_conflict_do_update(
                    index_elements=["company_id"],
                    set_={
                        "new_grad_salary_min": data["salary_min"],
                        "new_grad_salary_max": data["salary_max"],
                        "remote_work_policy": data["remote_work_policy"],
                        "has_current_openings": data["has_current_openings"],
                        "green_url": data["green_url"] or None,
                        "employee_count": data.get("employee_count"),
                        "average_age": data.get("average_age"),
                        "capital_10k_yen": data.get("capital_10k_yen"),
                        "is_listed": data.get("is_listed"),
                        "founded_year": data.get("founded_year"),
                        "scraped_at": func.now(),
                    },
                )
            )
            session.execute(stmt)
        logger.info(
            "✓ Green 更新: %s openings=%s remote=%s employees=%s",
            company_name,
            data["has_current_openings"],
            data["remote_work_policy"],
            data.get("employee_count"),
        )
    except Exception as e:
        logger.error("Green 更新失敗 %s: %s", company_name, e)
    time.sleep(settings.green_sleep_seconds)


def run_update(
    openwork: bool = True,
    green: bool = True,
    top: int | None = None,
    stale_days: int = _STALE_DAYS,
) -> None:
    """差分更新メイン処理。"""
    with get_session() as session:
        if top is not None:
            companies = _top_companies(session, top)
            logger.info("上位 %d 社を対象に更新", len(companies))
        else:
            companies = _stale_companies(session, stale_days)
            logger.info("stale（%d日超え or 未登録）: %d社を対象に更新", stale_days, len(companies))
        # セッション外でアクセスできるよう (id, name) を抽出しておく
        company_list = [(c.id, c.name) for c in companies]

    if not company_list:
        print("更新対象なし")
        return

    for i, (cid, cname) in enumerate(company_list, 1):
        logger.info("[%d/%d] %s", i, len(company_list), cname)
        if openwork:
            _update_openwork(cid, cname)
        if green:
            _update_green(cid, cname)

    print(f"[update_metrics] 完了: {len(company_list)}社処理")


def main() -> None:
    parser = argparse.ArgumentParser(description="企業メトリクス差分更新")
    parser.add_argument("--openwork-only", action="store_true", help="OpenWork のみ更新")
    parser.add_argument("--green-only", action="store_true", help="Green のみ更新")
    parser.add_argument(
        "--top", type=int, default=None, help="上位 N 社の has_current_openings を更新"
    )
    parser.add_argument(
        "--stale-days", type=int, default=_STALE_DAYS, help="何日以上古いデータを再取得するか"
    )
    args = parser.parse_args()

    do_openwork = not args.green_only
    do_green = not args.openwork_only
    run_update(openwork=do_openwork, green=do_green, top=args.top, stale_days=args.stale_days)


if __name__ == "__main__":
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
