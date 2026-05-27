"""ユーザープロフィール管理。

my_profile.json（後方互換）と UserProfile DB（V2）の両方を扱う。
Sonnet 4.6 でユーザーの実務力スコア（tech_level_score）を算出し、
条件設定から 10次元重みベクトル（dimension_weights）を生成する。
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

import anthropic
import sqlalchemy as sa

from backend.config import settings
from backend.database import get_session
from backend.models import UserProfile

logger = logging.getLogger(__name__)

PROFILE_JSON_PATH = Path(__file__).parent.parent.parent / "data" / "my_profile.json"

_LEVEL_ASSESSMENT_PROMPT = """\
以下はエンジニア志望者のスキル・経験情報です。
実務力スコア（0.0〜1.0）を評価してください。

【入力情報】
スキル: {skills}
資格: {qualifications}
個人開発・インターン経験:
{project_experience}

【評価基準】
0.0-0.2: プログラミング初学者（Hello World程度）
0.2-0.4: 基礎的な実装経験あり（簡単なWebアプリ等）
0.4-0.6: 中程度（フルスタック開発経験、インターン経験等）
0.6-0.8: 実務レベルに近い（マイクロサービス・ML実装・長期インターン等）
0.8-1.0: 高い実務力（OSS貢献・複雑なシステム設計・即戦力級）

JSONのみ出力:
{{"tech_level_score": 0.0-1.0, "rationale": "評価理由を100字以内で"}}"""


def _assess_tech_level(
    skills: list[str], qualifications: list[str], project_experience: str
) -> tuple[float, str]:
    """Sonnet 4.6 でユーザーの実務力スコアを算出する。失敗時は (0.5, "") を返す。"""
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        prompt = _LEVEL_ASSESSMENT_PROMPT.format(
            skills=", ".join(skills) or "未入力",
            qualifications=", ".join(qualifications) or "なし",
            project_experience=project_experience or "未入力",
        )
        response = client.messages.create(
            model=settings.sonnet_model,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        result = json.loads(raw)
        score = float(result.get("tech_level_score", 0.5))
        rationale = str(result.get("rationale", ""))
        time.sleep(settings.sonnet_sleep_seconds)
        return round(max(0.0, min(1.0, score)), 3), rationale
    except Exception as e:
        logger.warning("tech_level_score算出失敗: %s", e)
        return 0.5, ""


def _compute_dimension_weights(
    hard_constraints: dict[str, Any],
    soft_preferences: dict[str, Any],
    tech_level_score: float,
) -> list[float]:
    """ユーザーの条件・優先度から10次元重みベクトルを生成する。合計1.0に正規化済み。"""
    weights = [0.1] * 10  # 均等スタート

    # WLB: 残業上限が厳しいほど dim[7] 重視
    max_ot = hard_constraints.get("max_overtime_hours", 40)
    weights[7] += (1.0 - min(max_ot, 60) / 60.0) * 0.25

    # 開発環境: 技術力が高いほど dim[9] 重視
    weights[9] += tech_level_score * 0.25

    # キャリア成長: soft_preferences で career_growth を重視するなら dim[6] 加重
    if soft_preferences.get("priority") == "career_growth":
        weights[6] += 0.15
    elif soft_preferences.get("priority") == "wlb":
        weights[7] += 0.15

    # 立地: preferred_prefectures が設定されているなら dim[0] を少し重視
    if hard_constraints.get("preferred_prefectures"):
        weights[0] += 0.05

    # L1 正規化
    total = sum(weights)
    return [round(w / total, 4) for w in weights]


def _import_from_json() -> dict[str, Any]:
    """my_profile.json を読み込む。ファイルがなければ空dictを返す。"""
    if not PROFILE_JSON_PATH.exists():
        return {}
    try:
        with PROFILE_JSON_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("my_profile.json 読み込み失敗: %s", e)
        return {}


def load_or_create_profile(session_id: str) -> UserProfile:
    """DBからセッションIDでプロフィールを取得する。なければmy_profile.jsonから初期インポートする。"""
    with get_session() as session:
        existing = session.execute(
            sa.select(UserProfile).where(UserProfile.session_id == session_id)
        ).scalar_one_or_none()
        if existing:
            return existing

    # DB未登録 → my_profile.json からインポート
    json_data = _import_from_json()
    hard = json_data.get("hard_filters", {})
    soft = {}

    skills = json_data.get("required_skills", []) + json_data.get("bonus_skills", [])
    quals = json_data.get("qualifications", [])
    tech_score, rationale = (0.5, "") if not skills else _assess_tech_level(skills, quals, "")
    weights = _compute_dimension_weights(hard, soft, tech_score)

    with get_session() as session:
        profile = UserProfile(
            session_id=session_id,
            tech_skills=skills,
            qualifications=quals,
            project_experience=None,
            architecture_experience=None,
            tech_level_score=tech_score,
            tech_level_rationale=rationale,
            hard_constraints=hard,
            soft_preferences=soft,
            dimension_weights=weights,
        )
        session.add(profile)
        session.flush()
        session.expunge(profile)
        return profile


def save_profile(
    session_id: str,
    tech_skills: list[str],
    qualifications: list[str],
    project_experience: str,
    architecture_experience: list[str],
    hard_constraints: dict[str, Any],
    soft_preferences: dict[str, Any],
    recompute_level: bool = True,
) -> UserProfile:
    """プロフィールをDBにupsertする。recompute_level=True なら Sonnet で tech_level_score を再算出。"""
    if recompute_level:
        tech_score, rationale = _assess_tech_level(tech_skills, qualifications, project_experience)
    else:
        tech_score, rationale = 0.5, ""

    weights = _compute_dimension_weights(hard_constraints, soft_preferences, tech_score)

    with get_session() as session:
        existing = session.execute(
            sa.select(UserProfile).where(UserProfile.session_id == session_id)
        ).scalar_one_or_none()

        if existing:
            existing.tech_skills = tech_skills
            existing.qualifications = qualifications
            existing.project_experience = project_experience
            existing.architecture_experience = architecture_experience
            existing.tech_level_score = tech_score
            existing.tech_level_rationale = rationale
            existing.hard_constraints = hard_constraints
            existing.soft_preferences = soft_preferences
            existing.dimension_weights = weights
            session.flush()
            session.expunge(existing)
            return existing
        else:
            profile = UserProfile(
                session_id=session_id,
                tech_skills=tech_skills,
                qualifications=qualifications,
                project_experience=project_experience,
                architecture_experience=architecture_experience,
                tech_level_score=tech_score,
                tech_level_rationale=rationale,
                hard_constraints=hard_constraints,
                soft_preferences=soft_preferences,
                dimension_weights=weights,
            )
            session.add(profile)
            session.flush()
            session.expunge(profile)
            return profile
