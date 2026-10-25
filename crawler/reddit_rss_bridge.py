"""Reddit Atom/RSS 采集桥接器。

当前实现只读取配置中明确列出的 subreddit ``new/.rss``，再在本地执行
时间窗口、格式校验和帖子 ID 去重。关键词只记录命中情况，不参与前置淘汰。
不使用 OAuth、PRAW、登录 Cookie 或代理。
"""

from __future__ import annotations

import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional, Set, Tuple
from urllib.parse import quote, urlparse
from uuid import uuid4

import config
from crawler.base import CrawlRunResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"
MEDIA_NAMESPACE = "http://search.yahoo.com/mrss/"
ATOM = f"{{{ATOM_NAMESPACE}}}"
MEDIA = f"{{{MEDIA_NAMESPACE}}}"
SUBREDDIT_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


class RedditRssError(RuntimeError):
    """Reddit RSS 请求或 Atom 响应无法继续处理。"""


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _clean_subreddit(value: object) -> str:
    subreddit = str(value).strip()
    if subreddit.casefold().startswith("r/"):
        subreddit = subreddit[2:]
    return subreddit


def _as_float(value: object) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class _RssContentParser(HTMLParser):
    """从 Reddit Atom 的 HTML fragment 中提取正文和 ``[link]`` 地址。"""

    BLOCK_TAGS = {
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ol",
        "p",
        "pre",
        "table",
        "td",
        "tr",
        "ul",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._md_depth = 0
        self._body_parts: List[str] = []
        self._anchor_href = ""
        self._anchor_parts: List[str] = []
        self.external_url = ""

    def handle_starttag(
        self,
        tag: str,
        attrs: List[Tuple[str, Optional[str]]],
    ) -> None:
        attributes = {key: value or "" for key, value in attrs}
        classes = set(attributes.get("class", "").split())
        if tag == "div" and self._md_depth == 0 and "md" in classes:
            self._md_depth = 1
        elif self._md_depth > 0 and tag == "div":
            self._md_depth += 1

        if self._md_depth > 0 and tag in self.BLOCK_TAGS:
            self._append_break()
        if tag == "a":
            self._anchor_href = attributes.get("href", "").strip()
            self._anchor_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            label = "".join(self._anchor_parts).strip().casefold()
            if label == "[link]" and self._anchor_href:
                self.external_url = self._anchor_href
            self._anchor_href = ""
            self._anchor_parts = []

        if self._md_depth > 0 and tag in self.BLOCK_TAGS:
            self._append_break()
        if tag == "div" and self._md_depth > 0:
            self._md_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_parts.append(data)
        if self._md_depth > 0:
            self._body_parts.append(data)

    def _append_break(self) -> None:
        if self._body_parts and self._body_parts[-1] != "\n":
            self._body_parts.append("\n")

    def body_text(self) -> str:
        lines = [
            " ".join(line.split())
            for line in "".join(self._body_parts).splitlines()
        ]
        return "\n".join(line for line in lines if line).strip()


class RedditRssBridge:
    """采集 subreddit 最新 Atom feed，输出项目现有的内容 JSONL。"""

    def __init__(
        self,
        requester: Optional[Callable[..., object]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
        sleeper: Optional[Callable[[float], None]] = None,
        platform: Optional[str] = None,
    ) -> None:
        self.platform = "reddit"
        self.configured_platform = str(
            platform or config.CRAWL_PLATFORM
        ).strip().casefold()
        configured_keywords = getattr(
            config,
            "REDDIT_RSS_KEYWORDS",
            config.SEARCH_KEYWORDS,
        )
        if isinstance(configured_keywords, str):
            configured_keywords = [configured_keywords]
        self.keywords = [
            str(keyword).strip() for keyword in configured_keywords
        ]
        raw_subreddits = getattr(config, "REDDIT_RSS_SUBREDDITS", [])
        if isinstance(raw_subreddits, str):
            raw_subreddits = [raw_subreddits]
        self.subreddits = [
            _clean_subreddit(subreddit) for subreddit in raw_subreddits
        ]
        self.limit = getattr(config, "REDDIT_RSS_MAX_CANDIDATES", 0)
        self.per_subreddit_limit = getattr(
            config, "REDDIT_RSS_RESULTS_PER_SUBREDDIT", 100
        )
        self.lookback_hours = getattr(config, "REDDIT_RSS_LOOKBACK_HOURS", 168)
        self.request_timeout = getattr(
            config, "REDDIT_RSS_REQUEST_TIMEOUT_SECONDS", 30
        )
        self.request_interval = getattr(
            config, "REDDIT_RSS_REQUEST_INTERVAL_SECONDS", 31
        )
        self.max_response_bytes = getattr(
            config, "REDDIT_RSS_MAX_RESPONSE_BYTES", 5_000_000
        )
        self.user_agent = str(
            getattr(config, "REDDIT_RSS_USER_AGENT", "")
        ).strip()
        self.base_url = str(
            getattr(config, "REDDIT_RSS_BASE_URL", "https://www.reddit.com")
        ).strip().rstrip("/")
        self.state_file = _resolve_project_path(config.REDDIT_RSS_STATE_FILE)
        self.seen_id_limit = getattr(config, "REDDIT_RSS_SEEN_ID_LIMIT", 5000)
        self.target_data_dir = _resolve_project_path(config.DATA_DIR)
        self._requester = requester
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._sleeper = sleeper or time.sleep
        self._next_request_delay = 0.0
        self._pending_ids: Tuple[str, ...] = ()

    def validate(self) -> List[str]:
        """返回阻止 Reddit RSS 采集启动的配置错误。"""
        errors: List[str] = []
        if self.configured_platform != "reddit":
            errors.append(
                f"CRAWL_PLATFORM={self.configured_platform!r} 不能由 Reddit RSS "
                "采集器处理；请使用 reddit"
            )
        if any(not keyword for keyword in self.keywords):
            errors.append("REDDIT_RSS_KEYWORDS 不能包含空关键词")
        if not self.subreddits or any(not subreddit for subreddit in self.subreddits):
            errors.append("REDDIT_RSS_SUBREDDITS 必须至少包含一个明确的 subreddit")
        for subreddit in self.subreddits:
            if subreddit.casefold() == "all":
                errors.append(
                    "REDDIT_RSS_SUBREDDITS 不使用 all；请列出明确社区"
                )
            elif not SUBREDDIT_PATTERN.fullmatch(subreddit):
                errors.append(
                    f"REDDIT_RSS_SUBREDDITS 包含无效社区名称：{subreddit!r}"
                )
        if (
            not isinstance(self.limit, int)
            or isinstance(self.limit, bool)
            or self.limit < 0
        ):
            errors.append("REDDIT_RSS_MAX_CANDIDATES 必须是非负整数")
        if (
            not isinstance(self.per_subreddit_limit, int)
            or isinstance(self.per_subreddit_limit, bool)
            or not 1 <= self.per_subreddit_limit <= 100
        ):
            errors.append("REDDIT_RSS_RESULTS_PER_SUBREDDIT 必须在 1 到 100 之间")
        if (
            not isinstance(self.lookback_hours, (int, float))
            or self.lookback_hours <= 0
        ):
            errors.append("REDDIT_RSS_LOOKBACK_HOURS 必须大于 0")
        if (
            not isinstance(self.request_timeout, (int, float))
            or self.request_timeout <= 0
        ):
            errors.append("REDDIT_RSS_REQUEST_TIMEOUT_SECONDS 必须大于 0")
        if (
            not isinstance(self.request_interval, (int, float))
            or self.request_interval < 1
        ):
            errors.append("REDDIT_RSS_REQUEST_INTERVAL_SECONDS 必须至少为 1")
        if (
            not isinstance(self.max_response_bytes, int)
            or self.max_response_bytes <= 0
        ):
            errors.append("REDDIT_RSS_MAX_RESPONSE_BYTES 必须是正整数")
        if not isinstance(self.seen_id_limit, int) or self.seen_id_limit <= 0:
            errors.append("REDDIT_RSS_SEEN_ID_LIMIT 必须是正整数")
        if not self.user_agent or self.user_agent.casefold() in {
            "your-user-agent",
            "change-me",
        }:
            errors.append("REDDIT_RSS_USER_AGENT 必须如实标识本项目")

        parsed_base_url = urlparse(self.base_url)
        if (
            parsed_base_url.scheme not in {"http", "https"}
            or not parsed_base_url.netloc
        ):
            errors.append("REDDIT_RSS_BASE_URL 必须是有效的 http/https URL")

        state_error = self._state_error()
        if state_error:
            errors.append(state_error)
        return errors

    def run(self) -> CrawlRunResult:
        """执行一次 Reddit RSS 采集并写入本次运行的 JSONL。"""
        errors = self.validate()
        if errors:
            return CrawlRunResult(success=False, error="；".join(errors))

        now = self._as_utc(self._now_provider())
        run_id = f"{now:%Y%m%dT%H%M%S%f}-{uuid4().hex[:8]}"
        output_dir = self.target_data_dir / "crawler_runs" / run_id
        content_dir = output_dir / self.platform / "jsonl"
        output_file = content_dir / f"search_contents_{now:%Y-%m-%d}.jsonl"
        self._pending_ids = ()
        self._next_request_delay = float(self.request_interval)

        print(f"Reddit RSS 社区：{', '.join(self.subreddits)}")
        print(
            "Reddit 关键词命中仅记录、不前置淘汰："
            f"{', '.join(self.keywords) or '未配置'}"
        )
        print(f"Reddit 本次输出：{output_dir}")

        try:
            rows = self._collect(now)
        except (OSError, TypeError, ValueError, RuntimeError, ET.ParseError) as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"Reddit RSS 采集失败：{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"Reddit RSS 请求失败：{type(exc).__name__}: {exc}",
            )

        if not rows:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error="Reddit RSS 请求完成，但本次没有可处理的新内容",
            )

        try:
            content_dir.mkdir(parents=True, exist_ok=True)
            temporary_file = output_file.with_suffix(".jsonl.tmp")
            with temporary_file.open("w", encoding="utf-8") as file_obj:
                for row in rows:
                    file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")
            temporary_file.replace(output_file)
        except OSError as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"Reddit RSS 数据写入失败：{exc}",
            )

        self._pending_ids = tuple(str(row["id"]) for row in rows)
        return CrawlRunResult(
            success=True,
            data_files=(output_file,),
            output_dir=output_dir,
            returncode=0,
        )

    def acknowledge(self) -> str:
        """完整工作流成功后再记录已处理帖子 ID。"""
        if not self._pending_ids:
            return ""
        try:
            existing = self._load_seen_ids()
            ordered_ids = list(existing)
            ordered_ids.extend(
                post_id for post_id in self._pending_ids if post_id not in existing
            )
            ordered_ids = ordered_ids[-self.seen_id_limit :]
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_file = self.state_file.with_suffix(
                self.state_file.suffix + ".tmp"
            )
            temporary_file.write_text(
                json.dumps(
                    {"version": 1, "seen_ids": ordered_ids},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary_file.replace(self.state_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return f"无法保存 Reddit RSS 已处理状态：{exc}"
        self._pending_ids = ()
        return ""

    def _collect(self, now: datetime) -> List[Dict[str, object]]:
        seen_ids = set(self._load_seen_ids())
        current_ids: Set[str] = set()
        collected: List[Tuple[float, Dict[str, object]]] = []
        cutoff = now - timedelta(hours=float(self.lookback_hours))
        failures: List[str] = []
        successful_fetches = 0

        for index, subreddit_name in enumerate(self.subreddits):
            if index:
                delay = max(float(self.request_interval), self._next_request_delay)
                print(f"Reddit RSS 请求间隔：等待 {delay:g} 秒")
                self._sleeper(delay)
            try:
                entries = self._fetch_subreddit(subreddit_name)
            except RedditRssError as exc:
                failures.append(str(exc))
                continue

            successful_fetches += 1
            for entry in entries:
                row = self._entry_to_row(
                    entry,
                    subreddit_scope=subreddit_name,
                    cutoff=cutoff,
                )
                if row is None:
                    continue
                post_id = str(row["id"])
                if post_id in seen_ids or post_id in current_ids:
                    continue
                current_ids.add(post_id)
                sort_time = float(row.pop("_sort_time"))
                collected.append((sort_time, row))

        if not successful_fetches and failures:
            raise RedditRssError("；".join(failures))
        for failure in failures:
            print(f"Reddit RSS 跳过：{failure}")

        collected.sort(key=lambda item: item[0], reverse=True)
        rows = [row for _, row in collected]
        return rows[: self.limit] if self.limit else rows

    def _fetch_subreddit(self, subreddit_name: str) -> List[ET.Element]:
        url = (
            f"{self.base_url}/r/"
            f"{quote(subreddit_name, safe='')}/new/.rss"
        )
        requester = self._requester
        request_error_types: Tuple[type, ...] = (OSError,)
        if requester is None:
            try:
                import requests
            except ImportError as exc:
                raise RedditRssError(
                    "缺少 requests；请运行 "
                    "python3 -m pip install -r requirements.txt"
                ) from exc
            requester = requests.get
            request_error_types = (requests.RequestException,)

        try:
            response = requester(
                url,
                params={"limit": self.per_subreddit_limit},
                headers={
                    "Accept": "application/atom+xml, application/xml;q=0.9",
                    "User-Agent": self.user_agent,
                },
                timeout=float(self.request_timeout),
            )
        except request_error_types as exc:
            raise RedditRssError(
                f"r/{subreddit_name} 网络请求失败：{exc}"
            ) from exc

        headers = getattr(response, "headers", {})
        if not isinstance(headers, Mapping):
            headers = {}
        retry_after = _as_float(headers.get("Retry-After"))
        rate_reset = _as_float(headers.get("x-ratelimit-reset"))
        rate_remaining = _as_float(headers.get("x-ratelimit-remaining"))
        if rate_remaining <= 0 and rate_reset:
            self._next_request_delay = max(
                float(self.request_interval), rate_reset + 1
            )
        else:
            self._next_request_delay = float(self.request_interval)

        try:
            status_code = int(getattr(response, "status_code", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        if status_code == 429:
            self._next_request_delay = max(
                float(self.request_interval), retry_after, rate_reset + 1
            )
            wait_hint = max(retry_after, rate_reset)
            suffix = f"，建议等待 {wait_hint:g} 秒" if wait_hint else ""
            raise RedditRssError(f"r/{subreddit_name} 被限流（429）{suffix}")
        if status_code == 403:
            raise RedditRssError(f"r/{subreddit_name} RSS 被拒绝访问（403）")
        if status_code != 200:
            raise RedditRssError(
                f"r/{subreddit_name} RSS 返回 HTTP {status_code or '未知状态'}"
            )

        payload = str(getattr(response, "text", "") or "")
        if not payload:
            raise RedditRssError(f"r/{subreddit_name} RSS 响应为空")
        if len(payload.encode("utf-8")) > self.max_response_bytes:
            raise RedditRssError(f"r/{subreddit_name} RSS 响应超过大小限制")
        if "<!DOCTYPE" in payload.upper():
            raise RedditRssError(f"r/{subreddit_name} RSS 包含不允许的 DOCTYPE")

        try:
            root = ET.fromstring(payload)
        except ET.ParseError as exc:
            raise RedditRssError(
                f"r/{subreddit_name} 未返回合法 Atom XML"
            ) from exc
        if root.tag != f"{ATOM}feed":
            raise RedditRssError(f"r/{subreddit_name} RSS 根节点不是 Atom feed")
        return list(root.findall(f"{ATOM}entry"))

    def _entry_to_row(
        self,
        entry: ET.Element,
        subreddit_scope: str,
        cutoff: datetime,
    ) -> Optional[Dict[str, object]]:
        raw_id = (entry.findtext(f"{ATOM}id") or "").strip()
        post_id = raw_id[3:] if raw_id.startswith("t3_") else raw_id
        title = (entry.findtext(f"{ATOM}title") or "").strip()
        published_at = _parse_datetime(
            (entry.findtext(f"{ATOM}published") or "").strip()
            or (entry.findtext(f"{ATOM}updated") or "").strip()
        )
        if not post_id or not title or published_at is None:
            return None
        if published_at < self._as_utc(cutoff):
            return None

        content_node = entry.find(f"{ATOM}content")
        content_html = content_node.text if content_node is not None else ""
        content_parser = _RssContentParser()
        try:
            content_parser.feed(content_html or "")
            content_parser.close()
        except (TypeError, ValueError):
            return None
        body = content_parser.body_text()
        searchable_text = f"{title}\n{body}".casefold()
        matched_keywords = [
            keyword
            for keyword in self.keywords
            if keyword.casefold() in searchable_text
        ]

        post_url = ""
        for link_node in entry.findall(f"{ATOM}link"):
            href = str(link_node.attrib.get("href", "") or "").strip()
            rel = str(link_node.attrib.get("rel", "alternate") or "alternate")
            if href and rel == "alternate":
                post_url = href
                break
            if href and not post_url:
                post_url = href

        author = (
            entry.findtext(f"{ATOM}author/{ATOM}name") or "[deleted]"
        ).strip()
        if author.casefold().startswith("/u/"):
            author = author[3:]
        category_node = entry.find(f"{ATOM}category")
        subreddit = (
            str(category_node.attrib.get("term", "") or "").strip()
            if category_node is not None
            else ""
        )
        thumbnail_node = entry.find(f"{MEDIA}thumbnail")
        thumbnail_url = (
            str(thumbnail_node.attrib.get("url", "") or "").strip()
            if thumbnail_node is not None
            else ""
        )
        external_url = content_parser.external_url
        if external_url == post_url:
            external_url = ""

        return {
            "id": post_id,
            "title": title,
            "content": body or title,
            "source": "Reddit",
            "url": post_url,
            "external_url": external_url,
            "thumbnail_url": thumbnail_url,
            "author": author,
            "publish_time": published_at.isoformat(),
            "subreddit": subreddit or subreddit_scope,
            "score": None,
            "upvote_ratio": None,
            "comment_count": None,
            "metrics_available": False,
            "matched_keywords": matched_keywords,
            "search_keyword": matched_keywords[0] if matched_keywords else "",
            "search_subreddit": subreddit_scope,
            "collection_method": "reddit_rss",
            "_sort_time": published_at.timestamp(),
        }

    def _state_error(self) -> str:
        if not self.state_file.exists():
            return ""
        try:
            self._load_seen_ids()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return f"Reddit RSS 状态文件无效：{exc}"
        return ""

    def _load_seen_ids(self) -> List[str]:
        if not self.state_file.exists():
            return []
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("不支持的状态文件格式")
        seen_ids = data.get("seen_ids")
        if not isinstance(seen_ids, list) or any(
            not isinstance(post_id, str) for post_id in seen_ids
        ):
            raise ValueError("seen_ids 必须是字符串列表")
        return seen_ids

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
