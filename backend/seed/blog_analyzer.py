"""Phase B1: 企業の技術ブログを自動検出・定量化する。

データソース:
  - Zenn Publication (https://zenn.dev/p/{slug})
  - Qiita Organization (https://qiita.com/organizations/{org_id}/items)
  - 企業公式HP内のブログセクション

結果は CompanyDimensions の独立次元にはせず、
dim[2]（使用技術の鮮度）の技術タグ補強と
dim[5]（社風・カルチャー）の証拠テキスト補強として使用する。

実行例:
  from backend.seed.blog_analyzer import analyze_company_blog
  stats = analyze_company_blog("サイボウズ", "https://cybozu.co.jp")
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_HEADERS = {"User-Agent": "GradMatchAI/4.0 (academic research; contact: gradmatch@example.com)"}
_ZENN_API = "https://zenn.dev/api/articles"
_QIITA_API = "https://qiita.com/api/v2/organizations/{org_id}/items"

# 1年前のUTC timestamp（記事の「直近1年」フィルタ用）
_ONE_YEAR_AGO = datetime.now(tz=UTC).replace(year=datetime.now().year - 1)


@dataclass
class BlogStats:
    """技術ブログの定量指標。"""

    blog_url: str | None = None
    platform: str | None = None  # "zenn" / "qiita" / "official" / None
    article_count_1yr: int = 0
    post_frequency_per_month: float = 0.0  # 月平均投稿数
    unique_authors: int = 0
    tech_tags: list[str] = field(default_factory=list)  # 上位タグ
    last_post_days_ago: int | None = None  # 最終投稿からの経過日数
    fetch_error: str | None = None  # エラー時のメッセージ

    def to_evidence_text(self) -> str | None:
        """dim[2]/dim[5] の証拠テキストとして使える文字列を生成。"""
        if self.article_count_1yr == 0:
            return None
        parts = [f"技術ブログ({self.platform}): 直近1年{self.article_count_1yr}記事"]
        if self.unique_authors > 1:
            parts.append(f"著者{self.unique_authors}名")
        if self.tech_tags:
            parts.append(f"タグ: {', '.join(self.tech_tags[:5])}")
        return " / ".join(parts)

    def compute_blog_score(self) -> float:
        """技術発信活動スコア（0.0〜1.0）。dim[2]の補強用。"""
        if self.article_count_1yr == 0:
            return 0.0
        # 月4本・著者5人・30日以内で満点
        freq_score = min(1.0, self.post_frequency_per_month / 4.0)
        author_score = min(1.0, self.unique_authors / 5.0)
        recency_score = (
            max(0.0, 1.0 - (self.last_post_days_ago or 365) / 90.0)
            if self.last_post_days_ago is not None
            else 0.5
        )
        return round(freq_score * 0.40 + author_score * 0.40 + recency_score * 0.20, 3)


def _days_since(iso_str: str) -> int | None:
    """ISO 8601文字列から現在までの経過日数を返す。パース失敗時はNone。"""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        delta = datetime.now(tz=UTC) - dt
        return max(0, delta.days)
    except (ValueError, TypeError):
        return None


def _fetch_zenn_articles(org_slug: str) -> BlogStats:
    """Zenn Publication の記事統計を取得する。"""
    url = f"https://zenn.dev/api/articles?username={org_slug}&order=latest&count=50"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return BlogStats(fetch_error=str(e))

    articles = data.get("articles", [])
    if not articles:
        return BlogStats(blog_url=f"https://zenn.dev/{org_slug}", platform="zenn")

    count_1yr = 0
    authors: set[str] = set()
    tags: list[str] = []
    last_days: int | None = None

    for art in articles:
        published = art.get("published_at", "")
        days = _days_since(published)
        if last_days is None or (days is not None and days < last_days):
            last_days = days
        if days is not None and days <= 365:
            count_1yr += 1
        author = art.get("user", {}).get("username", "")
        if author:
            authors.add(author)
        for t in art.get("topics", []):
            name = t.get("name", "")
            if name and name not in tags:
                tags.append(name)

    freq = round(count_1yr / 12.0, 2)
    return BlogStats(
        blog_url=f"https://zenn.dev/{org_slug}",
        platform="zenn",
        article_count_1yr=count_1yr,
        post_frequency_per_month=freq,
        unique_authors=len(authors),
        tech_tags=tags[:10],
        last_post_days_ago=last_days,
    )


def _fetch_qiita_articles(org_id: str) -> BlogStats:
    """Qiita Organization の記事統計を取得する。"""
    url = _QIITA_API.format(org_id=org_id) + "?per_page=50"
    try:
        with httpx.Client(timeout=_TIMEOUT, headers=_HEADERS) as client:
            resp = client.get(url)
            resp.raise_for_status()
            articles = resp.json()
    except Exception as e:
        return BlogStats(fetch_error=str(e))

    if not isinstance(articles, list) or not articles:
        return BlogStats(blog_url=f"https://qiita.com/organizations/{org_id}", platform="qiita")

    count_1yr = 0
    authors: set[str] = set()
    tags: list[str] = []
    last_days: int | None = None

    for art in articles:
        created = art.get("created_at", "")
        days = _days_since(created)
        if last_days is None or (days is not None and days < last_days):
            last_days = days
        if days is not None and days <= 365:
            count_1yr += 1
        author = art.get("user", {}).get("id", "")
        if author:
            authors.add(author)
        for t in art.get("tags", []):
            name = t.get("name", "")
            if name and name not in tags:
                tags.append(name)

    freq = round(count_1yr / 12.0, 2)
    return BlogStats(
        blog_url=f"https://qiita.com/organizations/{org_id}",
        platform="qiita",
        article_count_1yr=count_1yr,
        post_frequency_per_month=freq,
        unique_authors=len(authors),
        tech_tags=tags[:10],
        last_post_days_ago=last_days,
    )


def _guess_blog_url_from_hp(company_name: str, official_url: str) -> tuple[str, str] | None:
    """HPのドメインからZenn/Qiitaのslugを推測する。

    Returns:
        (platform, slug) のタプル、または None
    """
    domain = urlparse(official_url).hostname or ""
    # ドメインの最初のラベルを slug 候補として使う (例: cybozu.co.jp → cybozu)
    slug_candidate = domain.split(".")[0] if domain else ""
    if not slug_candidate or slug_candidate in {"www", "tech", "blog"}:
        # second label を試す
        parts = domain.split(".")
        slug_candidate = parts[1] if len(parts) > 1 else ""

    candidates = []
    if slug_candidate:
        candidates.append(("zenn", slug_candidate))
        candidates.append(("qiita", slug_candidate))

    # 会社名をローマ字化した slug も試す（簡易版: ASCII 文字のみ抽出）
    ascii_name = "".join(c.lower() for c in company_name if c.isascii() and c.isalnum())
    if ascii_name and ascii_name != slug_candidate:
        candidates.append(("zenn", ascii_name))
        candidates.append(("qiita", ascii_name))

    for platform, slug in candidates:
        try:
            if platform == "zenn":
                url = f"https://zenn.dev/api/articles?username={slug}&count=1"
                with httpx.Client(timeout=5.0, headers=_HEADERS) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("articles"):
                            return (platform, slug)
            elif platform == "qiita":
                url = f"https://qiita.com/api/v2/organizations/{slug}/items?per_page=1"
                with httpx.Client(timeout=5.0, headers=_HEADERS) as client:
                    resp = client.get(url)
                    if resp.status_code == 200:
                        arts = resp.json()
                        if isinstance(arts, list) and arts:
                            return (platform, slug)
            time.sleep(0.3)
        except Exception:
            continue
    return None


def analyze_company_blog(company_name: str, official_url: str) -> BlogStats:
    """企業の技術ブログを自動検出して統計を返す。

    1. HPドメインから Zenn/Qiita の slug を推測
    2. 推測できない場合は BlogStats(fetch_error="ブログ未検出") を返す
    """
    logger.info("[blog_analyzer] %s を解析中...", company_name)

    result = _guess_blog_url_from_hp(company_name, official_url)
    if result is None:
        logger.debug("[blog_analyzer] %s: ブログURL未検出", company_name)
        return BlogStats(fetch_error="ブログ未検出")

    platform, slug = result
    time.sleep(0.5)

    if platform == "zenn":
        stats = _fetch_zenn_articles(slug)
    else:
        stats = _fetch_qiita_articles(slug)

    logger.info(
        "[blog_analyzer] %s: %s 記事%d件 / 著者%d名",
        company_name,
        platform,
        stats.article_count_1yr,
        stats.unique_authors,
    )
    return stats


if __name__ == "__main__":
    import logging as _logging
    import sys

    _logging.basicConfig(level="INFO")

    test_cases = [
        ("サイボウズ", "https://cybozu.co.jp"),
        ("freee", "https://corp.freee.co.jp"),
        ("パナソニック", "https://www.panasonic.com/jp/"),
    ]

    name = sys.argv[1] if len(sys.argv) > 1 else None
    url = sys.argv[2] if len(sys.argv) > 2 else None

    if name and url:
        stats = analyze_company_blog(name, url)
        print(f"\n{name}: {stats}")
        print(f"  スコア: {stats.compute_blog_score():.3f}")
        print(f"  証拠:   {stats.to_evidence_text()}")
    else:
        for n, u in test_cases:
            stats = analyze_company_blog(n, u)
            print(f"{n}: score={stats.compute_blog_score():.3f} | {stats.to_evidence_text()}")
            time.sleep(1)
