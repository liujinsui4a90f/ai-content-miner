"""Structured-feed workflow for the sibling YTB_LLM YouTube pipeline."""

from __future__ import annotations

import json
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

import config
from crawler.base import CollectorBridge
from output.youtube_feed import (
    YoutubeFeedError,
    render_youtube_feed,
    youtube_records_from_digest,
)
from utils.youtube_result_store import (
    YoutubeResultStoreError,
    append_youtube_results,
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
EXIT_STORE = 8
EXIT_RENDER = 9

YTB_SCRIPT_SEQUENCE = (
    ("daily_fetch.py", ()),
    ("relevance_gate.py", ("{run_date}",)),
    ("score_and_select.py", ("{run_date}",)),
)


def _ytb_path() -> Path:
    return Path(config.YOUTUBE_YTB_LLM_PATH).expanduser().resolve()


def validate_youtube_runtime_config(bridge: CollectorBridge) -> List[str]:
    """Validate only the YouTube structured workflow requirements."""
    errors: List[str] = []
    errors.extend(bridge.validate())
    if not isinstance(config.YOUTUBE_FEED_RETENTION_DAYS, int) or config.YOUTUBE_FEED_RETENTION_DAYS <= 0:
        errors.append("YOUTUBE_FEED_RETENTION_DAYS must be a positive integer")
    if not isinstance(config.YOUTUBE_FEED_MAX_ITEMS, int) or config.YOUTUBE_FEED_MAX_ITEMS <= 0:
        errors.append("YOUTUBE_FEED_MAX_ITEMS must be a positive integer")
    return errors


def _run_ytb_script(script: str, args: tuple[str, ...], run_date: str) -> None:
    command = [
        str(config.YOUTUBE_PYTHON),
        script,
        *[arg.format(run_date=run_date) for arg in args],
    ]
    print(f"YouTube/YTB_LLM: {' '.join(command)}")
    completed = subprocess.run(
        command,
        cwd=_ytb_path(),
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n")
    if completed.returncode != 0:
        raise RuntimeError(
            f"{script} failed with exit code {completed.returncode}"
        )


def run_ytb_pipeline(run_date: Optional[str] = None) -> Path:
    """Run YTB_LLM's existing scripts and return the produced digest path."""
    active_date = run_date or date.today().isoformat()
    datetime.strptime(active_date, "%Y-%m-%d")
    for script, args in YTB_SCRIPT_SEQUENCE:
        _run_ytb_script(script, args, active_date)
    digest_path = _ytb_path() / "data" / "digests" / f"digest_{active_date}.json"
    if not digest_path.exists():
        raise FileNotFoundError(f"YTB_LLM digest was not produced: {digest_path}")
    return digest_path


def load_youtube_digest(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            digest = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise YoutubeFeedError(f"Could not read YouTube digest {path}: {exc}") from exc
    if not isinstance(digest, dict):
        raise YoutubeFeedError(f"YouTube digest is not an object: {path}")
    return digest


def build_youtube_digest(
    bridge: CollectorBridge,
) -> PlatformOutcome:
    """运行外部 YTB_LLM 流程、写入结构化结果并渲染页面，不做状态确认。"""
    print("\n[YouTube 1/4] Run YTB_LLM pipeline")
    try:
        digest_path = run_ytb_pipeline()
    except (OSError, RuntimeError, ValueError, FileNotFoundError) as exc:
        print(f"YouTube/YTB_LLM pipeline failed: {exc}")
        return PlatformOutcome(
            "youtube", STATUS_FAILED, EXIT_CRAWLER, error=str(exc)
        )
    print(f"YouTube digest: {digest_path}")

    print("\n[YouTube 2/4] Map digest picks to structured records")
    try:
        records = youtube_records_from_digest(load_youtube_digest(digest_path))
    except YoutubeFeedError as exc:
        print(str(exc))
        return PlatformOutcome(
            "youtube", STATUS_FAILED, EXIT_UNEXPECTED, error=str(exc)
        )
    if not records:
        print("YTB_LLM digest contains no selected YouTube records")
        return PlatformOutcome("youtube", STATUS_EMPTY, EXIT_NO_DATA)
    print(f"YouTube structured records: {len(records)}")

    print("\n[YouTube 3/4] Write structured results")
    try:
        result_path = append_youtube_results(records)
    except YoutubeResultStoreError as exc:
        print(str(exc))
        return PlatformOutcome(
            "youtube", STATUS_FAILED, EXIT_STORE, error=str(exc)
        )
    print(f"YouTube structured results written to {result_path}")

    print("\n[YouTube 4/4] Update feed page")
    try:
        report_path = render_youtube_feed()
    except (YoutubeFeedError, YoutubeResultStoreError) as exc:
        print(str(exc))
        return PlatformOutcome(
            "youtube", STATUS_FAILED, EXIT_RENDER, error=str(exc)
        )
    print(f"YouTube feed page updated: {report_path}")

    return PlatformOutcome(
        platform="youtube",
        status=STATUS_OK,
        crawled=len(records),
        passed=len(records),
        items=records,
        result_path=result_path,
        report_path=report_path,
        meta={"digest_path": str(digest_path)},
    )


def run_youtube_workflow(bridge: CollectorBridge) -> int:
    """Run YouTube as a structured feed, not the legacy article-report path."""
    outcome = build_youtube_digest(bridge)
    if not outcome.ok:
        return outcome.exit_code

    state_error = bridge.acknowledge()
    if state_error:
        print(f"YouTube state save failed: {state_error}")
        return EXIT_UNEXPECTED

    print(f"YouTube workflow complete: {outcome.item_count} selected records")
    return EXIT_OK
