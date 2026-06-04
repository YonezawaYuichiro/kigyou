"""V5 マッチングエンジン（feature_key EAV ベースの加重平均スコアリング）

V2 の pgvector エンジン (matching_engine.py) と並存。
V5 は company_feature / user_preference / feature_definition の3テーブルを使う。

アルゴリズム:
  1. ハードフィルタ: is_hard_filter=True の条件を raw 値でチェック
  2. 項目別サブスコア (0-1):
       desired_value あり → max(0, 1 - |company_norm - desired_norm|)
       desired_value なし → company_norm をそのまま（direction 考慮済み）
       bool            → 一致=1.0 / 不一致=0.0
       tag / onehot    → Jaccard（Phase 5 以降）
  3. 加重平均: Score = Σ(w × subscore) / Σ(w)
       w = user_preference.weight ?? feature_definition.default_weight
       欠損項目は分子・分母ともに除外
  4. 寄与 TOP3 / BOTTOM3 を説明として返す

CLI:
  python -m backend.api.matching_engine_v5 \\
      --user-id  <UserProfile UUID> \\
      --company-id <Company UUID>
"""

from __future__ import annotations

import argparse
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlalchemy as sa

from backend.database import get_session

log = logging.getLogger(__name__)


# ============================================================
# データクラス
# ============================================================


@dataclass
class FeatureScore:
    feature_key: str
    display_name: str
    weight: float
    subscore: float
    company_norm: float | None
    desired_str: str
    contribution: float = field(init=False)

    def __post_init__(self) -> None:
        self.contribution = round(self.weight * self.subscore, 4)


@dataclass
class MatchScore:
    user_profile_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    total_score: float
    hard_filter_passed: bool
    hard_filter_failures: list[str]
    feature_scores: list[FeatureScore]
    missing_features: list[str]
    completeness_ratio: float

    @property
    def top3(self) -> list[FeatureScore]:
        return sorted(self.feature_scores, key=lambda x: x.contribution, reverse=True)[:3]

    @property
    def bottom3(self) -> list[FeatureScore]:
        return sorted(self.feature_scores, key=lambda x: x.contribution)[:3]


# ============================================================
# メイン関数
# ============================================================


def compute_match(
    user_profile_id: uuid.UUID,
    company_id: uuid.UUID,
    session: Any,
) -> MatchScore:
    """1ユーザー × 1企業のマッチングスコアを計算して返す。"""
    company_name = _get_company_name(session, company_id)
    prefs = _fetch_preferences(session, user_profile_id)
    feat_map = {r.feature_key: r for r in _fetch_company_features(session, company_id)}
    fdef_map = {r.feature_key: r for r in _fetch_feature_defs(session)}
    completeness = _fetch_completeness(session, company_id)

    hard_failures: list[str] = []
    scored: list[FeatureScore] = []
    missing: list[str] = []

    for pref in prefs:
        fkey = pref.feature_key
        fdef = fdef_map.get(fkey)
        feat = feat_map.get(fkey)

        if fdef is None:
            continue

        # tag / onehot は Phase 5 以降
        if fdef.method in ("tag", "onehot"):
            continue

        # ハードフィルタ判定（raw 値で比較）
        if pref.is_hard_filter:
            passed, reason = _check_hard_filter(pref, feat, fdef)
            if not passed:
                hard_failures.append(reason)

        # サブスコア計算
        subscore = _compute_subscore(pref, feat, fdef)
        if subscore is None:
            missing.append(fkey)
            continue

        weight = float(pref.weight) if pref.weight is not None else float(fdef.default_weight)
        scored.append(
            FeatureScore(
                feature_key=fkey,
                display_name=str(fdef.display_name),
                weight=weight,
                subscore=subscore,
                company_norm=float(feat.value_normalized)
                if feat and feat.value_normalized is not None
                else None,
                desired_str=_desired_str(pref),
            )
        )

    # 加重平均
    total_w = sum(s.weight for s in scored)
    total_score = sum(s.contribution for s in scored) / total_w if total_w > 0 else 0.0

    return MatchScore(
        user_profile_id=user_profile_id,
        company_id=company_id,
        company_name=company_name,
        total_score=round(total_score, 4),
        hard_filter_passed=len(hard_failures) == 0,
        hard_filter_failures=hard_failures,
        feature_scores=sorted(scored, key=lambda x: x.contribution, reverse=True),
        missing_features=missing,
        completeness_ratio=float(completeness) if completeness is not None else 0.0,
    )


