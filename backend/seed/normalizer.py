"""Phase 2: 特徴量正規化バッチ

company_feature の生値を 0-1 に正規化して value_normalized に書き込む。

正規化ルール:
  - high_good / neutral : (v - min) / (max - min)、クランプ [0, 1]
  - low_good            : 1 - (v - min) / (max - min)、クランプ [0, 1]
  - bool                : 0/1 のままコピー (min=0, max=1 で上式と等価)
  - has_official_actual : value_actual を優先、なければ value_official を使用
  - tag / onehot        : マッチング時に Jaccard 等で計算するため、ここでは対象外

実行:
  python -m backend.seed.normalizer                          # 全社
  python -m backend.seed.normalizer --company-id <UUID>     # 1社指定
"""

import argparse
import logging
import uuid

import sqlalchemy as sa

from backend.database import get_session

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ============================================================
# メイン
# ============================================================


def normalize_all(company_id: uuid.UUID | None = None) -> None:
    with get_session() as session:
        rows = _fetch_features(session, company_id)
        updated = skipped = 0

        for row in rows:
            effective = _effective_value(row)
            if effective is None:
                skipped += 1
                continue

            vn = _normalize(effective, row.value_min, row.value_max, row.direction)

            session.execute(
                sa.text(
                    "UPDATE company_feature"
                    " SET value_normalized = :vn, updated_at = now()"
                    " WHERE id = :id"
                ),
                {"vn": round(vn, 4), "id": str(row.id)},
            )
            updated += 1

        log.info("正規化完了: %d 件更新 / %d 件スキップ（有効値なし）", updated, skipped)
        _print_report(session, company_id)


# ============================================================
# ヘルパー
# ============================================================


def _effective_value(row) -> float | None:
    """制度/実態2スロット対応。value_actual 優先、なければ value_official、それ以外は value_numeric。"""
    if row.has_official_actual:
        if row.value_actual is not None:
            return float(row.value_actual)
        if row.value_official is not None:
            return float(row.value_official)
        return None
    return float(row.value_numeric) if row.value_numeric is not None else None


def _normalize(value: float, vmin: float, vmax: float, direction: str) -> float:
    """[vmin, vmax] → [0, 1] 正規化。low_good は反転。"""
    if vmax == vmin:
        return 0.5  # 定義ミスのフォールバック
    normalized = (value - vmin) / (vmax - vmin)
    normalized = max(0.0, min(1.0, normalized))  # クランプ
    if direction == "low_good":
        normalized = 1.0 - normalized
    return normalized


def _fetch_features(session, company_id: uuid.UUID | None):
    """正規化対象の company_feature を feature_definition と結合して取得。"""
    cid_filter = "AND cf.company_id = :cid" if company_id else ""
    return session.execute(
        sa.text(f"""
            SELECT
                cf.id,
                cf.feature_key,
                cf.value_numeric,
                cf.value_official,
                cf.value_actual,
                fd.method,
                fd.value_min,
                fd.value_max,
                fd.direction,
                fd.has_official_actual
            FROM company_feature cf
            JOIN feature_definition fd ON cf.feature_key = fd.feature_key
            WHERE fd.method IN ('direct', 'scale5', 'bool')
              AND fd.value_min IS NOT NULL
              AND fd.value_max  IS NOT NULL
              {cid_filter}
        """),  # noqa: S608
        {"cid": str(company_id)} if company_id else {},
    ).fetchall()


def _print_report(session, company_id: uuid.UUID | None) -> None:
    """正規化済みスコアを高い順・低い順でサマリー表示。"""
    cid_filter = "AND cf.company_id = :cid" if company_id else ""
    params = {"cid": str(company_id)} if company_id else {}

    rows = session.execute(
        sa.text(f"""
            SELECT
                fd.feature_key,
                fd.display_name,
                fd.direction,
                cf.value_normalized,
                cf.confidence,
                cf.source_type
            FROM company_feature cf
            JOIN feature_definition fd ON cf.feature_key = fd.feature_key
            WHERE cf.value_normalized IS NOT NULL
              {cid_filter}
            ORDER BY cf.value_normalized DESC
        """),  # noqa: S608
        params,
    ).fetchall()

    log.info("=== 正規化スコア一覧（高い順） ===")
    for r in rows:
        bar = "█" * int(r.value_normalized * 10)
        log.info(
            "  %s%-10s  %-35s  %.2f [%s] %s",
            bar.ljust(10),
            "",
            r.feature_key,
            r.value_normalized,
            r.source_type or "---",
            "(推定)" if r.confidence and r.confidence < 0.7 else "",
        )


# ============================================================
# エントリポイント
# ============================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="特徴量正規化バッチ")
    parser.add_argument(
        "--company-id",
        type=uuid.UUID,
        default=None,
        help="対象企業の UUID（省略時は全社）",
    )
    args = parser.parse_args()
    normalize_all(args.company_id)
