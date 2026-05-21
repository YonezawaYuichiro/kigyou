"""Phase 2.5: 法人番号公表サイトのスクレイピングで実在確認・法人番号取得を行う。

法人番号公表サイト（https://www.houjin-bangou.nta.go.jp/）に企業名でPOST検索し、
corporate_number・登記住所・法人種別を取得する。
廃業企業は検索除外設定（closeCkbxなし）で自動的に結果に出ないため、
ヒットしない企業は「未登録」または「廃業」として扱う。
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

logger = logging.getLogger(__name__)

INPUT_PATH = DATA_DIR / "verified.csv"
VALIDATED_PATH = DATA_DIR / "validated.csv"
REJECTED_PATH = DATA_DIR / "rejected.csv"

_HOUJIN_BASE_URL = "https://www.houjin-bangou.nta.go.jp"
_SEARCH_URL = f"{_HOUJIN_BASE_URL}/kensaku-kekka.html"
# CSRFトークンフィールド名（サイト側の実装で固定）
_TOKEN_FIELD = "jp.go.nta.houjin_bangou.framework.web.common.CNSFWTokenProcessor.request.token"

# 法人種別プレフィックス（正規化・種別抽出に使用）
_LEGAL_ENTITY_TYPES = [
    "株式会社",
    "合同会社",
    "有限会社",
    "一般財団法人",
    "一般社団法人",
    "公益財団法人",
    "公益社団法人",
    "特定非営利活動法人",
    "持株会社",
    "合名会社",
    "合資会社",
]

_REJECTED_COLUMNS = ["name", "official_url", "reason"]


def _normalize_corp_name(name: str) -> str:
    """比較用に法人種別・空白・記号を除去して正規化する。"""
    normalized = name
    for entity_type in _LEGAL_ENTITY_TYPES:
        normalized = normalized.replace(entity_type, "")
    normalized = re.sub(r"[\s　・【】（）()「」『』、。・]", "", normalized)
    return normalized.strip()


def _extract_official_name(name_raw: str) -> str:
    """検索結果の名称テキストから公式企業名を抽出する。

    検索結果テーブルでは「読み仮名+公式名称」が連結されている場合がある。
    例: "サイバーエージェント株式会社サイバーエージェント"
    → 法人種別が現れた位置から末尾を取り出す。
    """
    for entity_type in _LEGAL_ENTITY_TYPES:
        idx = name_raw.find(entity_type)
        if idx >= 0:
            return name_raw[idx:]
    return name_raw


def _extract_houjin_kind(name_raw: str) -> str:
    """法人名テキストから法人種別を抽出する。"""
    for entity_type in _LEGAL_ENTITY_TYPES:
        if entity_type in name_raw:
            return entity_type
    return "その他"


def _get_csrf_token(client: httpx.Client) -> str:
    """トップページからCSRFトークンを取得する。"""
    resp = client.get(_HOUJIN_BASE_URL + "/")
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")
    token_input = soup.find("input", {"name": _TOKEN_FIELD})
    if not token_input:
        raise ValueError("CSRFトークンが見つかりません。サイトの構造が変わった可能性があります。")
    return str(token_input["value"])


def _search_company(client: httpx.Client, name: str) -> list[dict[str, Any]]:
    """会社名で検索し、結果行のリストを返す。

    closeCkbxを付けないことで廃業・解散法人を除外する。
    見つかった企業はすべて登記が有効な法人。
    """
    token = _get_csrf_token(client)

    post_data = {
        _TOKEN_FIELD: token,
        "houzinNmTxtf": name,
        "houzinNmShTypeRbtn": "2",  # 部分一致
        "_kanaCkbx": "on",
        "_noconvCkbx": "on",
        "_enCkbx": "on",
        "_houzinKdCkbx": "on",
        "_historyCkbx": "on",
        "_hideCkbx": "on",
        # closeCkbx を付けない → 廃業・解散法人を除外
        "_closeCkbx": "on",
        "_chgYmdShTargetCkbx": "on",
    }

    resp = client.post(_SEARCH_URL, data=post_data)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "lxml")

    table = soup.find("table")
    if not table:
        return []

    results: list[dict[str, Any]] = []
    rows = table.find_all("tr")
    for row in rows[1:]:  # ヘッダー行をスキップ
        # 法人番号は <th> に、商号・住所は <td> に入っている
        th = row.find("th")
        cells = row.find_all("td")
        if not th or len(cells) < 2:
            continue
        results.append(
            {
                "corporate_number": th.get_text(strip=True),
                "name_raw": cells[0].get_text(strip=True),
                "address": cells[1].get_text(strip=True),
            }
        )
    return results


def _find_best_match(
    search_name: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """候補リストから最も一致度の高い企業を返す。

    優先順位:
    1. 正規化後の完全一致
    2. 正規化後の部分一致（短い方が長い方に含まれる）
    3. 候補が1件のみ → そのまま返す（名前の表記ゆれ対応）
    """
    normalized_search = _normalize_corp_name(search_name)

    for cand in candidates:
        official = _extract_official_name(cand["name_raw"])
        normalized_cand = _normalize_corp_name(official)
        if normalized_search and normalized_search == normalized_cand:
            return cand

    for cand in candidates:
        official = _extract_official_name(cand["name_raw"])
        normalized_cand = _normalize_corp_name(official)
        if normalized_search and (
            normalized_search in normalized_cand or normalized_cand in normalized_search
        ):
            return cand

    # 1件しか候補がなければ表記ゆれとして採用
    if len(candidates) == 1:
        return candidates[0]

    return None


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
                    phase="phase_2_5_houjin",
                    status=status,
                    target_company=target,
                    duration_ms=duration_ms,
                    error_message=error_message,
                )
            )
    except Exception as log_err:
        logger.warning("ProcessingLog 保存失敗: %s", log_err)


def validate_companies() -> None:
    """Phase 2.5: verified.csv を法人番号公表サイトで照合し validated.csv / rejected.csv に振り分ける。"""
    if not INPUT_PATH.exists():
        raise PhaseInputError(f"{INPUT_PATH} が存在しません。Phase 2 を先に実行してください。")

    df_in = pd.read_csv(INPUT_PATH, encoding="utf-8-sig", keep_default_na=False)

    # rejected.csv が既にあれば読み込んで追記（Phase 2の除外リストを引き継ぐ）
    if REJECTED_PATH.exists():
        existing_rejected = pd.read_csv(
            REJECTED_PATH, encoding="utf-8-sig", keep_default_na=False
        ).to_dict("records")
    else:
        existing_rejected = []

    validated_rows: list[dict] = []
    new_rejected_rows: list[dict] = []

    http_headers = {
        "User-Agent": settings.http_user_agent,
        "Accept-Language": "ja,en;q=0.9",
        "Referer": _HOUJIN_BASE_URL + "/",
    }

    with httpx.Client(
        timeout=settings.http_timeout_seconds * 2,  # 法人番号サイトはやや遅い
        headers=http_headers,
        follow_redirects=True,
    ) as client:
        for _, row in df_in.iterrows():
            name = str(row.get("name", ""))
            url = str(row.get("official_url", ""))
            llm_confidence = str(row.get("llm_confidence", ""))

            start = time.monotonic()
            try:
                candidates = _search_company(client, name)
                best = _find_best_match(name, candidates)
                duration_ms = int((time.monotonic() - start) * 1000)

                if best:
                    enriched = dict(row)
                    enriched["corporate_number"] = best["corporate_number"]
                    enriched["houjin_address"] = best["address"]
                    enriched["houjin_kind"] = _extract_houjin_kind(best["name_raw"])
                    enriched["houjin_source"] = "houjin_scraping"
                    validated_rows.append(enriched)
                    logger.info("✓ %s → %s", name, best["corporate_number"])
                    _save_log(name, "success", duration_ms)
                else:
                    enriched = dict(row)
                    enriched["corporate_number"] = ""
                    enriched["houjin_address"] = ""
                    enriched["houjin_kind"] = ""
                    enriched["houjin_source"] = "not_found"

                    if llm_confidence == "low":
                        # 法人未登録かつ低信頼 → 除外
                        logger.info("✗ %s → 法人未登録+低信頼 → 除外", name)
                        new_rejected_rows.append(
                            {"name": name, "official_url": url, "reason": "法人未登録+低信頼"}
                        )
                        _save_log(name, "failure", duration_ms, "法人未登録+低信頼")
                    else:
                        # 未登録でも信頼度が低くなければ継続（表記ゆれ等の可能性）
                        logger.info("△ %s → 法人番号サイト未登録 → not_found で継続", name)
                        validated_rows.append(enriched)
                        _save_log(name, "partial", duration_ms, "not_found")

            except Exception as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.warning("スクレイピング失敗 %s: %s → scraping_error で継続", name, e)
                enriched = dict(row)
                enriched["corporate_number"] = ""
                enriched["houjin_address"] = ""
                enriched["houjin_kind"] = ""
                enriched["houjin_source"] = "scraping_error"
                validated_rows.append(enriched)
                _save_log(name, "partial", duration_ms, str(e))

            time.sleep(settings.houjin_sleep_seconds)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 空のときもヘッダー付きで出力するため、カラムを明示的に指定する
    _new_fields = ["corporate_number", "houjin_address", "houjin_kind", "houjin_source"]
    val_columns = list(df_in.columns) + [c for c in _new_fields if c not in df_in.columns]
    df_val = pd.DataFrame(validated_rows, columns=val_columns)
    # corporate_number は13桁数字だが文字列として保持する（先頭0は存在しないが型を統一）
    df_val["corporate_number"] = df_val["corporate_number"].astype(str).replace("nan", "")
    df_val.fillna("").to_csv(VALIDATED_PATH, index=False, encoding="utf-8-sig")

    all_rejected = existing_rejected + new_rejected_rows
    pd.DataFrame(all_rejected, columns=_REJECTED_COLUMNS).fillna("").to_csv(
        REJECTED_PATH, index=False, encoding="utf-8-sig"
    )

    logger.info(
        "Phase 2.5 完了: 検証 %d社 / 今回除外追加 %d社",
        len(validated_rows),
        len(new_rejected_rows),
    )
    print(
        f"[Phase 2.5] 検証: {len(validated_rows)}社 → {VALIDATED_PATH}"
        f"\n            除外追加: {len(new_rejected_rows)}社 → {REJECTED_PATH}"
    )


if __name__ == "__main__":
    logging.basicConfig(level=settings.log_level)
    validate_companies()
