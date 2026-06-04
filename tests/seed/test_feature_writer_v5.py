"""feature_writer_v5._convert の単体テスト

_convert() は DB 不要の純関数なので直接テスト可能。
write_from_star / write_from_circle の統合テストは validate_pipeline_v5.py で実行する。
"""

import pytest

from backend.seed.feature_writer_v5 import _convert

# ============================================================
# scale5_direct: Haiku が 0-5 スケールで出力するケース
# ============================================================


def test_scale5_direct_high() -> None:
    assert _convert(5.0, "scale5_direct") == pytest.approx(5.0)


def test_scale5_direct_low() -> None:
    assert _convert(0.0, "scale5_direct") == pytest.approx(0.0)


def test_scale5_direct_mid() -> None:
    assert _convert(2.5, "scale5_direct") == pytest.approx(2.5)


# ============================================================
# pct_to_scale5: Haiku が 0-1 割合で出力 → 1-5 スケールに変換
# ============================================================


def test_pct_to_scale5_zero() -> None:
    # 0.0 → 1 + 0*4 = 1.0
    assert _convert(0.0, "pct_to_scale5") == pytest.approx(1.0)


def test_pct_to_scale5_one() -> None:
    # 1.0 → 1 + 1*4 = 5.0
    assert _convert(1.0, "pct_to_scale5") == pytest.approx(5.0)


def test_pct_to_scale5_half() -> None:
    # 0.5 → 1 + 0.5*4 = 3.0
    assert _convert(0.5, "pct_to_scale5") == pytest.approx(3.0)


def test_pct_to_scale5_typical_haiku_output() -> None:
    # 0.75（サイボウズの competitive_advantage_score）→ 1 + 0.75*4 = 4.0
    assert _convert(0.75, "pct_to_scale5") == pytest.approx(4.0)


# ============================================================
# pct_to_pct100: 0-1 割合 → パーセント値（unit='%' カラム用）
# ============================================================


def test_pct_to_pct100_rd_ratio() -> None:
    # Haiku が 0.25 → 25.0 (%)
    assert _convert(0.25, "pct_to_pct100") == pytest.approx(25.0)


def test_pct_to_pct100_zero() -> None:
    assert _convert(0.0, "pct_to_pct100") == pytest.approx(0.0)


def test_pct_to_pct100_one() -> None:
    assert _convert(1.0, "pct_to_pct100") == pytest.approx(100.0)


# ============================================================
# bool_to_num: bool → 1.0 / 0.0（feature が bool 型のとき）
# ============================================================


def test_bool_to_num_true() -> None:
    assert _convert(True, "bool_to_num") == 1.0


def test_bool_to_num_false() -> None:
    assert _convert(False, "bool_to_num") == 0.0


# ============================================================
# bool_to_scale5: bool → scale5 値への変換
#   f:hw_sw_integration (scale5) は hw_sw_integration (bool) から来る
# ============================================================


def test_bool_to_scale5_true_gives_5() -> None:
    assert _convert(True, "bool_to_scale5") == pytest.approx(5.0)


def test_bool_to_scale5_false_gives_1() -> None:
    # "ハードウェア連携なし" → scale5 最低値 1（高いほど良いので 0 ではなく 1）
    assert _convert(False, "bool_to_scale5") == pytest.approx(1.0)


# ============================================================
# interviewer: interviewer_type 文字列 → field_engineer_joins (bool)
# ============================================================


def test_interviewer_current_engineer() -> None:
    assert _convert("current_engineer", "interviewer") == 1.0


def test_interviewer_hr_only() -> None:
    assert _convert("hr_only", "interviewer") == 0.0


def test_interviewer_mixed() -> None:
    assert _convert("mixed", "interviewer") == 0.0


def test_interviewer_unknown() -> None:
    assert _convert("unknown", "interviewer") == 0.0


# ============================================================
# direct: そのまま float に変換
# ============================================================


def test_direct_int_input() -> None:
    assert _convert(12, "direct") == pytest.approx(12.0)


def test_direct_float_input() -> None:
    assert _convert(3.14, "direct") == pytest.approx(3.14)


# ============================================================
# None 入力 → None を返す（DB 書き込みをスキップするシグナル）
# ============================================================


def test_none_returns_none_for_all_types() -> None:
    types = [
        "scale5_direct",
        "pct_to_scale5",
        "pct_to_pct100",
        "bool_to_num",
        "bool_to_scale5",
        "interviewer",
        "direct",
    ]
    for vtype in types:
        result = _convert(None, vtype)
        assert result is None, f"_convert(None, '{vtype}') は None を返すべき。got {result}"


# ============================================================
# 不正な入力（文字列を数値として解釈できない）→ None を返す
# ============================================================


def test_invalid_string_returns_none() -> None:
    assert _convert("not_a_number", "scale5_direct") is None
    assert _convert("abc", "pct_to_pct100") is None


# ============================================================
# 重要な不変条件: Haiku が「証拠なし」のデフォルト値 2.5 を返した場合
# そのまま変換される（V5 書き込みは _upsert_feature の保護ロジックで制御）
# プロンプト修正後はここに 2.5 が来ないことが期待される
# ============================================================


def test_25_default_passthrough_before_prompt_fix() -> None:
    """2.5 は証拠なしデフォルト値。プロンプト修正前はこの値が来得た。
    修正後は null → None となり write_from_star でスキップされるべき。
    この変換自体は問題なく動く（DB 保護で既存 official/review を上書きしない）。
    """
    result = _convert(2.5, "scale5_direct")
    assert result == pytest.approx(2.5)
