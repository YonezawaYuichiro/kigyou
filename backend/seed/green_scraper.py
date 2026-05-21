"""Phase 3b: Green から企業の求人・リモートワーク情報をスクレイピングする。

__NEXT_DATA__ JSON を利用（BeautifulSoup 不要）。
取得データ: 求人有無 / 給与レンジ / リモートワーク可否

【検索方式】
Green の /search?keyword= は SPA で JS 側が処理するため SSR では無効。
代わりに /sitemap/companies.xml から全社 ID を取得し、
名前→ID のキャッシュを一度だけ構築してから名前検索を行う。
"""

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
import pandas as pd

from backend.config import DATA_DIR, settings
from backend.database import get_session
from backend.exceptions import PhaseInputError
from backend.models import ProcessingLog
from backend.seed.houjin_lookup import _normalize_corp_name

logger = logging.getLogger(__name__)

INPUT_PATH = DATA_DIR / "validated.csv"
OUTPUT_PATH = DATA_DIR / "green_data.csv"
_ID_CACHE_PATH = DATA_DIR / "green_id_cache.json"

_BASE_URL = "https://www.green-japan.com"
_SITEMAP_URL = f"{_BASE_URL}/sitemap/companies.xml"

# キャッシュ: normalized_name -> company_id（モジュール単位シングルトン）
_name_to_id: dict[str, int] | None = None

_OUTPUT_COLUMNS = [
    "name",
    "official_url",
    "green_url",
    "has_current_openings",
    "salary_min",
    "salary_max",
    "remote_work_policy",
    "employee_count",
    "average_age",
    "capital_10k_yen",
    "is_listed",
    "founded_year",
    "green_source",
]

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _get_next_data(client: httpx.Client, url: str) -> dict | None:
    """URL から __NEXT_DATA__ JSON を取得する。取得失敗時は None。"""
    resp = client.get(url)
    resp.raise_for_status()
    m = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', resp.text, re.DOTALL
    )
    if not m:
        return None
    return json.loads(m.group(1))


def _fetch_company_name(company_id: int) -> tuple[int, str | None]:
    """会社ページから fullName を取得する（スレッドセーフ: 1リクエスト/1クライアント）。"""
    try:
        with httpx.Client(headers=_HEADERS, timeout=8, follow_redirects=True) as c:
            resp = c.get(f"{_BASE_URL}/company/{company_id}")
            if resp.status_code != 200:
                return company_id, None
            m = re.search(
                r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                resp.text,
                re.DOTALL,
            )
            if not m:
                return company_id, None
            pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
            name = (pp.get("client") or {}).get("fullName")
            return company_id, name
    except Exception:
        return company_id, None


def _build_id_cache(max_workers: int = 5) -> dict[str, int]:
    """サイトマップから全社IDを取得し、正規化名→IDのキャッシュを構築する（一度だけ実行）。"""
    logger.info("Green ID キャッシュを構築中（初回のみ）...")
    with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as c:
        resp = c.get(_SITEMAP_URL)
        resp.raise_for_status()
    ids = list(map(int, re.findall(r"/company/(\d+)", resp.text)))
    ids.sort(reverse=True)  # 新しい企業（高 ID）から先に確認
    logger.info("サイトマップ: %d社分の ID を取得", len(ids))

    cache: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_company_name, cid): cid for cid in ids}
        done = 0
        for future in as_completed(futures):
            cid, name = future.result()
            done += 1
            if name:
                norm = _normalize_corp_name(name)
                if norm:
                    cache[norm] = cid
            if done % 200 == 0:
                logger.info("  %d / %d 完了", done, len(ids))

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _ID_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Green ID キャッシュ保存: %d社", len(cache))
    return cache


def _get_id_cache() -> dict[str, int]:
    """キャッシュをロードする。なければ構築する。"""
    global _name_to_id
    if _name_to_id is not None:
        return _name_to_id
    if _ID_CACHE_PATH.exists():
        _name_to_id = json.loads(_ID_CACHE_PATH.read_text(encoding="utf-8"))
        logger.info("Green ID キャッシュ読み込み: %d社", len(_name_to_id))
    else:
        _name_to_id = _build_id_cache()
    return _name_to_id


def _search_green(_client: httpx.Client, name: str) -> list[dict[str, Any]]:
    """企業名で Green を検索し、{id, name} のリストを返す（キャッシュ利用）。"""
    cache = _get_id_cache()
    norm = _normalize_corp_name(name)
    if not norm:
        return []
    # 完全一致
    if norm in cache:
        return [{"id": cache[norm], "name": name}]
    # 部分一致（前方 or 後方）
    for cached_norm, cid in cache.items():
        if norm in cached_norm or cached_norm in norm:
            return [{"id": cid, "name": name}]
    return []


