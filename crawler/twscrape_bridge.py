"""twscrape 关键词搜索桥接器。

当前实现直接对齐 PyPI ``twscrape==0.20.1`` 的异步接口：
``API(pool=...)``、``AccountsPool.add_account_cookies`` 与 ``API.search``。
twscrape 使用 X 的非公开 GraphQL 接口，运行需要只保存在本机的浏览器 Cookie。
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from importlib import metadata
from pathlib import Path
from typing import (
    Any,
    AsyncIterator,
    Callable,
    Dict,
    List,
    Optional,
    Sequence,
    Tuple,
)
from uuid import uuid4

import config
from crawler.base import CrawlRunResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PLATFORMS = {"x", "twitter", "x.com"}
SUPPORTED_PRODUCTS = {"Latest", "Top", "Media"}
REQUIRED_COOKIE_KEYS = {"auth_token", "ct0"}
MINIMUM_PYTHON = (3, 10)


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def _safe_attr(obj: object, name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, name)
    except (AttributeError, KeyError, TypeError, ValueError):
        return default


def _safe_nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _extract_links(tweet: object) -> List[Dict[str, str]]:
    """提取 twscrape 已展开的外部链接。"""
    links: List[Dict[str, str]] = []
    for link in _safe_attr(tweet, "links", []) or []:
        url = str(_safe_attr(link, "url", "") or "").strip()
        if not url:
            continue
        links.append(
            {
                "url": url,
                "label": str(_safe_attr(link, "text", "") or url).strip(),
                "short_url": str(_safe_attr(link, "tcourl", "") or "").strip(),
            }
        )
    return links


class TwscrapeBridge:
    """校验本地会话、搜索 X，并输出本次运行的标准 JSONL。"""

    def __init__(
        self,
        api_factory: Optional[Callable[..., object]] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
        platform: Optional[str] = None,
    ) -> None:
        self.platform = "x"
        self.configured_platform = str(
            platform or config.CRAWL_PLATFORM
        ).strip().lower()
        self.keywords = [str(keyword).strip() for keyword in config.SEARCH_KEYWORDS]
        self.per_query_limit = getattr(config, "TWSCRAPE_RESULTS_PER_QUERY", 20)
        self.product = str(getattr(config, "TWSCRAPE_SEARCH_PRODUCT", "Latest"))
        self.timeout = getattr(config, "TWSCRAPE_TIMEOUT_SECONDS", 120)
        self.lookback_hours = getattr(config, "TWSCRAPE_LOOKBACK_HOURS", 168)
        self.account_wait_seconds = getattr(
            config, "TWSCRAPE_ACCOUNT_WAIT_SECONDS", 10
        )
        self.expected_version = str(
            getattr(config, "TWSCRAPE_EXPECTED_VERSION", "0.20.1")
        )
        self.db_file = _resolve_project_path(config.TWSCRAPE_DB_FILE)
        self.state_file = _resolve_project_path(config.TWSCRAPE_STATE_FILE)
        self.seen_id_limit = getattr(config, "TWSCRAPE_SEEN_ID_LIMIT", 5000)
        self.target_data_dir = _resolve_project_path(config.DATA_DIR)
        self._api_factory = api_factory
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._pending_ids: Tuple[str, ...] = ()

    def validate(self) -> List[str]:
        """返回阻止 twscrape 搜索启动的配置或会话错误。"""
        errors: List[str] = []

        if self.configured_platform not in SUPPORTED_PLATFORMS:
            errors.append(
                f"CRAWL_PLATFORM={self.configured_platform!r} 不能由 twscrape 处理；"
                "请使用 x"
            )
        if not self.keywords or any(not keyword for keyword in self.keywords):
            errors.append("SEARCH_KEYWORDS 必须至少包含一个非空关键词")
        if sys.version_info < MINIMUM_PYTHON:
            errors.append(
                "twscrape 0.20.1 需要 Python >=3.10；"
                f"当前为 {sys.version_info.major}.{sys.version_info.minor}"
            )
        if not isinstance(self.per_query_limit, int) or self.per_query_limit <= 0:
            errors.append("TWSCRAPE_RESULTS_PER_QUERY 必须是正整数")
        if self.product not in SUPPORTED_PRODUCTS:
            errors.append(
                f"TWSCRAPE_SEARCH_PRODUCT={self.product!r} 不受支持；"
                "可选 Latest、Top、Media"
            )
        if not isinstance(self.timeout, (int, float)) or self.timeout <= 0:
            errors.append("TWSCRAPE_TIMEOUT_SECONDS 必须大于 0")
        if (
            not isinstance(self.lookback_hours, (int, float))
            or self.lookback_hours <= 0
        ):
            errors.append("TWSCRAPE_LOOKBACK_HOURS 必须大于 0")
        if (
            not isinstance(self.account_wait_seconds, (int, float))
            or self.account_wait_seconds <= 0
        ):
            errors.append("TWSCRAPE_ACCOUNT_WAIT_SECONDS 必须大于 0")
        if not isinstance(self.seen_id_limit, int) or self.seen_id_limit <= 0:
            errors.append("TWSCRAPE_SEEN_ID_LIMIT 必须是正整数")

        dependency_error = self._dependency_error()
        if dependency_error:
            errors.append(dependency_error)

        session_error = self._session_error()
        if session_error:
            errors.append(session_error)

        state_error = self._state_error()
        if state_error:
            errors.append(state_error)
        return errors

    def run(self) -> CrawlRunResult:
        """执行搜索；配置、网络、账号或无数据都会返回失败结果。"""
        errors = self.validate()
        if errors:
            return CrawlRunResult(success=False, error="；".join(errors))

        run_id = f"{datetime.now():%Y%m%dT%H%M%S%f}-{uuid4().hex[:8]}"
        output_dir = self.target_data_dir / "crawler_runs" / run_id
        content_dir = output_dir / self.platform / "jsonl"
        output_file = content_dir / f"search_contents_{datetime.now():%Y-%m-%d}.jsonl"
        self._pending_ids = ()

        print(f"twscrape 搜索模式：{self.product}")
        print(f"本次输出：{output_dir}")
        print(f"搜索关键词：{', '.join(self.keywords)}")

        try:
            rows = asyncio.run(
                asyncio.wait_for(self._collect(), timeout=float(self.timeout))
            )
        except asyncio.TimeoutError:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"twscrape 执行超时（{self.timeout} 秒）",
            )
        except (ImportError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"twscrape 执行失败：{type(exc).__name__}: {exc}",
            )
        except Exception as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"twscrape 搜索失败：{type(exc).__name__}: {exc}",
            )

        if not rows:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error="twscrape 正常完成，但本次没有新的 X 内容",
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
                error=f"twscrape 数据写入失败：{exc}",
            )

        self._pending_ids = tuple(str(row["id"]) for row in rows)
        return CrawlRunResult(
            success=True,
            data_files=(output_file,),
            output_dir=output_dir,
            returncode=0,
        )

    def acknowledge(self) -> str:
        """完整工作流成功后再记录已处理 ID，避免推送失败时丢数据。"""
        if not self._pending_ids:
            return ""
        try:
            existing = self._load_seen_ids()
            ordered_ids = list(existing)
            ordered_ids.extend(
                tweet_id for tweet_id in self._pending_ids if tweet_id not in existing
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
            return f"无法保存 twscrape 已处理状态：{exc}"
        self._pending_ids = ()
        return ""

    def fetch_replies(
        self,
        items: Sequence[Dict[str, object]],
        limit: int,
        timeout_seconds: float,
    ) -> Dict[str, Dict[str, object]]:
        """按推文 ID 获取有限数量的直接回复。"""
        requested: List[Tuple[str, str, str]] = []
        results: Dict[str, Dict[str, object]] = {}
        for item in items:
            item_id = str(item.get("id", "") or "").strip()
            tweet_id = str(item.get("platform_item_id", "") or "").strip()
            username = str(item.get("username", "") or "").strip()
            if not item_id:
                continue
            if not tweet_id.isdigit():
                results[item_id] = {
                    "available": False,
                    "comments": [],
                    "error": "无效的 X 推文 ID",
                }
                continue
            requested.append((item_id, tweet_id, username))

        if not requested:
            return results

        try:
            api = self._make_api()
            fetched = asyncio.run(
                self._fetch_replies_for_items(
                    api,
                    requested,
                    limit=max(1, int(limit)),
                    timeout_seconds=float(timeout_seconds),
                )
            )
        except Exception as exc:
            error = f"twscrape 回复获取失败：{type(exc).__name__}"
            for item_id, _tweet_id, _username in requested:
                results[item_id] = {
                    "available": False,
                    "comments": [],
                    "error": error,
                }
            return results

        results.update(fetched)
        return results

    async def _fetch_replies_for_items(
        self,
        api: object,
        requested: Sequence[Tuple[str, str, str]],
        limit: int,
        timeout_seconds: float,
    ) -> Dict[str, Dict[str, object]]:
        results: Dict[str, Dict[str, object]] = {}
        for item_id, tweet_id, original_username in requested:
            try:
                comments = await asyncio.wait_for(
                    self._fetch_one_tweet_replies(
                        api,
                        tweet_id,
                        original_username,
                        limit,
                    ),
                    timeout=timeout_seconds,
                )
            except Exception as exc:
                results[item_id] = {
                    "available": False,
                    "comments": [],
                    "error": (
                        f"twscrape 回复获取失败：{type(exc).__name__}"
                    ),
                }
                continue
            results[item_id] = {
                "available": True,
                "comments": comments,
                "error": "",
            }
        return results

    async def _fetch_one_tweet_replies(
        self,
        api: object,
        tweet_id: str,
        original_username: str,
        limit: int,
    ) -> List[Dict[str, object]]:
        comments: List[Dict[str, object]] = []
        tweet_replies = getattr(api, "tweet_replies")
        async for reply in tweet_replies(int(tweet_id), limit=limit):
            content = str(_safe_attr(reply, "rawContent", "") or "").strip()
            if not content:
                continue

            in_reply_to = str(
                _safe_attr(reply, "inReplyToTweetIdStr", None)
                or _safe_attr(reply, "inReplyToTweetId", "")
                or ""
            ).strip()
            if in_reply_to and in_reply_to != tweet_id:
                continue

            user = _safe_attr(reply, "user")
            username = str(_safe_attr(user, "username", "") or "").strip()
            published_at = _safe_attr(reply, "date")
            if isinstance(published_at, datetime):
                if published_at.tzinfo is None:
                    published_at = published_at.replace(tzinfo=timezone.utc)
                published_value = published_at.isoformat()
            else:
                published_value = ""

            comments.append(
                {
                    "id": str(
                        _safe_attr(reply, "id_str", None)
                        or _safe_attr(reply, "id", "")
                        or ""
                    ).strip(),
                    "content": content,
                    "author_username": username,
                    "like_count": _safe_nonnegative_int(
                        _safe_attr(reply, "likeCount", 0)
                    ),
                    "published_at": published_value,
                    "is_original_author": bool(
                        username
                        and original_username
                        and username.casefold() == original_username.casefold()
                    ),
                }
            )

        comments.sort(
            key=lambda comment: int(comment.get("like_count", 0) or 0),
            reverse=True,
        )
        return comments[:limit]

    async def _collect(self) -> List[Dict[str, object]]:
        api = self._make_api()
        seen_ids = set(self._load_seen_ids())
        collected: Dict[str, Tuple[float, Dict[str, object]]] = {}
        cutoff = self._now_provider() - timedelta(hours=float(self.lookback_hours))

        for keyword in self.keywords:
            async for tweet in self._search_keyword(api, keyword):
                row = self._tweet_to_row(tweet, keyword, cutoff)
                if row is None:
                    continue
                tweet_id = str(row["id"])
                if tweet_id in seen_ids:
                    continue
                sort_time = float(row.pop("_sort_time"))
                if tweet_id in collected:
                    matched_keywords = collected[tweet_id][1]["matched_keywords"]
                    if keyword not in matched_keywords:
                        matched_keywords.append(keyword)
                    continue
                collected[tweet_id] = (sort_time, row)

        ordered = sorted(collected.values(), key=lambda item: item[0], reverse=True)
        return [row for _, row in ordered]

    async def _search_keyword(
        self, api: object, keyword: str
    ) -> AsyncIterator[object]:
        search = getattr(api, "search")
        async for tweet in search(
            keyword,
            limit=self.per_query_limit,
            kv={"product": self.product},
        ):
            yield tweet

    def _tweet_to_row(
        self, tweet: object, keyword: str, cutoff: datetime
    ) -> Optional[Dict[str, object]]:
        tweet_id = str(
            _safe_attr(tweet, "id_str", None) or _safe_attr(tweet, "id", "")
        ).strip()
        content = str(_safe_attr(tweet, "rawContent", "") or "").strip()
        if not tweet_id or not content:
            return None

        user = _safe_attr(tweet, "user")
        username = str(_safe_attr(user, "username", "") or "").strip()
        display_name = str(_safe_attr(user, "displayname", "") or "").strip()
        author = display_name or (f"@{username}" if username else "")

        published_at = _safe_attr(tweet, "date")
        if not isinstance(published_at, datetime):
            return None
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=timezone.utc)
        if published_at < cutoff:
            return None

        title_line = next(
            (line.strip() for line in content.splitlines() if line.strip()),
            "X 帖子",
        )
        url = str(_safe_attr(tweet, "url", "") or "").strip()
        if not url:
            url_user = username or "i/web"
            url = f"https://x.com/{url_user}/status/{tweet_id}"

        quoted_tweet = _safe_attr(tweet, "quotedTweet")
        quoted_content = str(
            _safe_attr(quoted_tweet, "rawContent", "") or ""
        ).strip()
        in_reply_to = str(
            _safe_attr(tweet, "inReplyToTweetIdStr", None)
            or _safe_attr(tweet, "inReplyToTweetId", "")
            or ""
        ).strip()
        conversation_id = str(
            _safe_attr(tweet, "conversationIdStr", None)
            or _safe_attr(tweet, "conversationId", "")
            or tweet_id
        ).strip()
        hashtags = [
            str(tag).strip()
            for tag in (_safe_attr(tweet, "hashtags", []) or [])
            if str(tag).strip()
        ]
        is_retweet = _safe_attr(tweet, "retweetedTweet") is not None
        is_quote = bool(
            quoted_tweet is not None
            or _safe_attr(tweet, "isQuoteStatus", False)
        )

        return {
            "id": tweet_id,
            "title": title_line[:80],
            "content": content,
            "quoted_content": quoted_content,
            "source": "X (Twitter)",
            "url": url,
            "source_url": url,
            "referenced_urls": _extract_links(tweet),
            "author": author,
            "username": username,
            "publish_time": published_at.isoformat(),
            "lang": str(_safe_attr(tweet, "lang", "") or ""),
            "like_count": _safe_attr(tweet, "likeCount", 0) or 0,
            "comment_count": _safe_attr(tweet, "replyCount", 0) or 0,
            "share_count": _safe_attr(tweet, "retweetCount", 0) or 0,
            "collect_count": _safe_attr(tweet, "bookmarkedCount", 0) or 0,
            "quote_count": _safe_attr(tweet, "quoteCount", 0) or 0,
            "view_count": _safe_attr(tweet, "viewCount", 0) or 0,
            "search_keyword": keyword,
            "matched_keywords": [keyword],
            "platform_metadata": {
                "lang": str(_safe_attr(tweet, "lang", "") or ""),
                "hashtags": hashtags,
                "is_reply": bool(in_reply_to),
                "in_reply_to_id": in_reply_to,
                "is_retweet": is_retweet,
                "is_quote": is_quote,
                "possibly_sensitive": bool(
                    _safe_attr(tweet, "possibly_sensitive", False)
                ),
                "conversation_id": conversation_id,
            },
            "_sort_time": published_at.timestamp(),
        }

    def _make_api(self) -> object:
        kwargs = {
            "pool": str(self.db_file),
            "raise_when_no_account": True,
            "wait_timeout": float(self.account_wait_seconds),
            "wait_interval": 1.0,
        }
        if self._api_factory is not None:
            return self._api_factory(**kwargs)
        from twscrape import API

        return API(**kwargs)

    def _dependency_error(self) -> str:
        if importlib.util.find_spec("twscrape") is None:
            return (
                "缺少 twscrape；请运行 "
                "python3 -m pip install -r requirements.txt"
            )
        try:
            actual_version = metadata.version("twscrape")
        except metadata.PackageNotFoundError:
            return "无法读取 twscrape 安装版本"
        if self.expected_version and actual_version != self.expected_version:
            return (
                "twscrape 版本不匹配："
                f"期望 {self.expected_version}，实际 {actual_version}"
            )
        return ""

    def _session_error(self) -> str:
        if not self.db_file.is_file():
            return (
                f"twscrape 会话数据库不存在：{self.db_file}；"
                "请先运行 python3 scripts/setup_twscrape_session.py"
            )
        try:
            file_mode = self.db_file.stat().st_mode
        except OSError as exc:
            return f"twscrape 会话数据库无法读取：{exc}"
        if os.name != "nt" and file_mode & 0o077:
            return (
                f"twscrape 会话数据库权限过宽：{self.db_file}；"
                "请设置为仅当前用户可读写（chmod 600）"
            )
        try:
            uri = self.db_file.as_uri() + "?mode=ro"
            with sqlite3.connect(uri, uri=True) as connection:
                rows = connection.execute(
                    "SELECT username, cookies FROM accounts WHERE active = 1"
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            return f"twscrape 会话数据库无法读取：{exc}"
        if not rows:
            return "twscrape 会话数据库中没有可用账号"

        for _username, raw_cookies in rows:
            try:
                cookies = json.loads(raw_cookies)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(cookies, dict) and all(
                str(cookies.get(key, "")).strip() for key in REQUIRED_COOKIE_KEYS
            ):
                return ""
        return "twscrape 可用账号缺少 auth_token 或 ct0 Cookie"

    def _state_error(self) -> str:
        if not self.state_file.exists():
            return ""
        try:
            self._load_seen_ids()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return f"twscrape 状态文件无效：{exc}"
        return ""

    def _load_seen_ids(self) -> List[str]:
        if not self.state_file.exists():
            return []
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("不支持的状态文件格式")
        seen_ids = data.get("seen_ids")
        if not isinstance(seen_ids, list) or any(
            not isinstance(tweet_id, str) for tweet_id in seen_ids
        ):
            raise ValueError("seen_ids 必须是字符串列表")
        return seen_ids
