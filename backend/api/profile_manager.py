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
from sqlalchemy.exc import IntegrityError

from backend.config import settings
from backend.database import get_session
from backend.models import UserProfile

logger = logging.getLogger(__name__)

PROFILE_JSON_PATH = Path(__file__).parent.parent.parent / "data" / "my_profile.json"

_LEVEL_ASSESSMENT_PROMPT = """\
以下は新卒就活中の学生のスキル・経験情報です。
6因子ルーブリック（Dreyfus習得モデル / IPA ITスキル標準 / 国内IT企業の新卒採用評価表を参考）で
各因子を0〜4点で採点してください。最終スコアはPython側で機械計算します。

▼ 学生基準での採点です。社会人経験との比較ではなく「同世代の学生の中での相対評価」として
  0点＝未経験者、2点＝平均的な技術系学生、4点＝学生トップクラス を目安にしてください。

【入力情報】
スキル: {skills}
資格: {qualifications}
補足・自由記述:
{project_experience}{projects_section}{github_section}

【6因子ルーブリック（各 0〜4 点）】

F1 実装量・経験規模
  0: 実装経験なし / チュートリアルのコピーのみ
  1: 個人プロジェクト1〜2件（趣味・授業課題レベル）
  2: 個人プロジェクト3件以上 または 研究室・ゼミでのチーム実装
  3: 短期インターン（1〜2ヶ月）または 本番環境へのリリース経験（個人）
  4: 長期インターン（3ヶ月以上・チーム開発）/ OSSへのマージ済みPR ←学生では上位5%

F2 実装品質
  0: 品質評価不可（実装なし）
  1: 動作はするが保守性低い（テスト・設計なし）←学生の多数派
  2: 基本的な設計パターン（MVC等）を適用 / README整備
  3: ユニットテスト・ドキュメント・CI/CD のいずれかあり ←学生では上位30%
  4: テスト・CI/CD・コードレビュー・設計パターンが揃っている ←学生では上位5%

F3 技術スタック幅
  0: 1言語・1ライブラリのみ（初学者レベル）
  1: フロントエンドまたはバックエンドの一方を実用レベルで扱える
  2: フロントエンド + バックエンド（フルスタック）を実装した経験あり
  3: フルスタック + インフラ（Docker/クラウド/DB設計）のいずれかを実務で使用
  4: フルスタック + AI/ML + インフラ の3領域 または マイクロサービス設計の実装経験

F4 技術深度
  0: 入門書・チュートリアルレベル（コピペ中心）
  1: ドキュメントを参照しながら自力で実装できる（平均的な学生）
  2: エラーや非自明な問題を自力でデバッグ・解決できる
  3: 技術選定の根拠を説明・設計できる（IPA応用情報技術者相当の体系知識）
  4: 複雑なアーキテクチャを自ら設計・実装 / AtCoder水色以上 / AI論文実装経験

F5 資格・競技プログラミング（取得済みのもののみ。在学中合格のみカウント）
  0: なし
  1: IPA基本情報技術者 / G検定 / Paiza Cランク相当（←学生の30〜40%が取得）
  2: IPA応用情報技術者 / AWS Associate / AtCoder茶色以上 / Paiza Bランク（←上位20%）
  3: E資格（JDLA） / AWS Professional / AtCoder緑色以上 / Paiza Aランク（←上位5%）
  4: IPA高度試験（システムアーキテクト等）/ AtCoder水色以上（←学生では極めて稀）

F6 外部発信・コミュニティ貢献
  （学習の深さと発信力を測る。GitHubのstars/フォロワー数ではなく内容・影響力で判断）
  0: 外部発信実績なし
  1: 技術ブログ数本 or Qiita/Zenn投稿1〜5本 / GitHubに公開リポジトリあり
  2: Qiita/Zenn 10本以上 or 勉強会・LT登壇1回以上 or ハッカソン参加経験
  3: 記事に一定の反響（Qiita100LGTM等）or ハッカソン入賞 or Kaggle/SIGNATE参加実績
  4: 著名記事（1000+LGTM / はてブ多数）or Kaggle上位（Expert以上）or 学会発表

【採点の原則（重要）】
- 入力に記載のない実績は0点。推測による加点は禁止（証拠主義）
- 資格のみで実装実績が確認できない場合、F1・F2は低く評価する
- 各因子は独立して評価。他因子の高さで別因子を加点しない
- 全ての4点は「学生の中でトップクラス（上位5%）」基準。辛めに評価する

【スコア計算式（参考値として出力、実際はPythonで機械計算）】
tech_level_score = (F1 + F2 + F3 + F4 + F5 + F6) / 24

JSONのみ出力（f1〜f6は整数、rationaleはX部分に実際の点数を入れる）:
{{"f1": 0-4, "f2": 0-4, "f3": 0-4, "f4": 0-4, "f5": 0-4, "f6": 0-4, "tech_level_score": 0.0-1.0, "rationale": "F1:X F2:X F3:X F4:X F5:X F6:X=XX/24 根拠40字以内"}}"""


