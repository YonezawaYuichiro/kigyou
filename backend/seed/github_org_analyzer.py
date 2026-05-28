"""Phase B2: 企業の GitHub Organization を自動検出・解析する。

GitHub API（認証不要・60req/h）を使い、公開リポジトリの統計から
技術スタックの実態と開発活動の活発さを定量化する。

結果は CompanyDimensions の独立次元にはせず、
dim[2]（使用技術の鮮度）の技術スタック補強と
dim[9]（開発環境）の証拠テキスト補強として使用する。

実行例:
  from backend.seed.github_org_analyzer import analyze_github_org
  stats = analyze_github_org("サイボウズ", "https://cybozu.co.jp")
"""

import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_TIMEOUT = 10.0


def _build_github_headers() -> dict[str, str]:
    """GitHub APIヘッダーを生成する。GITHUB_TOKENがあれば認証付き（5000req/h）。"""
    headers: dict[str, str] = {
        "User-Agent": "GradMatchAI/4.0 (academic research)",
        "Accept": "application/vnd.github.v3+json",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
        logger.debug("GitHub API: 認証付きリクエスト（5000req/h）")
    else:
        logger.debug("GitHub API: 認証なし（60req/h）")
    return headers


# 180日以上更新なし = 非アクティブと判定
_INACTIVE_DAYS = 180


@dataclass
class GitHubOrgStats:
    """GitHub Organization の定量指標。"""

    org_name: str | None = None
    org_url: str | None = None
    public_repo_count: int = 0
    language_distribution: dict[str, float] = field(default_factory=dict)
    top_languages: list[str] = field(default_factory=list)
    total_stars: int = 0
    avg_last_commit_days: float | None = None  # 直近更新の平均経過日数
    active_repo_count: int = 0  # 180日以内に更新されたリポジトリ数
    fetch_error: str | None = None

    def to_evidence_text(self) -> str | None:
        """dim[9] の証拠テキストとして使える文字列を生成。"""
        if self.public_repo_count == 0:
            return None
        parts = [f"GitHub Org ({self.org_name}): 公開リポジトリ{self.public_repo_count}件"]
        if self.top_languages:
            parts.append(f"主要言語: {', '.join(self.top_languages[:4])}")
        if self.active_repo_count > 0:
            parts.append(f"アクティブ{self.active_repo_count}件")
        if self.total_stars > 0:
            parts.append(f"⭐{self.total_stars}")
        return " / ".join(parts)

    def compute_github_score(self) -> float:
        """開発活動スコア（0.0〜1.0）。dim[9]の補強用。"""
        if self.public_repo_count == 0:
            return 0.0
        repo_score = min(1.0, self.public_repo_count / 20.0)  # 20件で満点
        activity_score = (
            max(0.0, 1.0 - (self.avg_last_commit_days or _INACTIVE_DAYS) / _INACTIVE_DAYS)
            if self.avg_last_commit_days is not None
            else 0.5
        )
        star_score = min(1.0, math.log1p(self.total_stars) / 7.0)  # 1000★ ≈ 満点
        return round(repo_score * 0.30 + activity_score * 0.40 + star_score * 0.30, 3)

    def get_additional_tech_stack(self) -> list[str]:
        """GitHubで確認された技術スタック（Company.tech_stackの補強用）。"""
        return self.top_languages


def _days_since_iso(iso_str: str | None) -> int | None:
    """ISO 8601文字列から現在までの経過日数を返す。"""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return max(0, (datetime.now(tz=UTC) - dt).days)
    except (ValueError, TypeError):
        return None


def _find_org_name(company_name: str, official_url: str) -> str | None:
    """HPドメインからGitHub Orgのスラッグを推測して存在確認する。"""
    domain = urlparse(official_url).hostname or ""
    # ドメインの最初のラベルを候補として使う
    slug_candidate = domain.split(".")[0] if domain else ""
    if slug_candidate in {"www", "tech", "blog", ""}:
        parts = domain.split(".")
        slug_candidate = parts[1] if len(parts) > 1 else ""

    # 会社名のASCII部分も候補に
    ascii_name = "".join(c.lower() for c in company_name if c.isascii() and c.isalnum())

    candidates = list({slug_candidate, ascii_name} - {""})

    for slug in candidates:
        try:
            url = f"{_GITHUB_API}/orgs/{slug}"
            with httpx.Client(timeout=_TIMEOUT, headers=_build_github_headers()) as client:
                resp = client.get(url)
                if resp.status_code == 200:
                    logger.debug("[github_org] %s → org_name=%s", company_name, slug)
                    return slug
                # 404 = 存在しない、403/429 = レート制限
                if resp.status_code in {403, 429}:
                    logger.warning("[github_org] レート制限に達した")
                    return None
            time.sleep(0.5)
        except Exception as e:
            logger.debug("[github_org] %s 候補 %s: %s", company_name, slug, e)
            continue
    return None


def _fetch_org_repos(org_name: str) -> list[dict]:
    """GitHub Org の公開リポジトリ一覧を取得する（最大100件）。"""
    url = f"{_GITHUB_API}/orgs/{org_name}/repos"
    params = {"type": "public", "sort": "pushed", "per_page": 100}
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_build_github_headers()) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except Exception as e:
        logger.warning("[github_org] repos取得失敗 %s: %s", org_name, e)
        return []


