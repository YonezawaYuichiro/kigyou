"""Phase 2.5 houjin_lookup.py のユニットテスト（ネットワークアクセスなし）。"""

from unittest.mock import patch

import pandas as pd
import pytest

from backend.seed.houjin_lookup import (
    _extract_houjin_kind,
    _extract_official_name,
    _find_best_match,
    _normalize_corp_name,
    validate_companies,
)

# ---- _normalize_corp_name ----


def test_normalize_removes_kabu_prefix() -> None:
    assert _normalize_corp_name("株式会社サイバーエージェント") == "サイバーエージェント"


def test_normalize_removes_suffix_and_space() -> None:
    assert _normalize_corp_name("サイバーエージェント 株式会社") == "サイバーエージェント"


def test_normalize_empty_string() -> None:
    assert _normalize_corp_name("") == ""


# ---- _extract_official_name ----


def test_extract_official_name_simple() -> None:
    """法人種別から始まる名称はそのまま返す。"""
    assert _extract_official_name("株式会社サイバーエージェント") == "株式会社サイバーエージェント"


def test_extract_official_name_with_kana_prefix() -> None:
    """読み仮名+公式名称の連結形式から公式名称を抽出する。"""
    result = _extract_official_name("サイバーエージェント株式会社サイバーエージェント")
    assert result == "株式会社サイバーエージェント"


def test_extract_official_name_no_entity_type() -> None:
    """法人種別がない場合はそのまま返す。"""
    assert _extract_official_name("DMM.com") == "DMM.com"


# ---- _extract_houjin_kind ----


def test_extract_kind_kabushiki() -> None:
    assert _extract_houjin_kind("株式会社サイバーエージェント") == "株式会社"


def test_extract_kind_godo() -> None:
    assert _extract_houjin_kind("合同会社サンプル") == "合同会社"


def test_extract_kind_unknown() -> None:
    assert _extract_houjin_kind("DMM.com") == "その他"


# ---- _find_best_match ----


def test_find_best_match_exact() -> None:
    candidates = [
        {
            "corporate_number": "1010701020864",
            "name_raw": "株式会社サイバーエージェント",
            "address": "東京都",
        },
        {
            "corporate_number": "9999999999999",
            "name_raw": "合同会社サイバーエージェント",
            "address": "大阪府",
        },
    ]
    result = _find_best_match("株式会社サイバーエージェント", candidates)
    assert result is not None
    assert result["corporate_number"] == "1010701020864"


def test_find_best_match_partial() -> None:
    """法人種別なし入力でも部分一致する。"""
    candidates = [
        {
            "corporate_number": "1010701020864",
            "name_raw": "株式会社サイバーエージェント",
            "address": "東京都",
        },
    ]
    result = _find_best_match("サイバーエージェント", candidates)
    assert result is not None
    assert result["corporate_number"] == "1010701020864"


def test_find_best_match_kana_concat() -> None:
    """読み仮名+公式名称の連結形式でも正しくマッチする。"""
    candidates = [
        {
            "corporate_number": "1010701020864",
            "name_raw": "サイバーエージェント株式会社サイバーエージェント",
            "address": "東京都",
        },
    ]
    result = _find_best_match("株式会社サイバーエージェント", candidates)
    assert result is not None
    assert result["corporate_number"] == "1010701020864"


def test_find_best_match_no_candidate() -> None:
    result = _find_best_match("存在しない会社", [])
    assert result is None


def test_find_best_match_single_candidate_fallback() -> None:
    """候補が1件のみなら表記ゆれとして採用する。"""
    candidates = [
        {"corporate_number": "1234567890123", "name_raw": "サンプル株式会社", "address": "大阪府"},
    ]
    result = _find_best_match("サンプル", candidates)
    assert result is not None
    assert result["corporate_number"] == "1234567890123"


# ---- validate_companies 統合テスト（ネットワークアクセスなし）----


