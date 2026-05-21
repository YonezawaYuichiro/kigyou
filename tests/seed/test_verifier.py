"""Phase 2 verifier のユニットテスト。ネットワークアクセスなし。"""

import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pandas as pd
import pytest

from backend.seed.verifier import _verify_url, verify_candidates

# ── _verify_url 単体テスト ──────────────────────────────────────────────────


def _make_response(status_code: int, url: str = "https://example.com") -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.url = httpx.URL(url)
    return resp


def test_verify_url_returns_true_on_200() -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = _make_response(200)

    ok, reason, final_url = _verify_url("https://example.com", client)

    assert ok is True
    assert reason == "HTTP 200"
    assert final_url == "https://example.com"


def test_verify_url_follows_redirect() -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = _make_response(200, url="https://example.com/redirected/")

    ok, reason, final_url = _verify_url("https://example.com", client)

    assert ok is True
    assert "200" in reason
    assert "redirected" in final_url


def test_verify_url_returns_false_on_404() -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.return_value = _make_response(404)

    ok, reason, _ = _verify_url("https://example.com", client)

    assert ok is False
    assert reason == "HTTP 404"


def test_verify_url_handles_timeout() -> None:
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.TimeoutException("timeout")

    ok, reason, _ = _verify_url("https://example.com", client)

    assert ok is False
    assert reason == "TIMEOUT"


def test_verify_url_ssl_error_retries_without_verify() -> None:
    """SSL証明書エラー時に verify=False で再試行して成功すること。"""
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("SSL error")

    retry_response = _make_response(200)
    with patch("backend.seed.verifier.httpx.get", return_value=retry_response) as mock_get:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            ok, reason, _ = _verify_url("https://example.com", client)

    assert ok is True
    assert "SSL_SKIP" in reason
    mock_get.assert_called_once()
    assert mock_get.call_args.kwargs.get("verify") is False
    # warning が発行されていること
    assert any("SSL" in str(w.message) for w in caught)


def test_verify_url_ssl_retry_also_fails() -> None:
    """SSL再試行も失敗した場合は rejected になること。"""
    client = MagicMock(spec=httpx.Client)
    client.get.side_effect = httpx.ConnectError("SSL error")

    with patch("backend.seed.verifier.httpx.get", side_effect=Exception("再試行も失敗")):
        ok, reason, _ = _verify_url("https://example.com", client)

    assert ok is False
    assert "CONNECT_ERROR" in reason


# ── verify_candidates 統合テスト ───────────────────────────────────────────


_CANDIDATE_COLUMNS = [
    "name",
    "official_url",
    "hq_prefecture",
    "estimated_category",
    "llm_confidence",
    "tech_stack",
    "hiring_roles",
]


def _write_candidates_csv(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows, columns=_CANDIDATE_COLUMNS)
    df.fillna("").to_csv(path, index=False, encoding="utf-8-sig")


@pytest.fixture
def mock_db_log() -> MagicMock:
    with patch("backend.seed.verifier.get_session") as mock:
        ctx = MagicMock()
        ctx.__enter__ = MagicMock(return_value=MagicMock())
        ctx.__exit__ = MagicMock(return_value=False)
        mock.return_value = ctx
        yield mock


def test_verify_candidates_with_empty_input(tmp_data_dir: Path, mock_db_log: MagicMock) -> None:
    """空CSVを入力した場合、verified/rejected が空で作成されること。"""
    _write_candidates_csv(
        tmp_data_dir / "candidates_raw.csv",
        [],
    )
    with patch("backend.seed.verifier.httpx.Client"):
        verify_candidates()

    assert (tmp_data_dir / "verified.csv").exists()
    assert (tmp_data_dir / "rejected.csv").exists()
    assert len(pd.read_csv(tmp_data_dir / "verified.csv", encoding="utf-8-sig")) == 0
    assert len(pd.read_csv(tmp_data_dir / "rejected.csv", encoding="utf-8-sig")) == 0


def test_verify_candidates_all_rejected(tmp_data_dir: Path, mock_db_log: MagicMock) -> None:
    """全URL失敗でも verified.csv（0行）は作成されること。"""
    _write_candidates_csv(
        tmp_data_dir / "candidates_raw.csv",
        [{"name": "テスト株式会社", "official_url": "https://example.com"}],
    )
    mock_client = MagicMock(spec=httpx.Client)
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.side_effect = httpx.TimeoutException("timeout")

    with patch("backend.seed.verifier.httpx.Client", return_value=mock_client):
        with patch("backend.seed.verifier.time.sleep"):
            verify_candidates()

    verified = pd.read_csv(tmp_data_dir / "verified.csv", encoding="utf-8-sig")
    rejected = pd.read_csv(tmp_data_dir / "rejected.csv", encoding="utf-8-sig")
    assert len(verified) == 0
    assert len(rejected) == 1
    assert rejected.iloc[0]["reason"] == "TIMEOUT"


def test_output_encoding_is_utf8_sig(tmp_data_dir: Path, mock_db_log: MagicMock) -> None:
    """出力CSVが BOM 付き UTF-8（utf-8-sig）で書き込まれること。"""
    _write_candidates_csv(
        tmp_data_dir / "candidates_raw.csv",
        [],
    )
    with patch("backend.seed.verifier.httpx.Client"):
        verify_candidates()

    # BOM は 0xEF 0xBB 0xBF で始まる
    bom = b"\xef\xbb\xbf"
    assert (tmp_data_dir / "verified.csv").read_bytes().startswith(bom)
    assert (tmp_data_dir / "rejected.csv").read_bytes().startswith(bom)
