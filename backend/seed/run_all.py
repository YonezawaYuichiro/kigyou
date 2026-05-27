"""Step 0 統合実行: Phase 1→2→3→4 を順に実行する。"""

import logging
import sys

import pandas as pd

from backend.config import DATA_DIR, settings
from backend.exceptions import PhaseInputError
from backend.seed.dimensions_extractor import extract_all_companies
from backend.seed.enricher import enrich_companies
from backend.seed.green_scraper import scrape_green
from backend.seed.houjin_lookup import validate_companies
from backend.seed.llm_generator import generate_candidates
from backend.seed.loader import load_companies, load_green_metrics, load_openwork_metrics
from backend.seed.openwork_scraper import scrape_openwork
from backend.seed.vector_builder import build_all_vectors
from backend.seed.verifier import verify_candidates

logger = logging.getLogger(__name__)


def _check_phase_output(path_name: str, min_rows: int = 1) -> None:
    """出力CSVが存在して最低行数あることを確認する。なければ PhaseInputError を raise。"""
    path = DATA_DIR / path_name
    if not path.exists():
        raise PhaseInputError(f"{path} が存在しません。")
    df = pd.read_csv(path, encoding="utf-8-sig", keep_default_na=False)
    if len(df) < min_rows:
        raise PhaseInputError(f"{path} の行数が不足しています ({len(df)} 行 < {min_rows} 行)。")
    logger.info("ゲートチェック OK: %s (%d行)", path_name, len(df))


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 60)
    print("[run_all] GradMatch-AI Step 0 開始")
    print("=" * 60)

    try:
        print("\n[Phase 1] LLM候補生成")
        generate_candidates()
        _check_phase_output("candidates_raw.csv")

        print("\n[Phase 2] URL検証")
        verify_candidates()
        _check_phase_output("verified.csv")

        print("\n[Phase 2.5] 法人番号公表サイト照合")
        validate_companies()
        _check_phase_output("validated.csv")

        print("\n[Phase 3] 情報補強")
        enrich_companies()
        _check_phase_output("enriched.csv")

        print("\n[Phase 3a] OpenWork スクレイピング")
        scrape_openwork()
        # openwork_data.csv は全社 not_found でも続行（スコアリング任意データ）

        print("\n[Phase 3b] Green スクレイピング")
        scrape_green()
        # green_data.csv は全社 not_found でも続行

        print("\n[Phase 4] DB投入")
        load_companies()
        load_openwork_metrics()
        load_green_metrics()

        print("\n[Phase 3c] ディメンション抽出（Gemini検索 + Haiku）")
        extract_all_companies()

        print("\n[Phase 3d] ベクトル計算（算術計算）")
        build_all_vectors()

    except PhaseInputError as e:
        logger.error("フェーズゲート失敗: %s", e)
        print(f"\n[ERROR] 前フェーズの出力が不正です: {e}", file=sys.stderr)
        sys.exit(1)

    print("\n" + "=" * 60)
    print("[run_all] Step 0 完了！企業マスタ + V2ディメンション/ベクトルが構築されました。")
    print("=" * 60)


if __name__ == "__main__":
    main()
