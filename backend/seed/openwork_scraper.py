"""Phase 3a: OpenWork から企業の働き方指標をスクレイピングする。

httpx + BeautifulSoup で実装（Cloudflare ブロックなし確認済み）。
取得データ: 総合スコア / 口コミ件数 / 残業時間 / 有給消化率 / 平均年収
"""

import logging
import re
import time
from typing import Any

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from backend.config import DATA_DIR, settings
from backend.database import get_session
from backend.exceptions import PhaseInputError
from backend.models import ProcessingLog
from backend.seed.houjin_lookup import _normalize_corp_name

logger = logging.getLogger(__name__)

INPUT_PATH = DATA_DIR / "validated.csv"
OUTPUT_PATH = DATA_DIR / "openwork_data.csv"

_BASE_URL = "https://www.openwork.jp"
_SEARCH_URL = f"{_BASE_URL}/company_list"

_OUTPUT_COLUMNS = [
    "name",
    "official_url",
    "openwork_url",
    "openwork_score",
    "openwork_review_count",
    "avg_overtime_hours",
    "paid_leave_rate",
    "avg_annual_salary",
    "ow_score_treatment",
    "ow_score_morale",
    "ow_score_openness",
    "ow_score_growth",
    "openwork_source",
]

_SUB_SCORE_LABELS: dict[str, str] = {
    "待遇面の満足度": "ow_score_treatment",
    "社員の士気": "ow_score_morale",
    "風通しの良さ": "ow_score_openness",
    "20代成長環境": "ow_score_growth",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ja,en-US;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _search_openwork(client: httpx.Client, name: str) -> list[dict[str, str]]:
    """企業名で OpenWork を検索し、候補リストを返す。"""
    resp = client.get(_SEARCH_URL, params={"src_str": name, "sort": "1"})
    # 302 → /search/addcompany → 404: OpenWork に企業が存在しない
    if "/search/addcompany" in str(resp.url):
        return []
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    candidates: list[dict[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        if "company.php" in href and "m_id=" in href:
            text = a.get_text(strip=True)
            if text:
                candidates.append({"name": text, "url": f"{_BASE_URL}{href}"})
    return candidates


def _find_best_match(search_name: str, candidates: list[dict[str, str]]) -> dict[str, str] | None:
    """正規化後の名称で最もマッチする企業URLを返す。"""
    normalized = _normalize_corp_name(search_name)
    for cand in candidates:
        if normalized and normalized == _normalize_corp_name(cand["name"]):
            return cand
    for cand in candidates:
        nc = _normalize_corp_name(cand["name"])
        if normalized and (normalized in nc or nc in normalized):
            return cand
    if len(candidates) == 1:
        return candidates[0]
    return None


def _parse_float(text: str) -> float | None:
    """文字列から最初の数値（小数点あり）を抽出する。"""
    m = re.search(r"[\d.]+", text.replace(",", ""))
    return float(m.group()) if m else None


def _parse_int(text: str) -> int | None:
    """文字列から最初の整数を抽出する。"""
    m = re.search(r"\d+", text.replace(",", ""))
    return int(m.group()) if m else None


def _scrape_company_page(client: httpx.Client, url: str) -> dict[str, Any]:
    """企業ページから指標を取得する。取得できない項目は None。"""
    resp = client.get(url)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    # 総合スコア: <p class="totalEvaluation_item fs-17">3.72</p>
    score: float | None = None
    score_tag = soup.find("p", class_="fs-17")
    if score_tag:
        score = _parse_float(score_tag.get_text(strip=True))

    # 口コミ件数: 最初の "\d+件" にマッチする <span class="fw-b">
    review_count: int | None = None
    for span in soup.find_all("span", class_="fw-b"):
        txt = span.get_text(strip=True)
        if re.match(r"^\d+件$", txt):
            review_count = _parse_int(txt)
            break

    # 残業時間・有給消化率: <dt class="d-ib w-165">ラベル</dt><dd ...><span class="fs-14">値</span>...
    overtime: float | None = None
    leave_rate: float | None = None
    for dt in soup.find_all("dt", class_="d-ib"):
        label = dt.get_text(strip=True)
        dd = dt.find_next_sibling("dd")
        if dd is None:
            continue
        val_span = dd.find("span")
        if val_span is None:
            continue
        val_text = val_span.get_text(strip=True)
        if "残業時間" in label:
            overtime = _parse_float(val_text)
        elif "有給" in label and overtime is not None:
            # 最初の残業時間取得後の有給（ページ上部の集計値）
            raw = _parse_float(val_text)
            if raw is not None:
                leave_rate = raw / 100.0  # % → 0.0〜1.0
            break

    # 平均年収: <th ...>回答者の平均年収：</th><td ...><span class="fs-22 fw-b">719</span>...
    salary: int | None = None
    th = soup.find("th", string=re.compile(r"平均年収"))
    if th:
        td = th.find_next_sibling("td")
        if td:
            sal_span = td.find("span", class_="fs-22")
            if sal_span:
                salary = _parse_int(sal_span.get_text(strip=True))

    # サブスコア: <dt>ラベル</dt><dd>3.5</dd>（span なし、直接テキスト）
    sub_scores: dict[str, float | None] = dict.fromkeys(_SUB_SCORE_LABELS.values())
    for dt in soup.find_all("dt"):
        label = dt.get_text(strip=True)
        for jp_label, field in _SUB_SCORE_LABELS.items():
            if jp_label in label and sub_scores[field] is None:
                dd = dt.find_next_sibling("dd")
                if dd:
                    val = _parse_float(dd.get_text(strip=True))
                    if val is not None and 0.0 <= val <= 5.0:
                        sub_scores[field] = val

    return {
        "openwork_score": score,
        "openwork_review_count": review_count,
        "avg_overtime_hours": overtime,
        "paid_leave_rate": leave_rate,
        "avg_annual_salary": salary,
        **sub_scores,
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
                    phase="phase_3a_openwork",
                    status=status,
                    target_company=target,
                    duration_ms=duration_ms,
                    error_message=error_message,
                )
            )
    except Exception as log_err:
        logger.warning("ProcessingLog 保存失敗: %s", log_err)


def scrape_openwork() -> None:
    """Phase 3a: validated.csv の企業を OpenWork でスクレイピングし openwork_data.csv に保存する。"""
    if not INPUT_PATH.exists():
        raise PhaseInputError(f"{INPUT_PATH} が存在しません。Phase 2.5 を先に実行してください。")

    df_in = pd.read_csv(INPUT_PATH, encoding="utf-8-sig", keep_default_na=False)
    rows: list[dict] = []

    # 接続リセット後にDNS失敗が連鎖するためクライアントを企業ごとに生成する
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
                candidates = _search_openwork(client, name)
                best = _find_best_match(name, candidates)
                if best is None:
                    logger.info("△ %s → OpenWork 未登録", name)
                    rows.append(
                        {
                            "name": name,
                            "official_url": official_url,
                            "openwork_url": "",
                            "openwork_score": None,
                            "openwork_review_count": None,
                            "avg_overtime_hours": None,
                            "paid_leave_rate": None,
                            "avg_annual_salary": None,
                            "openwork_source": "not_found",
                        }
                    )
                    _save_log(name, "partial", int((time.monotonic() - start) * 1000), "not_found")
                else:
                    metrics = _scrape_company_page(client, best["url"])
                    duration_ms = int((time.monotonic() - start) * 1000)
                    logger.info(
                        "✓ %s → score=%s overtime=%s",
                        name,
                        metrics["openwork_score"],
                        metrics["avg_overtime_hours"],
                    )
                    rows.append(
                        {
                            "name": name,
                            "official_url": official_url,
                            "openwork_url": best["url"],
                            **metrics,
                            "openwork_source": "openwork_scraping",
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
                    "openwork_url": "",
                    "openwork_score": None,
                    "openwork_review_count": None,
                    "avg_overtime_hours": None,
                    "paid_leave_rate": None,
                    "avg_annual_salary": None,
                    "openwork_source": "scraping_error",
                }
            )
            _save_log(name, "failure", duration_ms, str(e))

        time.sleep(settings.openwork_sleep_seconds)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_out = pd.DataFrame(rows, columns=_OUTPUT_COLUMNS)
    df_out.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")
    found = (df_out["openwork_source"] == "openwork_scraping").sum()
    logger.info("Phase 3a 完了: %d社取得 / %d社中", found, len(df_out))
    print(f"[Phase 3a] {found}社取得 / {len(df_out)}社中 → {OUTPUT_PATH}")


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    scrape_openwork()
