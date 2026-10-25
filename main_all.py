"""多平台统一入口：一次采集所有已启用平台，合并筛选、复评并推送一份日报。

与 ``main.py`` 的关系
--------------------
``main.py`` 仍然只跑单个平台（由 ``CRAWL_PLATFORM`` 决定），行为不变。
本入口复用同一批采集器与筛选器，只多了跨平台编排、LLM 复评与混合推送。

用法
----
    # 跑配置里启用的全部平台
    python main_all.py

    # 指定平台
    python main_all.py --platforms xhs,zhihu,github

    # 只看候选池：不调 LLM、不生成研报、不推送
    python main_all.py --dry-run

    # 完整流程但不推送（用于本地验证）
    python main_all.py --no-push

    # 跳过 LLM 复评，按平台内名次交错（离线可用）
    python main_all.py --no-llm
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional, Sequence

import config
from pipeline.orchestrator import EXIT_UNEXPECTED, run_all

SUPPORTED_PLATFORMS = ("xhs", "zhihu", "github", "x", "reddit", "youtube")


def default_platforms() -> List[str]:
    platforms = [
        str(platform).strip().lower()
        for platform in getattr(config, "MULTI_ENABLED_PLATFORMS", [])
        if str(platform).strip()
    ]
    if getattr(config, "MULTI_INCLUDE_YOUTUBE", False):
        platforms.append("youtube")
    return [platform for platform in platforms if platform in SUPPORTED_PLATFORMS]


def parse_platforms(values: Optional[Sequence[str]]) -> List[str]:
    if not values:
        return default_platforms()

    parsed: List[str] = []
    for value in values:
        for piece in str(value).replace("，", ",").split(","):
            platform = piece.strip().lower()
            if not platform:
                continue
            if platform not in SUPPORTED_PLATFORMS:
                raise ValueError(
                    f"不支持的平台：{platform}；可选 {', '.join(SUPPORTED_PLATFORMS)}"
                )
            if platform not in parsed:
                parsed.append(platform)
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="多平台统一采集、合并筛选与日报推送",
    )
    parser.add_argument(
        "--platforms",
        nargs="+",
        help="要运行的平台，逗号或空格分隔；默认取 MULTI_ENABLED_PLATFORMS",
    )
    parser.add_argument(
        "--total",
        type=int,
        help="最终日报条数，默认 MULTI_TOTAL_ITEMS",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只构建候选池，不调用 LLM、不生成研报、不推送",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="跑完整流程但不发送企业微信",
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="跳过 LLM 复评，按平台内名次交错选取",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        platforms = parse_platforms(args.platforms)
    except ValueError as exc:
        print(f"❌ {exc}")
        return 2
    if not platforms:
        print("❌ 没有可运行的平台，请检查 MULTI_ENABLED_PLATFORMS 或 --platforms")
        return 2

    print("=" * 68)
    print("🚀 AI Content Miner · 多平台统一运行")
    print(f"平台：{', '.join(platforms)}")
    if args.dry_run:
        print("模式：dry-run（不调 LLM / 不生成研报 / 不推送）")
    elif args.no_push:
        print("模式：no-push（不发送企业微信）")
    print("=" * 68)

    try:
        summary = run_all(
            platforms,
            dry_run=args.dry_run,
            no_push=args.no_push,
            total=args.total,
            llm_rescore=False if args.no_llm else None,
        )
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"\n❌ 执行异常：{exc}")
        import traceback

        traceback.print_exc()
        return EXIT_UNEXPECTED

    print("\n" + "=" * 68)
    print("📊 汇总")
    print(f"   - 候选池：{len(summary.candidates)} 条")
    print(f"   - 日报：{len(summary.selected)} 条")
    print(f"   - 推送：{'已发送' if summary.pushed else '未发送'}")
    if summary.digest_path:
        print(f"   - 聚合页：{summary.digest_path}")
    print(f"   - 退出码：{summary.exit_code}")
    print("=" * 68)
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
