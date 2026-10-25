"""各平台的采集 + 筛选适配：复用既有 pipeline，不重复实现筛选逻辑。"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import config
from analyzer.filter import recent_keyword_filter
from crawler.base import CollectorBridge
from pipeline.types import DigestItem
from utils.parser import load_articles
from workflows.outcome import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    PlatformOutcome,
)

PLATFORM_LABELS = {
    "xhs": "小红书",
    "zhihu": "知乎",
    "github": "GitHub",
    "x": "X",
    "reddit": "Reddit",
    "youtube": "YouTube",
}

REPORT_PLATFORMS = {"xhs", "zhihu", "github"}
GENERIC_PLATFORMS = {"xhs", "zhihu"}


def platform_label(platform: str) -> str:
    return PLATFORM_LABELS.get(platform, platform)


def collect_platform(platform: str, bridge: CollectorBridge) -> PlatformOutcome:
    """运行单个平台的采集与筛选阶段，异常全部收敛为失败结果。"""
    try:
        if platform in GENERIC_PLATFORMS:
            return _collect_generic(platform, bridge)
        if platform == "github":
            return _collect_github(bridge)
        if platform == "x":
            from workflows.twitter import build_twitter_digest

            return build_twitter_digest(bridge)
        if platform == "reddit":
            from workflows.reddit import build_reddit_digest

            return build_reddit_digest(bridge)
        if platform == "youtube":
            from workflows.youtube import build_youtube_digest

            return build_youtube_digest(bridge)
    except Exception as exc:  # noqa: BLE001 - 单平台失败不能中断整次运行
        return PlatformOutcome(
            platform,
            STATUS_FAILED,
            1,
            error=f"{type(exc).__name__}: {exc}",
        )
    return PlatformOutcome(
        platform, STATUS_FAILED, 2, error=f"不支持的平台：{platform}"
    )


def _collect_generic(platform: str, bridge: CollectorBridge) -> PlatformOutcome:
    print(f"\n[{platform_label(platform)} 1/3] 启动数据采集")
    crawl = bridge.run()
    if not crawl.success:
        return PlatformOutcome(
            platform, STATUS_FAILED, 3, error=crawl.error
        )

    print(f"[{platform_label(platform)} 2/3] 加载本次运行数据")
    articles = load_articles(
        crawl.data_files,
        platform=platform,
        allow_manual_fallback=False,
    )
    if not articles:
        return PlatformOutcome(platform, STATUS_EMPTY, 4)

    articles, recent_stats = recent_keyword_filter(articles)
    if not articles:
        return PlatformOutcome(
            platform, STATUS_EMPTY, 4, meta={"recent_stats": recent_stats}
        )

    # 复用 main.py 中的唯一实现，避免两套筛选循环产生分歧。
    from main import run_generic_filters

    passed_items, dropped = run_generic_filters(articles)
    return PlatformOutcome(
        platform=platform,
        status=STATUS_OK,
        crawled=len(articles),
        passed=len(passed_items),
        items=passed_items,
        meta={"dropped": dropped, "recent_stats": recent_stats},
    )


def _collect_github(bridge: CollectorBridge) -> PlatformOutcome:
    print("\n[GitHub 1/3] 启动仓库搜索")
    crawl = bridge.run()
    if not crawl.success:
        return PlatformOutcome("github", STATUS_FAILED, 3, error=crawl.error)

    print("[GitHub 2/3] 加载本次运行数据")
    articles = load_articles(
        crawl.data_files,
        platform="github",
        allow_manual_fallback=False,
    )
    if not articles:
        return PlatformOutcome("github", STATUS_EMPTY, 4)

    from main import _run_github_filters

    passed_items, dropped = _run_github_filters(articles)
    return PlatformOutcome(
        platform="github",
        status=STATUS_OK,
        crawled=len(articles),
        passed=len(passed_items),
        items=passed_items,
        meta={"dropped": dropped},
    )


# ============================================================
# 平台原生条目 → DigestItem
# ============================================================


def _stable_uid(platform: str, seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"{platform}:{digest}"


def _as_datetime(value: object) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _from_report_item(platform: str, raw: Dict) -> DigestItem:
    article = raw.get("article", {})
    article = article if isinstance(article, dict) else {}
    title = str(article.get("title") or "无标题")
    url = str(article.get("url") or "")
    return DigestItem(
        platform=platform,
        uid=_stable_uid(
            platform,
            url or f"{title}|{str(article.get('content', ''))[:200]}",
        ),
        title=title,
        content=str(article.get("content") or ""),
        abstract=str(raw.get("summary") or "")[:180],
        score=float(raw.get("total_score") or 0.0),
        url=url,
        tags=[str(raw.get("category"))] if raw.get("category") else [],
        published_at=_as_datetime(article.get("publish_time")),
        audit={"filter_result": raw.get("filter_result", {})},
        report_eligible=True,
        source_item=raw,
    )


def _from_twitter(raw: Dict) -> DigestItem:
    publication = raw.get("publication_metadata")
    publication = publication if isinstance(publication, dict) else {}
    return DigestItem(
        platform="x",
        uid=str(raw.get("id") or _stable_uid("x", str(raw.get("source_url", "")))),
        title=str(raw.get("title") or "无标题"),
        content=str(raw.get("content") or ""),
        abstract=str(raw.get("abstract") or "")[:180],
        score=float(publication.get("score") or 0.0),
        url=str(raw.get("source_url") or ""),
        tags=[str(tag) for tag in (raw.get("tags") or [])],
        published_at=_as_datetime(raw.get("published_at")),
        metrics=raw.get("metrics") if isinstance(raw.get("metrics"), dict) else {},
        audit={"publication": publication},
        source_item=raw,
    )


def _from_reddit(raw: Dict) -> DigestItem:
    return DigestItem(
        platform="reddit",
        uid=str(raw.get("id") or _stable_uid("reddit", str(raw.get("source_url", "")))),
        title=str(raw.get("title") or "无标题"),
        content=str(raw.get("content") or ""),
        abstract=str(raw.get("abstract") or "")[:180],
        score=float(raw.get("score") or 0.0),
        url=str(raw.get("source_url") or ""),
        tags=[str(raw["subreddit"])] if raw.get("subreddit") else [],
        published_at=_as_datetime(raw.get("published_at")),
        metrics={"subreddit": raw.get("subreddit", "")},
        audit={"filter_metadata": raw.get("filter_metadata", {})},
        source_item=raw,
    )


def _from_youtube(raw: Dict) -> DigestItem:
    return DigestItem(
        platform="youtube",
        uid=str(raw.get("id") or _stable_uid("youtube", str(raw.get("source_url", "")))),
        title=str(raw.get("title") or "无标题"),
        content=str(raw.get("content") or ""),
        abstract=str(raw.get("abstract") or "")[:180],
        score=float(raw.get("score") or 0.0),
        url=str(raw.get("source_url") or ""),
        published_at=_as_datetime(raw.get("published_at")),
        source_item=raw,
    )


_CONVERTERS = {
    "x": _from_twitter,
    "reddit": _from_reddit,
    "youtube": _from_youtube,
}


def to_digest_items(outcome: PlatformOutcome) -> List[DigestItem]:
    """把一个平台阶段的产出转换成统一候选项，并写入平台内名次。"""
    converter = _CONVERTERS.get(outcome.platform)
    converted: List[DigestItem] = []
    for index, raw in enumerate(outcome.items, start=1):
        if not isinstance(raw, dict):
            continue
        item = (
            converter(raw)
            if converter
            else _from_report_item(outcome.platform, raw)
        )
        item.rank_in_platform = index
        converted.append(item)
    return converted


def group_by_platform(
    outcomes: Sequence[PlatformOutcome],
) -> Dict[str, List[DigestItem]]:
    grouped: Dict[str, List[DigestItem]] = {}
    for outcome in outcomes:
        if outcome.status != STATUS_OK:
            continue
        items = to_digest_items(outcome)
        if items:
            grouped[outcome.platform] = items
    return grouped
