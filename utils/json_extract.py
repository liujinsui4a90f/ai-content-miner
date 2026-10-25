"""从模型文本回复中提取 JSON 对象。

模型即使被要求「只返回 JSON」，仍可能输出 Markdown 代码块、前置说明，或把
两个对象拼在一起。这里只解析**第一个完整对象**，避免把后续内容当成 JSON
的延续（那会触发 ``Extra data``）。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict

_FENCE_PATTERN = re.compile(r"^\s*```(?:json|JSON)?\s*|\s*```\s*$")


def strip_code_fence(text: str) -> str:
    """去掉包裹整个回复的 Markdown 代码块标记。"""
    return _FENCE_PATTERN.sub("", str(text or "").strip())


def extract_json_object(raw: str) -> Dict[str, Any]:
    """返回文本中的第一个完整 JSON 对象。

    找不到对象或不完整时抛出 ``ValueError``，由调用方决定是否重试。
    """
    text = strip_code_fence(raw)
    start = text.find("{")
    if start < 0:
        raise ValueError("未找到 JSON")

    try:
        value, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON 不完整：{exc.msg}（位置 {exc.pos}）") from exc

    if not isinstance(value, dict):
        raise ValueError("JSON 顶层不是对象")
    return value
