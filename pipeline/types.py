"""多平台统一运行的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional


@dataclass
class DigestItem:
    """跨平台统一的候选项。

    ``score`` 是**平台内**原始分，只用于平台内排序；跨平台比较一律使用
    ``judge_score``（LLM 复评）或 ``rank_in_platform``，
    因为各平台的分数量纲与含义并不可比。
    """

    platform: str
    uid: str
    title: str
    content: str = ""
    abstract: str = ""
    score: float = 0.0
    rank_in_platform: int = 0
    url: str = ""
    tags: List[str] = field(default_factory=list)
    published_at: Optional[datetime] = None
    metrics: Dict[str, object] = field(default_factory=dict)
    audit: Dict[str, object] = field(default_factory=dict)

    # 逐条研报平台（小红书 / 知乎 / GitHub）才会生成报告
    report_eligible: bool = False
    report_path: Optional[str] = None
    report_url: Optional[str] = None
    download_url: Optional[str] = None

    # LLM 复评结果
    judge_score: Optional[float] = None
    judge_dimensions: List[str] = field(default_factory=list)
    judge_scores: List[float] = field(default_factory=list)
    judge_reasons: List[str] = field(default_factory=list)
    judge_summary: str = ""
    judge_category: str = ""

    # 平台原生条目（生成研报、推送回退时使用）
    source_item: Dict = field(default_factory=dict)

    @property
    def display_score(self) -> float:
        """对外展示的分数：优先使用 LLM 复评分。"""
        if self.judge_score is not None:
            return self.judge_score
        return self.score

    @property
    def link(self) -> str:
        return self.report_url or self.url
