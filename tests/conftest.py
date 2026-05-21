from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """data/ ディレクトリを tmp_path にリダイレクトする。"""
    import backend.config as config_module
    import backend.seed.verifier as verifier_module

    monkeypatch.setattr(config_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(verifier_module, "INPUT_PATH", tmp_path / "candidates_raw.csv")
    monkeypatch.setattr(verifier_module, "VERIFIED_PATH", tmp_path / "verified.csv")
    monkeypatch.setattr(verifier_module, "REJECTED_PATH", tmp_path / "rejected.csv")
    return tmp_path
