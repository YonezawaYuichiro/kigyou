"""normalizer._normalize の direction 不変条件テスト

不変条件: value_normalized は常に「1.0 = 最も望ましい」になっていること。
  - high_good: 高い raw 値 → 高い normalized 値
  - low_good : 低い raw 値 → 高い normalized 値（反転）
  - neutral  : high_good と同じ向き

この前提が崩れると _compute_subscore で "desired_min/max → value_normalized をそのまま"
という設計が符号逆転を引き起こす。
"""

import pytest

from backend.seed.normalizer import _normalize

# ============================================================
# high_good（年収・スコア等: 高いほど良い）
# ============================================================


def test_high_good_high_value_gets_high_score() -> None:
    # 最大値 → 1.0
    assert _normalize(80.0, 0.0, 80.0, "high_good") == pytest.approx(1.0)


def test_high_good_low_value_gets_low_score() -> None:
    # 最小値 → 0.0
    assert _normalize(0.0, 0.0, 80.0, "high_good") == pytest.approx(0.0)


def test_high_good_mid_value() -> None:
    # 中間 → 0.5
    assert _normalize(40.0, 0.0, 80.0, "high_good") == pytest.approx(0.5)


# ============================================================
# low_good（残業・離職率等: 低いほど良い）
# ============================================================


def test_low_good_low_value_gets_high_score() -> None:
    """残業 0h → スコア 1.0（最も望ましい）"""
    # 残業時間: min=0, max=80, direction=low_good
    assert _normalize(0.0, 0.0, 80.0, "low_good") == pytest.approx(1.0)


def test_low_good_high_value_gets_low_score() -> None:
    """残業 80h → スコア 0.0（最も望ましくない）"""
    assert _normalize(80.0, 0.0, 80.0, "low_good") == pytest.approx(0.0)


def test_low_good_cybozu_overtime() -> None:
    """サイボウズ実態 12h/月 → value_normalized = 1 - 12/80 = 0.85"""
    result = _normalize(12.0, 0.0, 80.0, "low_good")
    assert result == pytest.approx(0.85)


def test_low_good_turnover_low_is_good() -> None:
    """離職率 5% → 高スコア。離職率 40% → 低スコア"""
    score_low = _normalize(5.0, 0.0, 50.0, "low_good")
    score_high = _normalize(40.0, 0.0, 50.0, "low_good")
    assert score_low > score_high


# ============================================================
# neutral（direction 指定が neutral のとき high_good と同じ挙動）
# ============================================================


def test_neutral_same_as_high_good() -> None:
    assert _normalize(60.0, 0.0, 100.0, "neutral") == pytest.approx(0.6)


# ============================================================
# クランプ（範囲外の値を [0, 1] に収める）
# ============================================================


def test_clamp_above_max() -> None:
    # 実態値が max を超えた場合も 1.0 にクランプ
    assert _normalize(120.0, 0.0, 100.0, "high_good") == pytest.approx(1.0)


def test_clamp_below_min() -> None:
    # 実態値が min を下回った場合も 0.0 にクランプ
    assert _normalize(-10.0, 0.0, 100.0, "high_good") == pytest.approx(0.0)


def test_low_good_clamp_below_min_gives_high_score() -> None:
    # low_good で min 未満 → 反転後も 1.0 にクランプ
    assert _normalize(-10.0, 0.0, 80.0, "low_good") == pytest.approx(1.0)


# ============================================================
# エッジケース
# ============================================================


def test_vmin_equals_vmax_returns_half() -> None:
    """min=max の定義ミスはフォールバックで 0.5 を返す。"""
    assert _normalize(5.0, 5.0, 5.0, "high_good") == pytest.approx(0.5)
