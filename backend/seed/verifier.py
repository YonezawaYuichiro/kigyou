"""Phase 2: 公式URLにHTTPアクセスして実在を検証する。"""

import logging
import time
import warnings

import httpx
import pandas as pd

from backend.config import DATA_DIR, settings
from backend.database import get_session
from backend.exceptions import PhaseInputError
from backend.models import ProcessingLog

logger = logging.getLogger(__name__)

INPUT_PATH = DATA_DIR / "candidates_raw.csv"
VERIFIED_PATH = DATA_DIR / "verified.csv"
REJECTED_PATH = DATA_DIR / "rejected.csv"

_VERIFIED_COLUMNS = [
    "name",
    "official_url",
    "final_url",
    "hq_prefecture",
    "estimated_category",
    "llm_confidence",
    "tech_stack",
    "hiring_roles",
    "corporate_number",
]
_REJECTED_COLUMNS = ["name", "official_url", "reason"]


def _classify_http_status(status_code: int) -> tuple[bool, str]:
    if status_code < 400:
        return True, f"HTTP {status_code}"
    return False, f"HTTP {status_code}"


def _verify_url(url: str, client: httpx.Client) -> tuple[bool, str, str]:
    """(ok, reason, final_url) を返す。"""
    try:
        resp = client.get(url, follow_redirects=True)
        ok, reason = _classify_http_status(resp.status_code)
        return ok, reason, str(resp.url)
    except httpx.ConnectError:
        # SSL証明書エラーの場合は verify=False で再試行
        try:
            resp = httpx.get(
                url,
                follow_redirects=True,
                timeout=settings.http_timeout_seconds,
                verify=False,
            )
            warnings.warn(f"SSL証明書検証をスキップしました: {url}", stacklevel=2)
            ok, reason = _classify_http_status(resp.status_code)
            return ok, f"SSL_SKIP {reason}", str(resp.url)
        except Exception as inner:
            return False, f"CONNECT_ERROR: {inner}", url
    except httpx.TimeoutException:
        return False, "TIMEOUT", url
    except Exception as e:
        return False, f"UNKNOWN: {e}", url


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
                    phase="phase_2_verify",
                    status=status,
                    target_company=target,
                    duration_ms=duration_ms,
                    error_message=error_message,
                )
            )
    except Exception as log_err:
        logger.warning("ProcessingLog 保存失敗: %s", log_err)


def verify_candidates() -> None:
    """Phase 2: candidates_raw.csv の各URLを検証し verified/rejected に振り分ける。"""
    if not INPUT_PATH.exists():
        raise PhaseInputError(f"{INPUT_PATH} が存在しません。Phase 1 を先に実行してください。")

    df_in = pd.read_csv(INPUT_PATH, encoding="utf-8-sig", keep_default_na=False)
    verified_rows: list[dict] = []
    rejected_rows: list[dict] = []

    headers = {"User-Agent": settings.http_user_agent}
    with httpx.Client(timeout=settings.http_timeout_seconds, headers=headers) as client:
        for _, row in df_in.iterrows():
            name = str(row.get("name", ""))
            url = str(row.get("official_url", ""))
            if not url:
                rejected_rows.append({"name": name, "official_url": url, "reason": "URL未設定"})
                continue

            start = time.monotonic()
            ok, reason, final_url = _verify_url(url, client)
            duration_ms = int((time.monotonic() - start) * 1000)

            if ok:
                logger.info("✓ %s (%s)", name, reason)
                verified_rows.append(
                    {
                        "name": name,
                        "official_url": url,
                        "final_url": final_url,
                        "hq_prefecture": row.get("hq_prefecture", ""),
                        "estimated_category": row.get("estimated_category", ""),
                        "llm_confidence": row.get("llm_confidence", ""),
                        "tech_stack": row.get("tech_stack", "[]"),
                        "hiring_roles": row.get("hiring_roles", "[]"),
                        # NOTE: corporate_number は Phase 2.5（houjin_lookup.py）で埋める。
                        #       法人番号公表サイト Web-API (App ID 申請制) は使用不可のため、
                        #       スクレイピングで代替する。verified.csv では空文字のまま渡す。
                        "corporate_number": "",
                    }
                )
                _save_log(name, "success", duration_ms)
            else:
                logger.info("✗ %s (%s)", name, reason)
                rejected_rows.append({"name": name, "official_url": url, "reason": reason})
                _save_log(name, "failure", duration_ms, reason)

            time.sleep(settings.url_sleep_seconds)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(verified_rows, columns=_VERIFIED_COLUMNS).fillna("").to_csv(
        VERIFIED_PATH, index=False, encoding="utf-8-sig"
    )
    pd.DataFrame(rejected_rows, columns=_REJECTED_COLUMNS).fillna("").to_csv(
        REJECTED_PATH, index=False, encoding="utf-8-sig"
    )

    logger.info("Phase 2 完了: 通過 %d社 / 除外 %d社", len(verified_rows), len(rejected_rows))
    print(
        f"[Phase 2] 通過: {len(verified_rows)}社 → {VERIFIED_PATH}"
        f"\n         除外: {len(rejected_rows)}社 → {REJECTED_PATH}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    verify_candidates()