# ============================================================
# DB アクセス
# ============================================================


def _get_company_name(session: Any, company_id: uuid.UUID) -> str:
    row = session.execute(
        sa.text("SELECT name FROM company WHERE id = :id"),
        {"id": str(company_id)},
    ).fetchone()
    return row[0] if row else str(company_id)


def _fetch_preferences(session: Any, user_profile_id: uuid.UUID):
    return session.execute(
        sa.text(
            "SELECT feature_key, desired_value, desired_min, desired_max,"
            "       desired_tags, weight, is_hard_filter"
            " FROM user_preference WHERE user_profile_id = :uid"
        ),
        {"uid": str(user_profile_id)},
    ).fetchall()


def _fetch_company_features(session: Any, company_id: uuid.UUID):
    return session.execute(
        sa.text(
            "SELECT feature_key, value_numeric, value_official, value_actual, value_normalized"
            " FROM company_feature"
            " WHERE company_id = :cid AND role_id IS NULL"
        ),
        {"cid": str(company_id)},
    ).fetchall()


def _fetch_feature_defs(session: Any):
    return session.execute(
        sa.text(
            "SELECT feature_key, display_name, method, value_min, value_max,"
            "       direction, default_weight, has_official_actual"
            " FROM feature_definition WHERE is_active"
        )
    ).fetchall()