def _parse_capital_10k(raw: Any) -> int | None:
    """Green の capital フィールド（整数 or "15億27百万円" 形式）を万円整数に変換する。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    s = str(raw)
    oku = re.search(r"([\d,]+)億", s)
    man = re.search(r"([\d,]+)百?万", s)
    result = 0
    if oku:
        result += int(oku.group(1).replace(",", "")) * 10000
    if man:
        # "百万" なら ×100、"万" なら ×1
        multiplier = 100 if "百万" in s else 1
        result += int(man.group(1).replace(",", "")) * multiplier
    return result if result > 0 else None


def _find_best_match(
    search_name: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """正規化後の名称で最もマッチする企業を返す。"""
    normalized = _normalize_corp_name(search_name)
    for cand in candidates:
        nc = _normalize_corp_name(cand["name"])
        if normalized and normalized == nc:
            return cand
    for cand in candidates:
        nc = _normalize_corp_name(cand["name"])
        if normalized and (normalized in nc or nc in normalized):
            return cand
    return None


def _scrape_company_page(client: httpx.Client, company_id: int) -> dict[str, Any]:
    """企業ページから求人・給与・リモート・企業基本情報を取得する。"""
    url = f"{_BASE_URL}/company/{company_id}"
    data = _get_next_data(client, url)
    if not data:
        return _empty_metrics()

    pp = data.get("props", {}).get("pageProps", {})
    client_data: dict[str, Any] = pp.get("client") or {}
    job_offers = pp.get("clientJobOffers") or []

    has_openings = len(job_offers) > 0

    salaries_min = [jo["minSalary"] for jo in job_offers if jo.get("minSalary") is not None]
    salaries_max = [jo["maxSalary"] for jo in job_offers if jo.get("maxSalary") is not None]
    salary_min = min(salaries_min) if salaries_min else None
    salary_max = max(salaries_max) if salaries_max else None

    remote_flags = [
        jo.get("remoteWorkingFlg") for jo in job_offers if jo.get("remoteWorkingFlg") is not None
    ]
    if not remote_flags:
        remote_policy = None
    elif all(remote_flags):
        remote_policy = "full"
    elif any(remote_flags):
        remote_policy = "partial"
    else:
        remote_policy = "none"

    # 企業基本情報
    employee_count: int | None = client_data.get("employees")
    average_age: float | None = client_data.get("averageAge")
    capital_raw = client_data.get("capital")
    capital_10k_yen: int | None = _parse_capital_10k(capital_raw)
    # stock が None → 非上場、dict（非空）→ 上場
    stock = client_data.get("stock")
    is_listed: bool | None = (stock is not None) if "stock" in client_data else None
    establish_ts = client_data.get("establishTimestamp")
    founded_year: int | None = None
    if establish_ts is not None:
        from datetime import UTC, datetime

        founded_year = datetime.fromtimestamp(establish_ts, tz=UTC).year

    return {
        "has_current_openings": has_openings,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "remote_work_policy": remote_policy,
        "green_url": url,
        "employee_count": employee_count,
        "average_age": average_age,
        "capital_10k_yen": capital_10k_yen,
        "is_listed": is_listed,
        "founded_year": founded_year,
    }


def _empty_metrics() -> dict[str, Any]:
    return {
        "has_current_openings": None,
        "salary_min": None,
        "salary_max": None,
        "remote_work_policy": None,
        "green_url": "",
        "employee_count": None,
        "average_age": None,
        "capital_10k_yen": None,
        "is_listed": None,
        "founded_year": None,
    }


def _save_log(
    target: str,
    status: str,
    duration_ms: int,
    error_message: str | None = None,
) -> None:
    try:
        with get_session() as session:
            session.add(
                ProcessingLog(
                    phase="phase_3b_green",
                    status=status,
                    target_company=target,
                    duration_ms=duration_ms,
                    error_message=error_message,
                )
            )
    except Exception as log_err:
        logger.warning("ProcessingLog 保存失敗: %s", log_err)


def scrape_green() -> None:
    """Phase 3b: validated.csv の企業を Green でスクレイピングし green_data.csv に保存する。"""
    if not INPUT_PATH.exists():
        raise PhaseInputError(f"{INPUT_PATH} が存在しません。Phase 2.5 を先に実行してください。")

    df_in = pd.read_csv(INPUT_PATH, encoding="utf-8-sig", keep_default_na=False)
    rows: list[dict] = []

    _client_kwargs = {
        "headers": _HEADERS,
        "timeout": settings.http_timeout_seconds * 3,
        "follow_redirects": True,
    }

    for _, row in df_in.iterrows():
        name = str(row.get("name", ""))
        official_url = str(row.get("official_url", ""))
        start = time.monotonic()
        try:
            with httpx.Client(**_client_kwargs) as client:
                candidates = _search_green(client, name)
                best = _find_best_match(name, candidates)
                if best is None:
                    logger.info("△ %s → Green 未登録", name)
                    rows.append(
                        {
                            "name": name,
                            "official_url": official_url,
                            **_empty_metrics(),
                            "green_source": "not_found",
                        }
                    )
                    _save_log(name, "partial", int((time.monotonic() - start) * 1000), "not_found")
                else:
                    metrics = _scrape_company_page(client, best["id"])
                    duration_ms = int((time.monotonic() - start) * 1000)
                    logger.info(
                        "✓ %s → openings=%s remote=%s salary=%s-%s",
                        name,
                        metrics["has_current_openings"],
                        metrics["remote_work_policy"],
                        metrics["salary_min"],
                        metrics["salary_max"],
                    )
                    rows.append(
                        {
                            "name": name,
                            "official_url": official_url,
                            **metrics,
                            "green_source": "green_scraping",
                        }
                    )
                    _save_log(name, "success", duration_ms)
        except Exception as e:
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.error("スクレイピング失敗 %s: %s", name, e)
            rows.append(
                {
                    "name": name,
                    "official_url": official_url,
                    **_empty_metrics(),
                    "green_source": "scraping_error",
                }
            )
            _save_log(name, "failure", duration_ms, str(e))

        time.sleep(settings.green_sleep_seconds)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)
    df_out.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    found = (df_out["green_source"] == "green_scraping").sum()
    logger.info("Phase 3b 完了: %d社取得 / %d社中", found, len(df_out))
    print(f"[Phase 3b] {found}社取得 / {len(df_out)}社中 → {OUTPUT_PATH}")


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    scrape_green()
