"""多平台统一运行的编排层。

职责只有编排：逐个平台采集与筛选 → 取 top-k 候选 → LLM 复评 → 选出日报
→ 生成研报与聚合页 → 一次推送 → 全部成功后才确认各平台状态。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import config
from crawler.factory import build_collector
from notifier.digest_wecom import send_digest, validate_digest_wecom_config
from output.digest import render_digest_feed
from pipeline import ranking
from pipeline.stages import collect_platform, group_by_platform, platform_label
from pipeline.types import DigestItem
from workflows.outcome import (
    STATUS_EMPTY,
    STATUS_FAILED,
    STATUS_OK,
    STATUS_SKIPPED,
    PlatformOutcome,
)

EXIT_OK = 0
EXIT_UNEXPECTED = 1
EXIT_CONFIG = 2
EXIT_CRAWLER = 3
EXIT_NO_DATA = 4
EXIT_NOTIFY = 5
EXIT_STATE = 6
EXIT_PARTIAL = 10


@dataclass
class RunSummary:
    digest_date: str
    outcomes: List[PlatformOutcome] = field(default_factory=list)
    candidates: List[DigestItem] = field(default_factory=list)
    selected: List[DigestItem] = field(default_factory=list)
    messages: int = 0
    digest_path: Optional[str] = None
    pushed: bool = False
    exit_code: int = EXIT_OK

    def to_dict(self) -> Dict:
        return {
            "digest_date": self.digest_date,
            "exit_code": self.exit_code,
            "pushed": self.pushed,
            "messages": self.messages,
            "digest_path": self.digest_path,
            "platforms": [
                {
                    "platform": outcome.platform,
                    "status": outcome.status,
                    "crawled": outcome.crawled,
                    "passed": outcome.passed,
                    "candidates": len(
                        [item for item in self.candidates if item.platform == outcome.platform]
                    ),
                    "selected": len(
                        [item for item in self.selected if item.platform == outcome.platform]
                    ),
                    "error": outcome.error,
                    "result_path": str(outcome.result_path or ""),
                    "report_path": str(outcome.report_path or ""),
                }
                for outcome in self.outcomes
            ],
            "items": [
                {
                    "platform": item.platform,
                    "title": item.title,
                    "score": item.judge_score if item.judge_score is not None else item.score,
                    "url": item.url,
                    "report_url": item.report_url or "",
                }
                for item in self.selected
            ],
        }


def _summary_dir() -> Path:
    return Path(config.MULTI_DIGEST_DIR)


def _write_summary(summary: RunSummary) -> None:
    try:
        target = _summary_dir() / f"summary-{summary.digest_date}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(summary.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"   ⚠️ 运行摘要写入失败：{exc}")


def _print_outcome_table(outcomes: Sequence[PlatformOutcome]) -> None:
    print("\n" + "=" * 68)
    print("📋 平台结果")
    for outcome in outcomes:
        label = platform_label(outcome.platform)
        if outcome.status == STATUS_OK:
            detail = f"采集 {outcome.crawled}，通过 {outcome.passed}"
        elif outcome.status == STATUS_EMPTY:
            detail = "本次无新增内容"
        elif outcome.status == STATUS_SKIPPED:
            detail = f"跳过：{outcome.error}"
        else:
            detail = f"失败：{outcome.error}"
        print(f"   - {label:<7} [{outcome.status}] {detail}")


def _generate_reports(items: Sequence[DigestItem], limit: int) -> None:
    """只为最终入选的逐条研报平台生成 HTML（并尝试上传 COS）。"""
    if limit <= 0:
        return
    try:
        from output.generator import generate_output
    except Exception as exc:  # noqa: BLE001 - 缺少依赖或 API key 时降级
        print(f"   ⚠️ 研报生成不可用，跳过：{exc}")
        return

    print(f"\n📝 生成研报（上限 {limit} 篇）")
    generated = 0
    for item in items:
        if generated >= limit:
            break
        if not item.report_eligible:
            continue
        article = item.source_item.get("article")
        if not isinstance(article, dict):
            continue
        if not item.judge_dimensions or not item.judge_scores:
            print(f"   ⏭️ 跳过（缺少评分维度）：{item.title[:24]}")
            continue
        entry = {
            "article": article,
            "total_score": item.judge_score or item.score,
            "dimensions": item.judge_dimensions,
            "scores": item.judge_scores,
            "summary": item.judge_summary,
            "category": item.judge_category or "其他",
        }
        try:
            path = generate_output(entry)
            item.report_path = str(path)
            item.report_url = str(entry.get("_preview_url") or "")
            item.download_url = str(entry.get("_download_url") or "")
            generated += 1
            print(f"   ✅ {item.title[:24]} → {path}")
        except Exception as exc:  # noqa: BLE001
            print(f"   ❌ 研报生成失败 {item.title[:24]}：{exc}")


def run_all(
    platforms: Sequence[str],
    *,
    dry_run: bool = False,
    no_push: bool = False,
    total: Optional[int] = None,
    llm_rescore: Optional[bool] = None,
    include_unjudged: bool = False,
    bridge_factory: Optional[object] = None,
) -> RunSummary:
    """执行一次多平台运行，返回可审计的摘要。"""
    digest_date = datetime.now().strftime("%Y-%m-%d")
    summary = RunSummary(digest_date=digest_date)
    make_bridge = bridge_factory or build_collector

    if not dry_run and not no_push:
        errors = validate_digest_wecom_config()
        if errors:
            for error in errors:
                print(f"❌ {error}")
            summary.exit_code = EXIT_CONFIG
            return summary

    bridges: Dict[str, object] = {}
    for platform in platforms:
        print(f"\n{'=' * 68}\n🚀 平台：{platform_label(platform)}")
        try:
            bridge = make_bridge(platform)
        except Exception as exc:  # noqa: BLE001
            summary.outcomes.append(
                PlatformOutcome(
                    platform, STATUS_FAILED, EXIT_UNEXPECTED,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue

        errors = bridge.validate()
        if errors:
            print("   ⏭️ 配置不完整，跳过：")
            for error in errors:
                print(f"      - {error}")
            summary.outcomes.append(
                PlatformOutcome(
                    platform, STATUS_SKIPPED, EXIT_CONFIG,
                    error="；".join(errors),
                )
            )
            continue

        bridges[platform] = bridge
        outcome = collect_platform(platform, bridge)
        summary.outcomes.append(outcome)

    _print_outcome_table(summary.outcomes)

    grouped = group_by_platform(summary.outcomes)
    summary.candidates = ranking.take_top_k(grouped)
    print(
        f"\n🎯 候选池：{len(summary.candidates)} 条"
        f"（各平台 top-k 上限 {getattr(config, 'MULTI_PLATFORM_QUOTA', {})}）"
    )

    if not summary.candidates:
        print("❌ 没有任何平台产出候选内容")
        summary.exit_code = (
            EXIT_CRAWLER
            if any(o.status == STATUS_FAILED for o in summary.outcomes)
            else EXIT_NO_DATA
        )
        _write_summary(summary)
        return summary

    if dry_run:
        summary.selected = summary.candidates
        _apply_partial_status(summary)
        _write_summary(summary)
        print("\n🧪 dry-run：只展示候选池，不调用 LLM、不生成研报、不推送")
        _print_candidates(summary.candidates)
        return summary

    judged_count = ranking.judge_items(summary.candidates, enabled=llm_rescore)
    unjudged = [
        item for item in summary.candidates if item.judge_score is None
    ]
    if judged_count and unjudged:
        preview = "；".join(item.title[:20] for item in unjudged[:3])
        print(
            f"   ⚠️ {len(unjudged)} 条候选复评失败，默认排除：{preview}"
        )
    elif not judged_count and unjudged:
        print("   ⚠️ 全部候选复评失败，回退到平台轮转排序")

    summary.selected = ranking.select_final(
        summary.candidates,
        total=total,
        include_unjudged=include_unjudged,
    )
    print(f"\n🏆 最终日报：{len(summary.selected)} 条")
    _print_candidates(summary.selected)

    _generate_reports(
        summary.selected,
        int(getattr(config, "MULTI_REPORT_TOP_N", 5)),
    )

    digest_path = render_digest_feed(summary.selected, digest_date=digest_date)
    summary.digest_path = str(digest_path)
    print(f"🖼️ 聚合页：{digest_path}")

    if not summary.selected:
        _write_summary(summary)
        summary.exit_code = EXIT_NO_DATA
        return summary

    if no_push:
        print("📴 --no-push：跳过企业微信推送")
        _apply_partial_status(summary)
        _write_summary(summary)
        return summary

    if not send_digest(summary.selected, digest_date=digest_date):
        print("❌ 日报推送失败，本次不确认任何平台状态")
        summary.exit_code = EXIT_NOTIFY
        _write_summary(summary)
        return summary
    summary.pushed = True

    state_error = ""
    for outcome in summary.outcomes:
        # 只确认本次真正完成的平台：采集失败或跳过的平台没有待确认数据。
        if outcome.status != STATUS_OK:
            continue
        bridge = bridges.get(outcome.platform)
        if bridge is None:
            continue
        error = bridge.acknowledge()
        if error:
            state_error = f"{outcome.platform}: {error}"
            break
    if state_error:
        print(f"❌ 采集状态保存失败：{state_error}")
        summary.exit_code = EXIT_STATE
        _write_summary(summary)
        return summary

    _apply_partial_status(summary)
    _write_summary(summary)
    return summary


def _apply_partial_status(summary: RunSummary) -> None:
    """按平台状态写退出码：有失败或跳过即部分成功。

    ``--no-push`` 和 ``--dry-run`` 也必须走这里，否则"平台全挂但退出码 0"
    会让调度器误判为成功。
    """
    failed = [
        outcome
        for outcome in summary.outcomes
        if outcome.status in {STATUS_FAILED, STATUS_SKIPPED}
    ]
    if not failed:
        summary.exit_code = EXIT_OK
        return

    summary.exit_code = EXIT_PARTIAL
    print(
        "⚠️ 部分平台未完成："
        + "，".join(
            f"{platform_label(outcome.platform)}({outcome.status})"
            for outcome in failed
        )
    )


def _print_candidates(items: Sequence[DigestItem]) -> None:
    for index, item in enumerate(items, start=1):
        score = item.judge_score if item.judge_score is not None else item.score
        print(
            f"   {index:>2}. [{platform_label(item.platform):<7}] "
            f"{score:5.1f}  {item.title[:40]}"
        )