def _fetch_completeness(session: Any, company_id: uuid.UUID) -> float | None:
    row = session.execute(
        sa.text("SELECT completeness_ratio FROM company_completeness WHERE company_id = :cid"),
        {"cid": str(company_id)},
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


# ============================================================
# スコア計算ロジック
# ============================================================


def _get_effective_raw(feat: Any, fdef: Any) -> float | None:
    """制度/実態2スロット対応。value_actual 優先、なければ value_official / value_numeric。"""
    if fdef.has_official_actual:
        if feat.value_actual is not None:
            return float(feat.value_actual)
        if feat.value_official is not None:
            return float(feat.value_official)
        return None
    return float(feat.value_numeric) if feat.value_numeric is not None else None


def _check_hard_filter(pref: Any, feat: Any, fdef: Any) -> tuple[bool, str]:
    """ハードフィルタ判定。データなしは通過とみなす（欠損ペナルティは completeness_ratio で可視化）。"""
    if feat is None:
        return True, ""

    raw = _get_effective_raw(feat, fdef)
    if raw is None:
        return True, ""

    if pref.desired_max is not None and raw > float(pref.desired_max):
        return False, f"{fdef.display_name}: 実態 {raw:.1f} > 上限 {float(pref.desired_max):.1f}"
    if pref.desired_min is not None and raw < float(pref.desired_min):
        return False, f"{fdef.display_name}: 実態 {raw:.1f} < 下限 {float(pref.desired_min):.1f}"
    if (
        pref.desired_value is not None
        and fdef.method == "bool"
        and raw != float(pref.desired_value)
    ):
        return False, f"{fdef.display_name}: {raw} ≠ 希望 {pref.desired_value}"

    return True, ""


def _desired_normalized(desired_value: float, fdef: Any) -> float | None:
    """desired_value を company_norm と同じ [0,1] スケールに変換する。"""
    if fdef.value_min is None or fdef.value_max is None:
        return None
    vmin, vmax = float(fdef.value_min), float(fdef.value_max)
    if vmax == vmin:
        return 0.5
    dn = (desired_value - vmin) / (vmax - vmin)
    dn = max(0.0, min(1.0, dn))
    if fdef.direction == "low_good":
        dn = 1.0 - dn
    return dn


def _compute_subscore(pref: Any, feat: Any, fdef: Any) -> float | None:
    """サブスコア 0-1。企業データ欠損は None を返し加重平均から除外する。"""
    if feat is None or feat.value_normalized is None:
        return None

    vn = float(feat.value_normalized)

    # bool: 一致/不一致
    if fdef.method == "bool":
        if pref.desired_value is not None:
            raw = _get_effective_raw(feat, fdef)
            return 1.0 if (raw is not None and raw == float(pref.desired_value)) else 0.0
        return vn

    # desired_value 指定あり: 正規化した希望値との距離ペナルティ
    if pref.desired_value is not None:
        dn = _desired_normalized(float(pref.desired_value), fdef)
        if dn is None:
            return vn
        return max(0.0, 1.0 - abs(vn - dn))

    # desired_min / desired_max のみ: value_normalized をそのまま使用
    # direction 考慮済み（low_good 軸は残業少ない=高スコア）
    return vn


def _desired_str(pref: Any) -> str:
    prefix = "【必須】" if pref.is_hard_filter else ""
    if pref.desired_value is not None:
        return f"{prefix}={pref.desired_value}"
    parts = []
    if pref.desired_min is not None:
        parts.append(f">={pref.desired_min}")
    if pref.desired_max is not None:
        parts.append(f"<={pref.desired_max}")
    return prefix + " ".join(parts) if parts else f"{prefix}(方向一致)"


# ============================================================
# 結果表示
# ============================================================


def print_result(result: MatchScore) -> None:
    sep = "=" * 64
    hf = "[OK] 通過" if result.hard_filter_passed else "[NG] 不合格"
    print(f"\n{sep}")
    print(f"  企業: {result.company_name}")
    print(f"  総合スコア : {result.total_score:.3f}  ({result.total_score * 100:.1f} / 100点)")
    print(f"  ハードフィルタ: {hf}")
    for f in result.hard_filter_failures:
        print(f"    -> {f}")
    print(
        f"  データ網羅率: {result.completeness_ratio * 100:.0f}%"
        f"  / 欠損項目: {len(result.missing_features)} 件"
    )
    print(sep)

    print("\n【寄与 TOP 3 -- なぜこの企業が高いか】")
    for i, s in enumerate(result.top3, 1):
        print(
            f"  {i}. {s.display_name[:32]:<32}"
            f"  score={s.subscore:.2f}  w={s.weight:.1f}"
            f"  contrib={s.contribution:.3f}  希望{s.desired_str}"
        )

    print("\n【改善余地 BOTTOM 3 -- なぜ下がるか】")
    for i, s in enumerate(result.bottom3, 1):
        print(
            f"  {i}. {s.display_name[:32]:<32}"
            f"  score={s.subscore:.2f}  w={s.weight:.1f}"
            f"  contrib={s.contribution:.3f}  希望{s.desired_str}"
        )

    print(f"\n【全 {len(result.feature_scores)} 項目】")
    for s in result.feature_scores:
        bar = "#" * int(s.subscore * 10)
        print(f"  {bar:<10}  {s.feature_key:<36}  {s.subscore:.2f}  希望{s.desired_str}")


# ============================================================
# CLI エントリポイント
# ============================================================


def _run_cli() -> None:
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="V5 マッチングスコア計算")
    parser.add_argument("--user-id", type=uuid.UUID, required=True)
    parser.add_argument("--company-id", type=uuid.UUID, required=True)
    args = parser.parse_args()

    with get_session() as session:
        result = compute_match(args.user_id, args.company_id, session)

    print_result(result)


if __name__ == "__main__":
    _run_cli()