def _format_projects_section(projects: list[dict] | None) -> str:
    """製作物リストをプロンプト用テキストに整形する。"""
    if not projects:
        return ""
    lines = ["\n製作物一覧:"]
    for i, p in enumerate(projects, 1):
        name = p.get("name", f"製作物{i}")
        ptype = p.get("type", "")
        desc = p.get("description", "")
        stack = ", ".join(p.get("tech_stack") or [])
        is_ai = "あり" if p.get("is_ai") else "なし"
        team = p.get("team_size", "")
        duration = p.get("duration", "")
        lines.append(
            f"  [{i}] {name}（{ptype}）"
            f" / 技術: {stack or '未記入'}"
            f" / AI開発: {is_ai}"
            f" / チーム: {team or '未記入'}"
            f" / 期間: {duration or '未記入'}"
            f"\n      概要: {desc or '未記入'}"
        )
    return "\n".join(lines)


def _assess_tech_level(
    skills: list[str],
    qualifications: list[str],
    project_experience: str,
    github_summary: str | None = None,
    projects: list[dict] | None = None,
) -> tuple[float, str]:
    """Sonnet 4.6 でユーザーの実務力スコアを算出する。失敗時は (0.5, "") を返す。"""
    try:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        github_section = f"\nGitHub活動:\n{github_summary}" if github_summary else ""
        projects_section = _format_projects_section(projects)
        prompt = _LEVEL_ASSESSMENT_PROMPT.format(
            skills=", ".join(skills) or "未入力",
            qualifications=", ".join(qualifications) or "なし",
            project_experience=project_experience or "未入力",
            projects_section=projects_section,
            github_section=github_section,
        )
        response = client.messages.create(
            model=settings.sonnet_model,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            lines = raw.splitlines()
            raw = "\n".join(lines[1:-1]) if len(lines) > 2 else raw
        result = json.loads(raw)
        # 6因子が揃っていればPythonで機械計算（LLMのホリスティック判断を排除）
        if all(f"f{i}" in result for i in range(1, 7)):
            factor_sum = sum(max(0, min(4, int(result[f"f{i}"]))) for i in range(1, 7))
            score = round(factor_sum / 24, 3)
        else:
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
    target_industries: list[str] | None = None,
    target_roles: list[str] | None = None,
    dev_phase_preference: str | None = None,
    eval_preference: str | None = None,
    psych_safety_importance: float | None = None,
) -> list[float]:
    """ユーザーの条件・優先度から10次元重みベクトルを生成する。合計1.0に正規化済み。

    dim[0] 立地, dim[1] ビジョン, dim[2] BM, dim[3] 財務, dim[4] トレンド,
    dim[5] カルチャー, dim[6] キャリア, dim[7] WLB, dim[8] 採用透明度, dim[9] 開発環境
    """
    weights = [0.1] * 10  # 均等スタート

    # WLB: 残業上限が厳しいほど dim[7] 重視
    max_ot = hard_constraints.get("max_overtime_hours", 40)
    weights[7] += (1.0 - min(max_ot, 60) / 60.0) * 0.25

    # 開発環境: 技術力が高いほど dim[9] 重視
    weights[9] += tech_level_score * 0.25

    # 優先事項（4択）
    priority = soft_preferences.get("priority", "balanced")
    if priority == "career_growth":
        weights[6] += 0.15
    elif priority == "wlb":
        weights[7] += 0.15
    elif priority == "salary":
        weights[7] += 0.05  # WLBと相関が高いため少し加重

    # 立地: preferred_prefectures が設定されているなら dim[0] を少し重視
    if hard_constraints.get("preferred_prefectures"):
        weights[0] += 0.05

    # 志望業界・職種による調整
    industries = [i.lower() for i in (target_industries or [])]
    roles = [r.lower() for r in (target_roles or [])]
    if any(k in " ".join(industries + roles) for k in ["ai", "データ", "ml", "機械学習"]):
        weights[9] += 0.15  # 開発環境次元を強化
    if any(k in " ".join(industries) for k in ["スタートアップ", "ベンチャー"]):
        weights[1] += 0.10  # ビジョン次元を強化
    if any(k in " ".join(roles) for k in ["インフラ", "sre", "devops"]):
        weights[9] += 0.08

    # 開発フェーズの好み
    if dev_phase_preference == "R&D":
        weights[1] += 0.10  # ビジョン次元を強化
        weights[4] += 0.05  # 業界トレンド
    elif dev_phase_preference == "新規開発":
        weights[1] += 0.05
        weights[9] += 0.05
    elif dev_phase_preference == "運用保守":
        weights[7] += 0.05  # 安定志向 → WLB重視

    # 評価制度の好み
    if eval_preference == "成果主義":
        weights[6] += 0.10  # キャリア成長次元を強化
    elif eval_preference == "プロセス重視":
        weights[5] += 0.05  # カルチャー次元を強化

    # 心理的安全性の重視度（0.0〜1.0）
    if psych_safety_importance is not None:
        weights[5] += psych_safety_importance * 0.15

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
            session.expunge(existing)
            return existing

    # DB未登録 → my_profile.json からインポート
    json_data = _import_from_json()
    hard = json_data.get("hard_filters", {})
    soft = {}

    skills = json_data.get("required_skills", []) + json_data.get("bonus_skills", [])
    quals = json_data.get("qualifications", [])
    tech_score, rationale = (0.5, "") if not skills else _assess_tech_level(skills, quals, "")
    weights = _compute_dimension_weights(hard, soft, tech_score)

    try:
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
    except IntegrityError:
        # Streamlit の再実行で別リクエストが先に INSERT した場合は再取得
        with get_session() as session:
            existing = session.execute(
                sa.select(UserProfile).where(UserProfile.session_id == session_id)
            ).scalar_one()
            session.expunge(existing)
            return existing


