"""候选截取、LLM 复评与最终选取。

跨平台分数不可比（X 的分数是批内分位加权、Reddit/小红书是 0–10 质量分），
因此这里分两步：
  1. 每个平台按自己的分数取 top-k，只作为**候选上限**；
  2. 用 ``analyzer.scorer`` 的统一评分口径对候选重新打分，再跨平台排序。
"""

from __future__ import annotations

import math
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, List, Optional

import config
from pipeline.types import DigestItem


def take_top_k(
    grouped: Dict[str, List[DigestItem]],
    quota: Optional[Dict[str, int]] = None,
) -> List[DigestItem]:
    """每个平台按平台内分数取前 k 条，并重写平台内名次。"""
    limits = quota or getattr(config, "MULTI_PLATFORM_QUOTA", {})
    picked: List[DigestItem] = []
    for platform, items in grouped.items():
        limit = int(limits.get(platform, 0))
        if limit <= 0:
            continue
        ordered = sorted(items, key=lambda item: (-item.score, item.rank_in_platform))
        for index, item in enumerate(ordered[:limit], start=1):
            item.rank_in_platform = index
            picked.append(item)
    return picked


def _judge_article(item: DigestItem) -> Dict:
    """把候选项还原成 scorer 需要的 article 结构。"""
    if item.platform in {"xhs", "zhihu", "github"}:
        article = item.source_item.get("article")
        if isinstance(article, dict) and article.get("content"):
            return article
    return {
        "title": item.title,
        "content": item.content,
        "source": item.platform,
        "url": item.url,
    }


def judge_items(
    items: Iterable[DigestItem],
    enabled: Optional[bool] = None,
    concurrency: Optional[int] = None,
    scorer: Optional[object] = None,
) -> int:
    """用现有 LLM 评分机制复评候选；返回成功复评的条数。

    任一条失败都只影响它自己：``judge_score`` 保持 ``None``，
    最终排序时排到已复评内容之后，而不是让整次运行失败。
    """
    active = (
        bool(getattr(config, "MULTI_LLM_RESCORE_ENABLED", True))
        if enabled is None
        else enabled
    )
    candidates = list(items)
    if not active or not candidates:
        return 0

    score_and_classify = scorer
    if score_and_classify is None:
        try:
            from analyzer.scorer import score_and_classify as _scorer

            score_and_classify = _scorer
        except Exception as exc:  # noqa: BLE001 - 缺少 API key 等
            print(f"   ⚠️ LLM 复评不可用，回退到平台内排名：{exc}")
            return 0

    def work(item: DigestItem) -> Dict:
        try:
            return score_and_classify(_judge_article(item))
        except Exception as exc:  # noqa: BLE001
            return {"total_score": 0.0, "summary": f"评分失败：{exc}"}

    workers = max(1, int(concurrency or getattr(config, "MULTI_LLM_CONCURRENCY", 4)))
    print(f"   🤖 LLM 复评 {len(candidates)} 条候选（并发 {workers}）")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, candidates))

    judged = 0
    for item, result in zip(candidates, results):
        total = float(result.get("total_score") or 0.0)
        if not math.isfinite(total) or total <= 0:
            continue
        item.judge_score = total
        item.judge_summary = str(result.get("summary") or "")
        item.judge_category = str(result.get("category") or "")
        item.judge_dimensions = [str(value) for value in result.get("dimensions") or []]
        item.judge_scores = [float(value) for value in result.get("scores") or []]
        item.judge_reasons = [str(value) for value in result.get("reasons") or []]
        judged += 1
    print(f"   ✅ 复评成功 {judged}/{len(candidates)} 条")
    return judged


def interleave(items: Iterable[DigestItem]) -> List[DigestItem]:
    """按平台轮转交错，作为 LLM 不可用时的确定性回退。"""
    buckets: Dict[str, List[DigestItem]] = defaultdict(list)
    for item in items:
        buckets[item.platform].append(item)
    for bucket in buckets.values():
        bucket.sort(key=lambda item: item.rank_in_platform)

    ordered: List[DigestItem] = []
    round_index = 0
    while any(len(bucket) > round_index for bucket in buckets.values()):
        for platform in sorted(buckets):
            bucket = buckets[platform]
            if round_index < len(bucket):
                ordered.append(bucket[round_index])
        round_index += 1
    return ordered


def select_final(
    items: Iterable[DigestItem],
    total: Optional[int] = None,
) -> List[DigestItem]:
    """按复评分排序取最终推送集；未复评的排在后面。"""
    limit = int(
        total if total is not None else getattr(config, "MULTI_TOTAL_ITEMS", 12)
    )
    candidates = list(items)
    judged = [item for item in candidates if item.judge_score is not None]
    unjudged = [item for item in candidates if item.judge_score is None]

    judged.sort(
        key=lambda item: (-(item.judge_score or 0.0), item.rank_in_platform)
    )
    if unjudged:
        unjudged = interleave(unjudged)

    ordered = judged + unjudged
    return ordered[:limit] if limit > 0 else ordered


def platform_counts(items: Iterable[DigestItem]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for item in items:
        counts[item.platform] = counts.get(item.platform, 0) + 1
    return counts
