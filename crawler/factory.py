"""根据目标平台选择真正可用的采集器。"""

from __future__ import annotations

from typing import Optional

import config
from crawler.base import CollectorBridge
from crawler.github_bridge import GithubBridge
from crawler.mediacrawler_bridge import MediaCrawlerBridge
from crawler.reddit_rss_bridge import RedditRssBridge
from crawler.twscrape_bridge import TwscrapeBridge
from crawler.youtube_bridge import YoutubeBridge


def build_collector(platform: Optional[str] = None) -> CollectorBridge:
    """为指定平台构造采集器；不传时沿用 config.CRAWL_PLATFORM。

    多平台统一运行会在同一进程内构造多个采集器，因此平台必须可以显式传入，
    不能只依赖全局配置。
    """
    active = str(platform or getattr(config, "CRAWL_PLATFORM", "")).strip().lower()
    if active in {"x", "twitter", "x.com"}:
        return TwscrapeBridge(platform=active)
    if active in {"github", "gh"}:
        return GithubBridge(platform=active)
    if active == "reddit":
        return RedditRssBridge(platform=active)
    if active in {"youtube", "yt", "ytb"}:
        return YoutubeBridge(platform=active)
    return MediaCrawlerBridge(platform=active)
