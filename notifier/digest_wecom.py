"""跨平台混合日报的企业微信推送。

固定使用 ``markdown_v2``（与 Twitter/Reddit 通知一致），按完整条目边界
装入 4096 字节预算，超出时拆成多条消息。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import urlparse

import config
from pipeline.ranking import platform_counts
from pipeline.stages import PLATFORM_LABELS, platform_label
from pipeline.types import DigestItem

SEND_GAP_SECONDS = 4.0
DEFAULT_MAX_BYTES = 4096


def _http_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _plain(value: object, limit: Optional[int] = None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = (
        text.replace("\\", "")
        .replace("*", "")
        .replace("[", "【")
        .replace("]", "】")
        .replace("<", "＜")
        .replace(">", "＞")
        .replace("`", "'")
    )
    if limit and len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def _safe_url(url: str) -> str:
    return url.replace("(", "%28").replace(")", "%29")


def _links(item: DigestItem) -> str:
    parts: List[str] = []
    if _http_url(item.report_url):
        parts.append(f"[在线阅读研报]({_safe_url(str(item.report_url))})")
    if _http_url(item.url):
        parts.append(f"[原文]({_safe_url(item.url)})")
    return " ｜ ".join(parts)


def _block(item: DigestItem, index: int, abstract_limit: int = 120) -> str:
    header = (
        f"**{index}. {platform_label(item.platform)} · "
        f"{item.display_score:.1f}/10**"
    )
    lines = [header, _plain(item.title, limit=60)]
    abstract = _plain(item.abstract, limit=abstract_limit)
    if abstract:
        lines.append(abstract)
    links = _links(item)
    if links:
        lines.append(links)
    return "\n".join(lines)


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8"))


def build_messages(
    items: List[DigestItem],
    digest_date: Optional[str] = None,
    max_bytes: Optional[int] = None,
) -> List[str]:
    """把日报切成若干条不超过字节上限的消息。"""
    date = digest_date or datetime.now().strftime("%Y-%m-%d")
    limit = int(
        max_bytes or getattr(config, "MULTI_WECOM_MAX_BYTES", DEFAULT_MAX_BYTES)
    )
    counts = platform_counts(items)
    summary = " · ".join(
        f"{platform_label(platform)} {counts[platform]}"
        for platform in PLATFORM_LABELS
        if counts.get(platform)
    )
    header = f"# AI 情报日报 · {date}\n\n共 {len(items)} 条 ｜ {summary}"
    continuation = f"# AI 情报日报 · {date}（续）"

    messages: List[str] = []
    current = header
    has_item = False

    for index, item in enumerate(items, start=1):
        block = _block(item, index)
        candidate = f"{current}\n\n{block}"
        if _byte_len(candidate) <= limit:
            current = candidate
            has_item = True
            continue

        if not has_item:
            # 当前消息还空着：压缩摘要再试，仍不行就丢弃这一条
            compact = _block(item, index, abstract_limit=40)
            candidate = f"{current}\n\n{compact}"
            if _byte_len(candidate) <= limit:
                current = candidate
                has_item = True
            continue

        # 当前消息已满：开新消息
        messages.append(current)
        current = f"{continuation}\n\n{block}"
        has_item = _byte_len(current) <= limit
        if not has_item:
            compact = _block(item, index, abstract_limit=40)
            candidate = f"{continuation}\n\n{compact}"
            if _byte_len(candidate) <= limit:
                current = candidate
                has_item = True

    if has_item or not messages:
        messages.append(current)
    return messages


def validate_digest_wecom_config() -> List[str]:
    webhook = str(getattr(config, "WECOM_WEBHOOK", "") or "").strip()
    placeholders = ("your-webhook-key", "your-key", "example.com")
    if not _http_url(webhook) or any(
        value in webhook.casefold() for value in placeholders
    ):
        return ["多平台日报推送需要有效的 WECOM_WEBHOOK"]
    return []


def send_digest(
    items: List[DigestItem],
    digest_date: Optional[str] = None,
    session: Optional[object] = None,
) -> bool:
    """发送混合日报；任一条失败即返回 False。"""
    if not items:
        print("   ℹ️ 没有入选内容，跳过企业微信推送")
        return True
    errors = validate_digest_wecom_config()
    if errors:
        for error in errors:
            print(f"   ❌ {error}")
        return False

    import requests

    active_session = session or requests
    messages = build_messages(items, digest_date=digest_date)
    for index, content in enumerate(messages, start=1):
        try:
            response = active_session.post(
                config.WECOM_WEBHOOK,
                json={
                    "msgtype": "markdown_v2",
                    "markdown_v2": {"content": content},
                },
                timeout=15,
            )
            if getattr(response, "status_code", 0) != 200:
                print(f"   ❌ 第 {index} 条日报发送失败：HTTP {response.status_code}")
                return False
            data = response.json()
            if not isinstance(data, dict) or data.get("errcode") != 0:
                print(f"   ❌ 第 {index} 条日报返回异常：{data}")
                return False
        except Exception as exc:  # noqa: BLE001
            print(f"   ❌ 第 {index} 条日报发送异常：{exc}")
            return False
        print(f"   ✅ 已发送第 {index}/{len(messages)} 条日报")
        if index < len(messages):
            time.sleep(SEND_GAP_SECONDS)
    return True
