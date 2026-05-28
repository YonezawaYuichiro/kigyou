"""GitHub解析モジュール（V3 Phase 5）。

公開GitHubリポジトリをGitHub API（認証不要）で取得し、
ユーザーの技術力・活動量を定量化して tech_level_score の精度を上げる。

レート制限: 非認証で 60 req/hour。本モジュールは最大 2 リクエストを使用する。

返却形式:
  {
    "username": str,
    "repo_count": int,
    "tech_stack": list[str],   # 使用言語上位5つ
    "total_stars": int,
    "commit_frequency": int,   # 直近90日のpush回数（eventsから推定）
    "project_complexity_score": float,  # 0.0-1.0
    "oss_contribution": bool,  # 他者リポジトリへのcontribute有無
    "summary": str,            # Sonnetプロンプト用テキストサマリー
    "error": str | None
  }
"""

import logging
import re
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_TIMEOUT = 10.0
_COMPLEX_TOPICS = {
    "machine-learning",
    "deep-learning",
    "nlp",
    "reinforcement-learning",
    "computer-vision",
    "pytorch",
    "tensorflow",
    "llm",
    "generative-ai",
    "kubernetes",
    "microservices",
    "distributed-systems",
    "compiler",
    "operating-system",
    "embedded",
    "robotics",
    "blockchain",
}
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def _parse_username(github_url: str) -> str | None:
    """GitHub URL またはユーザー名文字列からユーザー名を抽出する。"""
    s = github_url.strip().rstrip("/")
    # URL形式: https://github.com/username or github.com/username
    m = re.search(r"github\.com/([^/\s]+)/?$", s)
    if m:
        return m.group(1)
    # ユーザー名のみ（英数字とハイフン）
    if re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{0,38}$", s):
        return s
    return None


def _score_complexity(repos: list[dict]) -> float:
    """リポジトリリストから技術的複雑さスコア（0.0-1.0）を算出する。"""
    if not repos:
        return 0.1

    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    languages = {r.get("language") for r in repos if r.get("language")}
    max_size_kb = max((r.get("size", 0) for r in repos), default=0)
    all_topics = {t for r in repos for t in (r.get("topics") or [])}
    has_complex_topic = bool(all_topics & _COMPLEX_TOPICS)

    score = 0.0
    # スター数: 対数スケールで最大 0.3
    if total_stars > 0:
        import math

        score += min(0.3, math.log10(total_stars + 1) / 3.0)
    # 言語多様性: 3言語以上で +0.2
    score += min(0.2, len(languages) * 0.07)
    # リポジトリ数: 10以上で +0.15
    score += min(0.15, len(repos) * 0.015)
    # 複雑トピック存在: +0.2
    if has_complex_topic:
        score += 0.2
    # 大規模リポジトリ（1MB超）: +0.15
    if max_size_kb > 1000:
        score += min(0.15, max_size_kb / 50000)

    return round(min(1.0, score), 3)


def _recent_push_count(events: list[dict], days: int = 90) -> int:
    """eventsリストから直近 N 日間の PushEvent 数を返す。"""
    cutoff = datetime.now(UTC) - timedelta(days=days)
    count = 0
    for ev in events:
        if ev.get("type") != "PushEvent":
            continue
        created = ev.get("created_at", "")
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if dt >= cutoff:
                count += 1
        except ValueError:
            pass
    return count


def analyze_github(github_url: str) -> dict:
    """GitHub APIを使ってユーザーのリポジトリ・活動量を分析する。

    Args:
        github_url: GitHubプロフィールURL または ユーザー名

    Returns:
        分析結果dict。エラー時は error キーに説明を入れて返す（例外は raise しない）
    """
    username = _parse_username(github_url)
    if not username:
        return {"error": f"GitHubユーザー名を解析できませんでした: {github_url}"}

    try:
        with httpx.Client(headers=_HEADERS, timeout=_TIMEOUT) as client:
            # リポジトリ一覧（pushで降順、オーナーのもの最大30件）
            repos_resp = client.get(
                f"{_GITHUB_API}/users/{username}/repos",
                params={"sort": "pushed", "per_page": 30, "type": "owner"},
            )
            if repos_resp.status_code == 404:
                return {"error": f"GitHubユーザーが見つかりません: {username}"}
            if repos_resp.status_code == 403:
                return {
                    "error": "GitHub APIレート制限に達しました。しばらく待ってから試してください。"
                }
            repos_resp.raise_for_status()
            repos: list[dict] = repos_resp.json()

            # 最近のイベント（最大100件）
            events_resp = client.get(
                f"{_GITHUB_API}/users/{username}/events/public",
                params={"per_page": 100},
            )
            events: list[dict] = events_resp.json() if events_resp.status_code == 200 else []

    except httpx.TimeoutException:
        return {"error": "GitHub APIがタイムアウトしました。"}
    except Exception as e:
        logger.warning("GitHub API呼び出し失敗: %s", e)
        return {"error": f"GitHub API呼び出し失敗: {e}"}

    # 言語集計
    lang_counter: Counter = Counter()
    for r in repos:
        if r.get("language"):
            lang_counter[r["language"]] += 1
    top_languages = [lang for lang, _ in lang_counter.most_common(5)]

    total_stars = sum(r.get("stargazers_count", 0) for r in repos)
    complexity = _score_complexity(repos)
    commit_freq = _recent_push_count(events, days=90)

    # OSS貢献: 自分以外のリポジトリへのPushEventがあるか
    oss = any(
        ev.get("type") == "PushEvent"
        and ev.get("repo", {}).get("name", "").split("/")[0].lower() != username.lower()
        for ev in events
    )

    # Sonnetプロンプト用サマリーテキスト
    top_repos = sorted(repos, key=lambda r: r.get("stargazers_count", 0), reverse=True)[:3]
    repo_summaries = "; ".join(
        f"{r['name']}（{r.get('language', '?')}・⭐{r.get('stargazers_count', 0)}）"
        + (f": {r['description'][:50]}" if r.get("description") else "")
        for r in top_repos
    )
    summary = (
        f"GitHub: @{username} / リポジトリ {len(repos)}件 / "
        f"使用言語: {', '.join(top_languages) or '不明'} / "
        f"総スター: {total_stars} / "
        f"直近90日push: {commit_freq}回 / "
        f"OSSコントリビュート: {'あり' if oss else 'なし'} / "
        f"代表リポジトリ: {repo_summaries or 'なし'}"
    )

    return {
        "username": username,
        "repo_count": len(repos),
        "tech_stack": top_languages,
        "total_stars": total_stars,
        "commit_frequency": commit_freq,
        "project_complexity_score": complexity,
        "oss_contribution": oss,
        "summary": summary,
        "error": None,
    }
