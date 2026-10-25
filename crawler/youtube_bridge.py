"""Bridge metadata for the sibling YTB_LLM YouTube pipeline."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional

import config
from crawler.base import CrawlRunResult


REQUIRED_YTB_SCRIPTS = (
    "daily_fetch.py",
    "relevance_gate.py",
    "score_and_select.py",
)


class YoutubeBridge:
    """Validate the external YTB_LLM project used by the YouTube workflow."""

    def __init__(self, platform: Optional[str] = None) -> None:
        self.platform = "youtube"
        self.configured_platform = str(
            platform or config.CRAWL_PLATFORM
        ).strip().casefold()
        self.ytb_llm_path = Path(config.YOUTUBE_YTB_LLM_PATH).expanduser()
        self.python = str(config.YOUTUBE_PYTHON or "").strip()

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.configured_platform != "youtube":
            errors.append(
                f"CRAWL_PLATFORM={self.configured_platform!r} cannot be handled "
                "by the YouTube workflow; use youtube"
            )
        if not self.python:
            errors.append("YOUTUBE_PYTHON must be configured")
        elif "/" in self.python and not Path(self.python).expanduser().exists():
            errors.append(f"YOUTUBE_PYTHON does not exist: {self.python}")
        elif "/" not in self.python and shutil.which(self.python) is None:
            errors.append(f"YOUTUBE_PYTHON was not found on PATH: {self.python}")
        if not self.ytb_llm_path.exists() or not self.ytb_llm_path.is_dir():
            errors.append(f"YOUTUBE_YTB_LLM_PATH is not a directory: {self.ytb_llm_path}")
        else:
            for script in REQUIRED_YTB_SCRIPTS:
                if not (self.ytb_llm_path / script).is_file():
                    errors.append(f"YTB_LLM script missing: {self.ytb_llm_path / script}")
        return errors

    def run(self) -> CrawlRunResult:
        return CrawlRunResult(
            success=False,
            error="YouTube collection is orchestrated by workflows.youtube",
        )

    def acknowledge(self) -> str:
        return ""
