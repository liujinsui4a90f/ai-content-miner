"""仅供 Twitter 使用的结构化信息流工作流。"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from typing import Dict, List, Optional, Sequence
from urllib.parse import urlparse

import config
from analyzer.twitter_comments import (
    TwitterCommentConfigError,
    twitter_comment_settings,
)
from analyzer.twitter_daily_selector import (
    TwitterDailySelectionConfigError,
    select_twitter_daily_items,
    validate_twitter_daily_config,
)
from analyzer.twitter_embedding import (
    TwitterEmbeddingError,
    probe_twitter_embedding_service,
    validate_twitter_embedding_config,
)
from analyzer.twitter_enricher import (
    TwitterEnrichmentConfigError,
    enrich_twitter_items,
    validate_twitter_enrichment_config,
)
from analyzer.twitter_pipeline import run_twitter_preselection_filters
from analyzer.twitter_rules import validate_twitter_rule_config
from crawler.base import CollectorBridge, CrawlRunResult
from notifier.twitter_wecom import (
    send_twitter_wecom,
    validate_twitter_wecom_config,
)
from output.twitter_feed import (
    TwitterFeedRenderError,
    render_twitter_feed,
)
from utils.twitter_parser import load_twitter_items
from utils.twitter_result_store import (
    TwitterResultStoreError,
    append_twitter_results,
    load_recent_twitter_digest_items,
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


def validate_twitter_runtime_config(
    bridge: CollectorBridge,
) -> List[str]:
    """只校验 Twitter 新流程需要的配置。"""
    errors: List[str] = []
    modules = ["openai"]
    if config.TWITTER_ENABLE_WECOM:
        modules.append("requests")
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
        validate_twitter_rule_config()
    except ValueError as exc:
        errors.append(str(exc))
    # Embedding 关闭时不要求模型和批次配置可用，避免可选能力阻塞主流程。
    if getattr(config, "TWITTER_EMBEDDING_ENABLED", True):
        try:
            validate_twitter_embedding_config()
        except TwitterEmbeddingError as exc:
            errors.append(str(exc))
    try:
        twitter_comment_settings()
    except TwitterCommentConfigError as exc:
        errors.append(str(exc))
    try:
        validate_twitter_daily_config()
    except TwitterDailySelectionConfigError as exc:
        errors.append(str(exc))
    try:
        validate_twitter_enrichment_config()
    except TwitterEnrichmentConfigError as exc:
        errors.append(str(exc))
    if (
        not isinstance(config.TWITTER_FEED_RETENTION_DAYS, int)
        or config.TWITTER_FEED_RETENTION_DAYS <= 0
    ):
        errors.append("TWITTER_FEED_RETENTION_DAYS 必须是正整数")
    if (
        not isinstance(config.TWITTER_FEED_MAX_ITEMS, int)
        or config.TWITTER_FEED_MAX_ITEMS <= 0
    ):
        errors.append("TWITTER_FEED_MAX_ITEMS 必须是正整数")
    errors.extend(validate_twitter_wecom_config())
    errors.extend(bridge.validate())
    return errors


def _finalize(
    all_items: Sequence[Dict],
    selected_items: Sequence[Dict],
) -> None:
    selected_objects = {id(item) for item in selected_items}
    processed_at = datetime.now(timezone.utc)
    for item in all_items:
        metadata = item["filter_metadata"]
        publication = item.get("publication_metadata", {})
        if id(item) in selected_objects:
            metadata["final_decision"] = "keep"
            metadata["final_reason_codes"] = [
                "TWITTER_DAILY_SELECTED"
            ]
        elif metadata.get("final_decision") == "drop":
            pass
        elif (
            isinstance(publication, dict)
            and publication.get("decision")
            in {"archive", "cluster_duplicate"}
        ):
            metadata["final_decision"] = "archive"
            metadata["final_reason_codes"] = list(
                publication.get("reason_codes", [])
            )
        elif metadata.get("final_decision") == "pending":
            metadata["final_decision"] = "drop"
            metadata["final_reason_codes"] = [
                "TWITTER_FILTER_PIPELINE_INCOMPLETE"
            ]
        item["processed_at"] = processed_at


def build_twitter_digest(
    bridge: CollectorBridge,
    embedding_client: Optional[object] = None,
    enrichment_client: Optional[object] = None,
) -> PlatformOutcome:
    """采集、筛选、摘要、存储并渲染 Twitter，不做推送与状态确认。

    多平台统一入口复用本函数；单平台入口 ``run_twitter_workflow`` 在此基础上
    追加确认与推送，行为与改造前一致。
    """
    if getattr(config, "TWITTER_EMBEDDING_ENABLED", True):
        print("\n[Twitter 0/8] 验证 Embedding 服务")
        try:
            dimensions = probe_twitter_embedding_service(
                client=embedding_client,
            )
        except TwitterEmbeddingError as exc:
            print(f"Twitter Embedding 预检失败：{exc}")
            return PlatformOutcome(
                "x", STATUS_FAILED, EXIT_EMBEDDING, error=str(exc)
            )
        print(f"Twitter Embedding 预检通过：向量维度 {dimensions}")

    print("\n[Twitter 1/8] 启动数据采集")
    crawl_result: CrawlRunResult = bridge.run()
    if not crawl_result.success:
        print(f"Twitter 采集失败：{crawl_result.error}")
        return PlatformOutcome(
            "x", STATUS_FAILED, EXIT_CRAWLER, error=crawl_result.error
        )

    print("\n[Twitter 2/8] 加载并标准化本次数据")
    try:
        items = load_twitter_items(crawl_result.data_files)
    except ValueError as exc:
        print(f"Twitter 数据加载失败：{exc}")
        return PlatformOutcome(
            "x", STATUS_FAILED, EXIT_UNEXPECTED, error=str(exc)
        )
    if not items:
        print("本次运行没有可处理的 Twitter 内容")
        return PlatformOutcome("x", STATUS_EMPTY, EXIT_NO_DATA)

    print("\n[Twitter 3/8] 执行规则和 Embedding 筛选")
    reply_provider = (
        bridge
        if callable(getattr(bridge, "fetch_replies", None))
        else None
    )
    try:
        filtered = run_twitter_preselection_filters(
            items,
            embedding_client=embedding_client,
        )
    except TwitterEmbeddingError as exc:
        print(f"Twitter Embedding 筛选失败：{exc}")
        return PlatformOutcome(
            "x", STATUS_FAILED, EXIT_EMBEDDING, error=str(exc)
        )

    print("\n[Twitter 4/8] 生成每日 8+4 选择")
    try:
        recent_history = load_recent_twitter_digest_items()
    except TwitterResultStoreError as exc:
        print(str(exc))
        return PlatformOutcome("x", STATUS_FAILED, EXIT_STORE, error=str(exc))
    try:
        selection = select_twitter_daily_items(
            filtered["passed"],
            reply_provider=reply_provider,
            recent_history=recent_history,
        )
    except (
        TwitterCommentConfigError,
        TwitterDailySelectionConfigError,
    ) as exc:
        print(f"Twitter 每日选择配置无效：{exc}")
        return PlatformOutcome("x", STATUS_FAILED, EXIT_CONFIG, error=str(exc))

    print("\n[Twitter 5/8] 生成极简摘要和分层标签")
    try:
        selected_items = enrich_twitter_items(
            selection["selected"],
            client=enrichment_client,
        )
    except TwitterEnrichmentConfigError as exc:
        print(f"Twitter 摘要或标签配置无效：{exc}")
        return PlatformOutcome("x", STATUS_FAILED, EXIT_CONFIG, error=str(exc))
    _finalize(filtered["all_items"], selected_items)

    print("\n[Twitter 6/8] 写入结构化结果")
    try:
        result_path = append_twitter_results(filtered["all_items"])
    except TwitterResultStoreError as exc:
        print(str(exc))
        return PlatformOutcome("x", STATUS_FAILED, EXIT_STORE, error=str(exc))
    print(f"Twitter 结构化结果已写入 {result_path}")

    print("\n[Twitter 7/8] 更新每日页面")
    try:
        report_path = render_twitter_feed(
            primary_items=selection["primary"],
            more_items=selection["more"],
            digest_date=str(selection["digest_date"]),
        )
    except (TwitterFeedRenderError, TwitterResultStoreError) as exc:
        print(str(exc))
        return PlatformOutcome("x", STATUS_FAILED, EXIT_RENDER, error=str(exc))
    print(f"Twitter 聚合页面已更新 {report_path}")

    return PlatformOutcome(
        platform="x",
        status=STATUS_OK,
        crawled=len(items),
        passed=len(filtered["passed"]),
        items=selected_items,
        result_path=result_path,
        report_path=report_path,
        meta={
            "primary": selection["primary"],
            "more": selection["more"],
            "digest_date": selection["digest_date"],
        },
    )


def run_twitter_workflow(
    bridge: CollectorBridge,
    embedding_client: Optional[object] = None,
    enrichment_client: Optional[object] = None,
) -> int:
    """运行 Twitter 专用流程，不调用任何旧平台处理模块。"""
    outcome = build_twitter_digest(
        bridge,
        embedding_client=embedding_client,
        enrichment_client=enrichment_client,
    )
    if not outcome.ok:
        return outcome.exit_code

    selection = {
        "primary": outcome.meta.get("primary", []),
        "more": outcome.meta.get("more", []),
    }

    state_error = bridge.acknowledge()
    if state_error:
        print(f"Twitter 采集状态保存失败：{state_error}")
        return EXIT_STATE

    print("\n[Twitter 8/8] 可选通知")
    if config.TWITTER_ENABLE_WECOM and not send_twitter_wecom(
        selection["primary"],
        more_count=len(selection["more"]),
    ):
        print("Twitter 通知失败，结果和采集状态已保留")
        return EXIT_NOTIFY
    if not config.TWITTER_ENABLE_WECOM:
        print("Twitter 企业微信通知未启用")

    print(
        f"Twitter 工作流完成：候选 {outcome.crawled}，"
        f"预筛通过 {outcome.passed}，"
        f"主推 {len(selection['primary'])}，"
        f"补充 {len(selection['more'])}"
    )
    return EXIT_OK
