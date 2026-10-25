"""AI Content Miner 工作流主入口。

职责仅限程序编排：配置检查 → 按平台路由 → 本次爬取 → 数据加载
→ 平台筛选 → 输出 → 企业微信推送。各业务实现仍由已有模块负责。
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from datetime import datetime
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import config
from crawler.base import CollectorBridge, CrawlRunResult
from crawler.factory import build_collector
from utils.parser import load_articles
from analyzer.filter import (
    multi_stage_filter,
    recent_keyword_filter,
)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CONFIG = 2
EXIT_CRAWLER = 3
EXIT_NO_DATA = 4
EXIT_NOTIFY = 5
EXIT_STATE = 6
EXIT_EMBEDDING = 7


def _is_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def validate_runtime_config(bridge: CollectorBridge) -> List[str]:
    """在爬虫启动前检查完整工作流所需的关键配置。"""
    if getattr(bridge, "platform", "") == "x":
        from workflows.twitter import validate_twitter_runtime_config

        return validate_twitter_runtime_config(bridge)
    if getattr(bridge, "platform", "") == "reddit":
        from workflows.reddit import validate_reddit_runtime_config

        return validate_reddit_runtime_config(bridge)
    if getattr(bridge, "platform", "") == "youtube":
        from workflows.youtube import validate_youtube_runtime_config

        return validate_youtube_runtime_config(bridge)

    errors: List[str] = []

    for module_name in ("openai", "requests", "qcloud_cos"):
        if importlib.util.find_spec(module_name) is None:
            errors.append(
                f"缺少项目依赖 {module_name}；请先运行 "
                "python3 -m pip install -r requirements.txt"
            )

    api_key = str(getattr(config, "API_KEY", "")).strip()
    api_placeholders = {"your-api-key-here", "sk-xxxxx", "your-key"}
    if not api_key or api_key.lower() in api_placeholders:
        errors.append("API_KEY 未配置或仍是占位值")

    base_url = str(getattr(config, "BASE_URL", "")).strip()
    if not _is_http_url(base_url):
        errors.append("BASE_URL 必须是有效的 http/https URL")
    if not str(getattr(config, "MODEL_NAME", "")).strip():
        errors.append("MODEL_NAME 不能为空")

    webhook = str(getattr(config, "WECOM_WEBHOOK", "")).strip()
    webhook_placeholders = ("your-webhook-key", "your-key", "example.com")
    if not _is_http_url(webhook):
        errors.append("WECOM_WEBHOOK 必须是有效的 http/https URL")
    elif any(placeholder in webhook.lower() for placeholder in webhook_placeholders):
        errors.append("WECOM_WEBHOOK 未配置或仍是占位值")

    cos_required = {
        "COS_SECRET_ID": getattr(config, "COS_SECRET_ID", ""),
        "COS_SECRET_KEY": getattr(config, "COS_SECRET_KEY", ""),
        "COS_REGION": getattr(config, "COS_REGION", ""),
        "COS_BUCKET": getattr(config, "COS_BUCKET", ""),
    }
    missing_cos = [
        name for name, value in cos_required.items()
        if not str(value or "").strip()
    ]
    if missing_cos:
        errors.append(f"COS 配置缺失：{', '.join(missing_cos)}")

    report_base_url = str(getattr(config, "REPORT_BASE_URL", "") or "").strip()
    if report_base_url and not _is_http_url(report_base_url):
        errors.append("REPORT_BASE_URL 必须是有效的 http/https URL")

    if getattr(bridge, "platform", "") in {"xhs", "zhihu"}:
        embedding_provider = str(
            getattr(config, "EMBEDDING_PROVIDER", "openai")
        ).strip().lower()
        if embedding_provider not in {"openai", "dashscope"}:
            errors.append("EMBEDDING_PROVIDER 只能是 openai 或 dashscope")
        if not str(getattr(config, "EMBEDDING_API_KEY", "")).strip():
            required = (
                "DASHSCOPE_API_KEY"
                if embedding_provider == "dashscope"
                else "EMBEDDING_API_KEY"
            )
            errors.append(f"{required} 未配置")
        if embedding_provider == "openai" and not _is_http_url(
            str(getattr(config, "EMBEDDING_BASE_URL", ""))
        ):
            errors.append("EMBEDDING_BASE_URL 必须是有效的 http/https URL")
        if (
            embedding_provider == "dashscope"
            and importlib.util.find_spec("dashscope") is None
        ):
            errors.append(
                "缺少 dashscope；请先运行 python -m pip install -r requirements.txt"
            )
        if not str(getattr(config, "EMBEDDING_MODEL", "")).strip():
            errors.append("EMBEDDING_MODEL 不能为空")

    if getattr(bridge, "platform", "") == "github":
        from analyzer.github_embedding import (
            GithubEmbeddingError,
            validate_github_embedding_config,
        )
        from analyzer.github_quality import validate_github_quality_config
        from analyzer.github_rules import validate_github_rule_config

        try:
            validate_github_rule_config()
            validate_github_embedding_config()
            validate_github_quality_config()
        except (GithubEmbeddingError, ValueError) as exc:
            errors.append(str(exc))
            
    score_threshold = getattr(config, "SCORE_THRESHOLD", None)
    if (
        not isinstance(score_threshold, (int, float))
        or isinstance(score_threshold, bool)
        or not 0 <= score_threshold <= 10
    ):
        errors.append("SCORE_THRESHOLD 必须是 0 到 10 之间的数字")

    errors.extend(bridge.validate())
    return errors




def generate_reports(scored_items: List[Dict]) -> Tuple[List[Dict], int]:
    """仅对最高分的合格内容生成报告，默认最多十篇。"""
    from output.generator import generate_output
    from utils.raditer import log_decision

    print("\n📝 [5/6] 生成报告...")
    final_items: List[Dict] = []
    generated_count = 0
    score_threshold = float(getattr(config, "SCORE_THRESHOLD", 6.0))
    max_reports = int(getattr(config, "REPORT_MAX_ITEMS", 10))
    eligible_items = [
        item for item in scored_items
        if float(item.get("total_score", 0) or 0) >= score_threshold
    ]
    eligible_items.sort(
        key=lambda item: float(item.get("total_score", 0) or 0),
        reverse=True,
    )
    selected_items = (
        eligible_items[:max_reports] if max_reports > 0 else eligible_items
    )
    skipped_by_limit = len(eligible_items) - len(selected_items)
    if skipped_by_limit:
        print(
            f"   ℹ️ 按综合分仅生成前 {len(selected_items)} 篇，"
            f"跳过 {skipped_by_limit} 篇"
        )

    for index, item in enumerate(selected_items, start=1):
        article = item.get("article", {})
        title = article.get("title", "无标题")[:25]
        if not item.get("dimensions") or not item.get("scores"):
            from analyzer.scorer import score_and_classify

            dimension_score = score_and_classify(article)
            item.update(
                {
                    key: value for key, value in dimension_score.items()
                    if key not in {"article", "total_score"}
                }
            )

        try:
            output_path = generate_output(item)
            if not output_path:
                print(f"   ❌ 未生成输出: {title}")
                continue

            log_decision(item, output_path)
            final_items.append(item)
            generated_count += 1
            print(
                f"   ✅ [{index}/{len(selected_items)}] {title} → {output_path}"
            )
        except Exception as exc:
            print(f"   ❌ 处理异常: {title} - {exc}")

        time.sleep(1)

    print(f"   ✅ 生成完成，共 {generated_count} 篇报告")
    return final_items, generated_count



def _print_retrieval_stats(articles: Sequence[Dict], passed_items: Sequence[Dict], final_items: Sequence[Dict]) -> None:
    """Print per-query production yield without changing scoring decisions."""
    passed_ids = {id(item.get("article", {})) for item in passed_items}
    report_ids = {id(item.get("article", {})) for item in final_items}
    stats: Dict[str, Dict[str, int]] = {}
    for article in articles:
        for match in article.get("_retrieval_matches", []):
            key = f"{match['intent_group']} / {match['query']}"
            bucket = stats.setdefault(key, {"collected": 0, "passed": 0, "reports": 0})
            bucket["collected"] += 1
            article_id = id(article)
            if article_id in passed_ids:
                bucket["passed"] += 1
            if article_id in report_ids:
                bucket["reports"] += 1
    if not stats:
        return
    print("   📈 检索产出统计（按意图组 / 查询）：")
    for key, bucket in sorted(stats.items(), key=lambda item: (-item[1]["reports"], item[0])):
        print(
            f"      {key}: 采集 {bucket['collected']}，"
            f"评分通过 {bucket['passed']}，研报 {bucket['reports']}"
        )

def _run_github_filters(articles: Sequence[Dict]) -> Tuple[List[Dict], int]:
    """运行 GitHub 专用筛选，并转换为已有报告生成器输入。"""
    from analyzer.github_pipeline import run_github_filters

    filtered = run_github_filters(articles)
    passed_items: List[Dict] = []
    for index, article in enumerate(filtered["passed"], start=1):
        metadata = article.get("github_filter_metadata", {})
        stages = metadata.get("stages", []) if isinstance(metadata, dict) else []
        quality = next(
            (
                stage for stage in reversed(stages)
                if isinstance(stage, dict) and stage.get("stage") == "quality"
            ),
            {},
        )
        total_score = float(quality.get("score", 0.0) or 0.0)
        components = quality.get("components", {})
        passed_items.append(
            {
                "article": article,
                "total_score": total_score,
                "filter_result": metadata,
                "blogger_weight": 1.0,
                "dimensions": [
                    "相关度", "文档质量", "社区热度", "活跃度", "项目元数据"
                ],
                "scores": [
                    float(components.get("relevance", 0.0)),
                    float(components.get("documentation", 0.0)),
                    float(components.get("community", 0.0)),
                    float(components.get("activity", 0.0)),
                    float(components.get("metadata", 0.0)),
                ],
                "summary": (
                    str(article.get("raw", {}).get("description", "") or "")
                    or "GitHub 开源仓库"
                )[:180],
                "category": "GitHub 开源项目",
            }
        )
        title = article.get("title", "无标题")[:25]
        print(
            f"   ✅ 通过 [{index}/{len(filtered['passed'])}] {title} "
            f"→ GitHub 质量分 {total_score:.2f}"
        )

    for index, article in enumerate(filtered["dropped"], start=1):
        metadata = article.get("github_filter_metadata", {})
        reasons = metadata.get("final_reason_codes", []) if isinstance(metadata, dict) else []
        title = article.get("title", "无标题")[:25]
        print(
            f"   ⏭️ 淘汰 [{index}/{len(filtered['dropped'])}] {title} "
            f"→ {', '.join(reasons) or 'GITHUB_FILTER_DROPPED'}"
        )
    return passed_items, len(filtered["dropped"])


def run_generic_filters(articles: Sequence[Dict]) -> Tuple[List[Dict], int]:
    """对文章逐篇执行四层筛选，返回通过项与淘汰数。

    单平台入口与多平台编排共用本函数，避免两处实现产生分歧。
    """
    print("\n🧠 [3/6] 执行四层筛选（规则 → 语义去重 → 评论区 → 博主画像）...")
    passed_items: List[Dict] = []
    filtered_count = 0
    for idx, article in enumerate(articles, start=1):
        result = multi_stage_filter(
            article,
            existing_articles=None,
            enable_semantic=True,
            enable_comment=True,
            enable_author_profile=True,
        )
        title = article.get("title", "无标题")[:25]
        if not result.passed:
            filtered_count += 1
            print(
                f"   ⏭️ 淘汰 [{idx}/{len(articles)}] {title} → "
                f"{result.rule_reason or result.semantic_reason or result.comment_reason or result.author_reason}"
            )
            continue
        passed_items.append(
            {
                "article": article,
                "total_score": result.total_score(),
                "filter_result": result.to_dict(),
                "blogger_weight": 1.0,
            }
        )
        print(
            f"   ✅ 通过 [{idx}/{len(articles)}] {title} "
            f"→ 综合得分 {result.total_score():.2f}"
        )
    return passed_items, filtered_count


def run_workflow(bridge: CollectorBridge) -> int:
    """运行已通过配置检查的完整工作流，并返回进程退出码。"""
    if getattr(bridge, "platform", "") == "x":
        from workflows.twitter import run_twitter_workflow

        return run_twitter_workflow(bridge)
    if getattr(bridge, "platform", "") == "reddit":
        from workflows.reddit import run_reddit_workflow

        return run_reddit_workflow(bridge)
    if getattr(bridge, "platform", "") == "youtube":
        from workflows.youtube import run_youtube_workflow

        return run_youtube_workflow(bridge)

    print("\n📡 [1/6] 启动数据采集...")
    crawl_result: CrawlRunResult = bridge.run()
    if not crawl_result.success:
        print(f"   ❌ 爬取失败: {crawl_result.error}")
        return EXIT_CRAWLER
    print(f"   ✅ 爬取完成，内容文件 {len(crawl_result.data_files)} 个")

    print("\n📂 [2/6] 加载本次运行数据...")
    articles = load_articles(
        crawl_result.data_files,
        platform=bridge.platform,
        allow_manual_fallback=False,
    )
    if not articles:
        print("   ❌ 本次运行没有可处理的内容数据，流程终止")
        return EXIT_NO_DATA
    print(f"   ✅ 加载 {len(articles)} 篇文章")

    if getattr(bridge, "platform", "") in {"xhs", "zhihu"}:
        articles, recent_stats = recent_keyword_filter(articles)
        print(
            "   🗓️ 三日关键词筛选："
            f"保留 {recent_stats['kept']} 篇，"
            f"时间淘汰 {recent_stats['dropped_old']} 篇，"
            f"无时间 {recent_stats['dropped_missing_time']} 篇，"
            f"无关键词 {recent_stats['dropped_unrelated']} 篇"
        )
        if recent_stats["truncated"]:
            print(
                "   ⚠️ 相关内容超过上限，"
                f"另截断 {recent_stats['truncated']} 篇"
            )
        if not articles:
            print("   ❌ 最近三天没有命中关键词的内容，流程终止")
            return EXIT_NO_DATA
    if getattr(bridge, "platform", "") == "github":
        print("\n🧠 [3/6] 执行 GitHub 筛选（仓库规则 → 关键词 Embedding → 质量评分）...")
        try:
            passed_items, filtered_count = _run_github_filters(articles)
        except Exception as exc:
            from analyzer.github_embedding import GithubEmbeddingError

            if isinstance(exc, GithubEmbeddingError):
                print(f"   ❌ GitHub Embedding 筛选失败: {exc}")
                return EXIT_EMBEDDING
            print(f"   ❌ GitHub 筛选失败: {type(exc).__name__}: {exc}")
            return EXIT_UNEXPECTED
    else:
        passed_items, filtered_count = run_generic_filters(articles)

    print(f"   ✅ 筛选完成：通过 {len(passed_items)} 篇，淘汰 {filtered_count} 篇")
    # ----- 后续：RAL（禁用） + 生成报告 + 推送 -----
    if hasattr(config, "ENABLE_RETRIEVAL"):
        original_retrieval = config.ENABLE_RETRIEVAL
        config.ENABLE_RETRIEVAL = False   # 临时禁用
    else:
        original_retrieval = False

    final_items, generated_count = generate_reports(passed_items)
    if getattr(bridge, "platform", "") in {"xhs", "zhihu"}:
        _print_retrieval_stats(articles, passed_items, final_items)

    print("\n📤 [6/6] 推送企业微信...")
    if final_items:
        from notifier.wecom import send_to_wecom
        if not send_to_wecom(final_items):
            print("   ❌ 推送失败")
            return EXIT_NOTIFY
    else:
        print("   ℹ️ 无高分文章，跳过推送")

    state_error = bridge.acknowledge()
    if state_error:
        print(f"   ❌ 采集状态保存失败: {state_error}")
        return EXIT_STATE

    print("\n" + "=" * 70)
    print("🎉 工作流执行完毕！")
    print("📊 统计：")
    print(f"   - 读取文章: {len(articles)} 篇")
    print(f"   - 通过筛选: {len(passed_items)} 篇")
    print(f"   - 平台筛选淘汰: {filtered_count} 篇")
    print(f"   - 生成报告: {generated_count} 篇")
    print(f"   - 推送报告: {len(final_items)} 篇")
    print("=" * 70)
    return EXIT_OK


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Content Miner 工作流")
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="仅检查运行配置、采集器依赖和本地会话，不启动爬虫",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_argument_parser().parse_args(argv)

    print("=" * 70)
    print("🚀 AI Content Miner - 全自动化知识工作流")
    print(f"⏰ 启动时间: {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("=" * 70)

    bridge = build_collector()
    config_errors = validate_runtime_config(bridge)
    if config_errors:
        print("\n❌ 配置检查失败：")
        for error in config_errors:
            print(f"   - {error}")
        return EXIT_CONFIG

    if (
        getattr(bridge, "platform", "") != "x"
        and (
            "127.0.0.1" in config.REPORT_BASE_URL
            or "localhost" in config.REPORT_BASE_URL
        )
    ):
        print(
            "\n⚠️ REPORT_BASE_URL 为本地地址，企业员工可能无法访问生成的报告"
        )

    print("\n✅ 配置检查通过")
    if args.check_config:
        return EXIT_OK
    return run_workflow(bridge)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断执行")
        sys.exit(130)
    except Exception as exc:
        print(f"\n❌ 执行异常: {exc}")
        import traceback

        traceback.print_exc()
        sys.exit(EXIT_UNEXPECTED)