def test_validate_companies_success(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """スクレイピング成功時に validated.csv に corporate_number が記録される。"""

    monkeypatch.setattr("backend.seed.houjin_lookup.INPUT_PATH", tmp_path / "verified.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.VALIDATED_PATH", tmp_path / "validated.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.REJECTED_PATH", tmp_path / "rejected.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.DATA_DIR", tmp_path)

    # テスト用 verified.csv を作成
    df = pd.DataFrame(
        [
            {
                "name": "株式会社テスト",
                "official_url": "https://example.com",
                "final_url": "https://example.com",
                "hq_prefecture": "大阪府",
                "estimated_category": "自社開発",
                "llm_confidence": "high",
                "tech_stack": "[]",
                "hiring_roles": "[]",
                "corporate_number": "",
            }
        ]
    )
    df.to_csv(tmp_path / "verified.csv", index=False, encoding="utf-8-sig")

    # _search_company をモック → 1件ヒット
    mock_candidates = [
        {
            "corporate_number": "1234567890123",
            "name_raw": "株式会社テスト",
            "address": "大阪府大阪市中央区1-1",
        },
    ]
    with (
        patch("backend.seed.houjin_lookup._search_company", return_value=mock_candidates),
        patch("backend.seed.houjin_lookup._save_log"),
        patch("httpx.Client"),
    ):
        validate_companies()

    df_out = pd.read_csv(
        tmp_path / "validated.csv", encoding="utf-8-sig", dtype={"corporate_number": str}
    )
    assert len(df_out) == 1
    assert df_out.iloc[0]["corporate_number"] == "1234567890123"
    assert df_out.iloc[0]["houjin_source"] == "houjin_scraping"


def test_validate_companies_not_found_low_confidence(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ヒットなし + llm_confidence=low → rejected.csv に追加される。"""
    monkeypatch.setattr("backend.seed.houjin_lookup.INPUT_PATH", tmp_path / "verified.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.VALIDATED_PATH", tmp_path / "validated.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.REJECTED_PATH", tmp_path / "rejected.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.DATA_DIR", tmp_path)

    df = pd.DataFrame(
        [
            {
                "name": "架空株式会社",
                "official_url": "https://fake-example.com",
                "final_url": "https://fake-example.com",
                "hq_prefecture": "東京都",
                "estimated_category": "その他",
                "llm_confidence": "low",
                "tech_stack": "[]",
                "hiring_roles": "[]",
                "corporate_number": "",
            }
        ]
    )
    df.to_csv(tmp_path / "verified.csv", index=False, encoding="utf-8-sig")

    with (
        patch("backend.seed.houjin_lookup._search_company", return_value=[]),
        patch("backend.seed.houjin_lookup._save_log"),
        patch("httpx.Client"),
    ):
        validate_companies()

    # validated.csv には入らない
    df_val = pd.read_csv(tmp_path / "validated.csv", encoding="utf-8-sig")
    assert len(df_val) == 0

    # rejected.csv に追加される
    df_rej = pd.read_csv(tmp_path / "rejected.csv", encoding="utf-8-sig")
    assert len(df_rej) == 1
    assert df_rej.iloc[0]["reason"] == "法人未登録+低信頼"


def test_validate_companies_scraping_error_continues(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """スクレイピング例外が起きても処理を継続し scraping_error で validated.csv に入る。"""
    monkeypatch.setattr("backend.seed.houjin_lookup.INPUT_PATH", tmp_path / "verified.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.VALIDATED_PATH", tmp_path / "validated.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.REJECTED_PATH", tmp_path / "rejected.csv")
    monkeypatch.setattr("backend.seed.houjin_lookup.DATA_DIR", tmp_path)

    df = pd.DataFrame(
        [
            {
                "name": "サンプル株式会社",
                "official_url": "https://sample.co.jp",
                "final_url": "https://sample.co.jp",
                "hq_prefecture": "大阪府",
                "estimated_category": "自社開発",
                "llm_confidence": "medium",
                "tech_stack": "[]",
                "hiring_roles": "[]",
                "corporate_number": "",
            }
        ]
    )
    df.to_csv(tmp_path / "verified.csv", index=False, encoding="utf-8-sig")

    with (
        patch(
            "backend.seed.houjin_lookup._search_company", side_effect=Exception("接続タイムアウト")
        ),
        patch("backend.seed.houjin_lookup._save_log"),
        patch("httpx.Client"),
    ):
        validate_companies()

    df_val = pd.read_csv(tmp_path / "validated.csv", encoding="utf-8-sig")
    assert len(df_val) == 1
    assert df_val.iloc[0]["houjin_source"] == "scraping_error"