def save_profile(
    session_id: str,
    tech_skills: list[str],
    qualifications: list[str],
    project_experience: str,
    architecture_experience: list[str],
    hard_constraints: dict[str, Any],
    soft_preferences: dict[str, Any],
    graduation_year: int | None = None,
    major: str | None = None,
    target_industries: list[str] | None = None,
    target_roles: list[str] | None = None,
    dev_phase_preference: str | None = None,
    min_salary: int | None = None,
    mbti: str | None = None,
    eval_preference: str | None = None,
    psych_safety_importance: float | None = None,
    github_summary: str | None = None,
    projects: list[dict] | None = None,
    recompute_level: bool = True,
) -> UserProfile:
    """プロフィールをDBにupsertする。recompute_level=True なら Sonnet で tech_level_score を再算出。"""
    if recompute_level:
        tech_score, rationale = _assess_tech_level(
            tech_skills,
            qualifications,
            project_experience,
            github_summary=github_summary,
            projects=projects,
        )
    else:
        tech_score, rationale = 0.5, ""

    weights = _compute_dimension_weights(
        hard_constraints,
        soft_preferences,
        tech_score,
        target_industries=target_industries,
        target_roles=target_roles,
        dev_phase_preference=dev_phase_preference,
        eval_preference=eval_preference,
        psych_safety_importance=psych_safety_importance,
    )

    new_fields = {
        "tech_skills": tech_skills,
        "qualifications": qualifications,
        "project_experience": project_experience,
        "architecture_experience": architecture_experience,
        "tech_level_score": tech_score,
        "tech_level_rationale": rationale,
        "hard_constraints": hard_constraints,
        "soft_preferences": soft_preferences,
        "dimension_weights": weights,
        "graduation_year": graduation_year,
        "major": major,
        "target_industries": target_industries,
        "target_roles": target_roles,
        "dev_phase_preference": dev_phase_preference,
        "min_salary": min_salary,
        "mbti": mbti,
        "eval_preference": eval_preference,
        "psych_safety_importance": psych_safety_importance,
        "projects": projects,
    }

    with get_session() as session:
        existing = session.execute(
            sa.select(UserProfile).where(UserProfile.session_id == session_id)
        ).scalar_one_or_none()

        if existing:
            for k, v in new_fields.items():
                setattr(existing, k, v)
            session.flush()
            session.expunge(existing)
            return existing
        else:
            profile = UserProfile(session_id=session_id, **new_fields)
            session.add(profile)
            session.flush()
            session.expunge(profile)
            return profile


def update_dimension_weights(session_id: str, weights: list[float]) -> None:
    """dimension_weightsのみをDBに直接上書きする。意図翻訳エンジン用。"""
    with get_session() as session:
        existing = session.execute(
            sa.select(UserProfile).where(UserProfile.session_id == session_id)
        ).scalar_one_or_none()
        if existing:
            existing.dimension_weights = weights


def update_hard_constraints(session_id: str, hard_constraints: dict[str, Any]) -> None:
    """hard_constraintsのみをDBに直接上書きする。サイドバー用。"""
    with get_session() as session:
        existing = session.execute(
            sa.select(UserProfile).where(UserProfile.session_id == session_id)
        ).scalar_one_or_none()
        if existing:
            existing.hard_constraints = hard_constraints
