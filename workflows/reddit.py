"""仅供 Reddit 使用的结构化社交信息流工作流。"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import urlparse

import config
from analyzer.reddit_embedding import (
    RedditEmbeddingError,
    validate_reddit_embedding_config,
)
from analyzer.reddit_enricher import (
    RedditEnrichmentConfigError,
    enrich_reddit_items,
    validate_reddit_enrichment_config,
)
from analyzer.reddit_pipeline import (
    run_reddit_filters,
    validate_reddit_pipeline_config,
)
from analyzer.reddit_quality import validate_reddit_quality_config
from analyzer.reddit_rules import validate_reddit_rule_config
from crawler.base import CollectorBridge, CrawlRunResult
from notifier.reddit_wecom import (
    send_reddit_wecom,
    validate_reddit_wecom_config,
)
from output.reddit_feed import (
    RedditFeedRenderError,
    render_reddit_feed,
)
from utils.parser import load_articles
from utils.reddit_result_store import (
    RedditResultStoreError,
    append_reddit_results,
    build_reddit_result,
)
from workflows.outcome import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    PlatformOutcome,
)


EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CONFIG = 2
EXIT_CRAWLER = 3
EXIT_NO_DATA = 4
EXIT_NOTIFY = 5
EXIT_STATE = 6
EXIT_EMBEDDING = 7
EXIT_STORE = 8
EXIT_RENDER = 9


def _http_url(value: object) -> bool:
    parsed = urlparse(str(value or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_reddit_runtime_config(
    bridge: CollectorBridge,
) -> List[str]:
    """只校验 Reddit 专用流程需要的配置。"""
    errors: List[str] = []
    modules = ["openai", "requests"]
    if str(config.EMBEDDING_PROVIDER).strip().casefold() == "dashscope":
        modules.append("dashscope")
    for module_name in modules:
        if importlib.util.find_spec(module_name) is None:
            errors.append(
                f"缺少项目依赖 {module_name}；请先安装 requirements.txt"
            )

    api_key = str(config.API_KEY or "").strip()
    if not api_key or api_key.casefold() in {
        "your-api-key-here",
        "sk-xxxxx",
        "your-key",
    }:
        errors.append("API_KEY 未配置或仍是占位值")
    if not _http_url(config.BASE_URL):
        errors.append("BASE_URL 必须是有效的 http/https URL")
    if not str(config.MODEL_NAME or "").strip():
        errors.append("MODEL_NAME 不能为空")

    try:
        validate_reddit_rule_config()
        validate_reddit_embedding_config()
        validate_reddit_quality_config()
        validate_reddit_pipeline_config()
        validate_reddit_enrichment_config()
    except (RedditEmbeddingError, RedditEnrichmentConfigError, ValueError) as exc:
        errors.append(str(exc))

    for name in (
        "REDDIT_FEED_RETENTION_DAYS",
        "REDDIT_FEED_MAX_ITEMS",
    ):
        value = getattr(config, name, 0)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            errors.append(f"{name} 必须是正整数")
    errors.extend(validate_reddit_wecom_config())
    errors.extend(bridge.validate())
    return errors


def _print_filter_results(filtered: Dict[str, List[Dict]]) -> None:
    for index, item in enumerate(filtered["passed"], start=1):
        record = build_reddit_result(item)
        print(
            f"   ✅ 通过 [{index}/{len(filtered['passed'])}] "
            f"{str(item.get('title', '无标题'))[:25]} "
            f"→ Reddit 质量分 {record['score']:.2f}"
        )
    for index, item in enumerate(filtered["dropped"], start=1):
        metadata = item.get("reddit_filter_metadata", {})
        reasons = (
            metadata.get("final_reason_codes", [])
            if isinstance(metadata, dict)
            else []
        )
        print(
            f"   ⏭️ 淘汰 [{index}/{len(filtered['dropped'])}] "
            f"{str(item.get('title', '无标题'))[:25]} "
            f"→ {', '.join(reasons) or 'REDDIT_FILTER_DROPPED'}"
        )


def build_reddit_digest(
    bridge: CollectorBridge,
    embedding_client: Optional[object] = None,
    enrichment_client: Optional[object] = None,
) -> PlatformOutcome:
    """采集、筛选、摘要、存储并渲染 Reddit，不做推送与状态确认。"""
    print("\n[Reddit 1/7] 启动数据采集")
    crawl_result: CrawlRunResult = bridge.run()
    if not crawl_result.success:
        print(f"Reddit 采集失败：{crawl_result.error}")
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_CRAWLER, error=crawl_result.error
        )

    print("\n[Reddit 2/7] 加载本次运行数据")
    try:
        items = load_articles(
            crawl_result.data_files,
            platform="reddit",
            allow_manual_fallback=False,
        )
    except ValueError as exc:
        print(f"Reddit 数据加载失败：{exc}")
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_UNEXPECTED, error=str(exc)
        )
    if not items:
        print("本次运行没有可处理的 Reddit 内容")
        return PlatformOutcome("reddit", STATUS_EMPTY, EXIT_NO_DATA)

    print("\n[Reddit 3/7] 执行规则、Embedding、质量评分和排序")
    try:
        filtered = run_reddit_filters(
            items,
            embedding_client=embedding_client,
        )
    except RedditEmbeddingError as exc:
        print(f"Reddit Embedding 筛选失败：{exc}")
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_EMBEDDING, error=str(exc)
        )
    except ValueError as exc:
        print(f"Reddit 筛选配置无效：{exc}")
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_CONFIG, error=str(exc)
        )
    _print_filter_results(filtered)
    print(
        f"Reddit 筛选完成：保留 {len(filtered['passed'])}，"
        f"删除 {len(filtered['dropped'])}"
    )

    print("\n[Reddit 4/7] 生成极简标题和摘要")
    try:
        kept_items = enrich_reddit_items(
            filtered["passed"],
            client=enrichment_client,
        )
    except RedditEnrichmentConfigError as exc:
        print(f"Reddit 摘要配置无效：{exc}")
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_CONFIG, error=str(exc)
        )

    processed_at = datetime.now(timezone.utc)
    try:
        records = [
            build_reddit_result(item, processed_at=processed_at)
            for item in filtered["all_items"]
        ]
    except RedditResultStoreError as exc:
        print(str(exc))
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_STORE, error=str(exc)
        )
    kept_ids = {
        f"reddit:{str(item.get('raw', {}).get('id', '')).strip()}"
        for item in kept_items
        if isinstance(item.get("raw"), dict)
    }
    kept_records = [
        record for record in records if record["id"] in kept_ids
    ]

    print("\n[Reddit 5/7] 写入结构化结果")
    try:
        result_path = append_reddit_results(records)
    except RedditResultStoreError as exc:
        print(str(exc))
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_STORE, error=str(exc)
        )
    print(f"Reddit 结构化结果已写入 {result_path}")

    print("\n[Reddit 6/7] 更新聚合页面")
    try:
        report_path = render_reddit_feed()
    except (RedditFeedRenderError, RedditResultStoreError) as exc:
        print(str(exc))
        return PlatformOutcome(
            "reddit", STATUS_FAILED, EXIT_RENDER, error=str(exc)
        )
    print(f"Reddit 聚合页面已更新 {report_path}")

    return PlatformOutcome(
        platform="reddit",
        status=STATUS_OK,
        crawled=len(items),
        passed=len(kept_records),
        items=kept_records,
        result_path=result_path,
        report_path=report_path,
        meta={"dropped": len(filtered["dropped"])},
    )


def run_reddit_workflow(
    bridge: CollectorBridge,
    embedding_client: Optional[object] = None,
    enrichment_client: Optional[object] = None,
) -> int:
    """运行 Reddit 专用流程，不调用逐条研报或通用通知模块。"""
    outcome = build_reddit_digest(
        bridge,
        embedding_client=embedding_client,
        enrichment_client=enrichment_client,
    )
    if not outcome.ok:
        return outcome.exit_code

    kept_records = outcome.items

    state_error = bridge.acknowledge()
    if state_error:
        print(f"Reddit 采集状态保存失败：{state_error}")
        return EXIT_STATE

    print("\n[Reddit 7/7] 可选企业微信通知")
    if config.REDDIT_ENABLE_WECOM and not send_reddit_wecom(kept_records):
        print("Reddit 通知失败，结构化结果和采集状态已保留")
        return EXIT_NOTIFY
    if not config.REDDIT_ENABLE_WECOM:
        print("Reddit 企业微信通知未启用")

    print(
        f"Reddit 工作流完成：候选 {outcome.crawled}，"
        f"保留 {len(kept_records)}，删除 {outcome.meta.get('dropped', 0)}"
    )
    return EXIT_OK