def analyze_github_org(company_name: str, official_url: str) -> GitHubOrgStats:
    """企業のGitHub Orgを検出し、公開リポジトリを解析して統計を返す。

    失敗時は fetch_error を設定した GitHubOrgStats を返す（例外を上げない）。
    """
    logger.info("[github_org] %s を解析中...", company_name)

    org_name = _find_org_name(company_name, official_url)
    if org_name is None:
        return GitHubOrgStats(fetch_error="GitHub Org未検出")

    time.sleep(0.5)
    repos = _fetch_org_repos(org_name)
    if not repos:
        return GitHubOrgStats(
            org_name=org_name,
            org_url=f"https://github.com/{org_name}",
            fetch_error="リポジトリ0件",
        )

    lang_bytes: dict[str, int] = {}
    total_stars = 0
    commit_days_list: list[int] = []
    active_count = 0

    for repo in repos:
        if repo.get("fork"):
            continue  # フォークリポジトリは除外

        total_stars += repo.get("stargazers_count", 0)

        lang = repo.get("language")
        if lang:
            # bytes ではなくリポジトリ数でカウント（bytes取得にはAPIコスト高い）
            lang_bytes[lang] = lang_bytes.get(lang, 0) + 1

        pushed = repo.get("pushed_at")
        days = _days_since_iso(pushed)
        if days is not None:
            commit_days_list.append(days)
            if days <= _INACTIVE_DAYS:
                active_count += 1

    # 言語分布（リポジトリ数比率）
    total_repos = max(1, sum(lang_bytes.values()))
    lang_dist = {lang: round(count / total_repos, 3) for lang, count in lang_bytes.items()}
    top_langs = sorted(lang_dist, key=lang_dist.get, reverse=True)[:6]  # type: ignore[arg-type]

    avg_days = round(sum(commit_days_list) / len(commit_days_list), 1) if commit_days_list else None

    stats = GitHubOrgStats(
        org_name=org_name,
        org_url=f"https://github.com/{org_name}",
        public_repo_count=len(repos),
        language_distribution=lang_dist,
        top_languages=top_langs,
        total_stars=total_stars,
        avg_last_commit_days=avg_days,
        active_repo_count=active_count,
    )
    logger.info(
        "[github_org] %s: %d repos / ⭐%d / active=%d / top_lang=%s",
        org_name,
        stats.public_repo_count,
        total_stars,
        active_count,
        top_langs[:3],
    )
    return stats


if __name__ == "__main__":
    import logging as _logging
    import sys

    _logging.basicConfig(level="INFO")

    test_cases = [
        ("サイボウズ", "https://cybozu.co.jp"),
        ("freee", "https://corp.freee.co.jp"),
        ("NTTデータ", "https://www.nttdata.com/jp/ja/"),
    ]

    name = sys.argv[1] if len(sys.argv) > 1 else None
    url = sys.argv[2] if len(sys.argv) > 2 else None

    if name and url:
        stats = analyze_github_org(name, url)
        print(f"\n{name}: {stats}")
        print(f"  スコア: {stats.compute_github_score():.3f}")
        print(f"  証拠:   {stats.to_evidence_text()}")
    else:
        for n, u in test_cases:
            stats = analyze_github_org(n, u)
            print(f"{n}: score={stats.compute_github_score():.3f} | {stats.to_evidence_text()}")
            time.sleep(2)
