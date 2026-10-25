"""平台阶段结果：供单平台入口与多平台编排共用。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


STATUS_OK = "ok"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


@dataclass
class PlatformOutcome:
    """一次平台阶段运行的完整结果。

    ``items`` 保留平台原生结构（不做跨平台转换），便于审计与各平台
    JSONL / 聚合页继续使用；跨平台转换由 ``pipeline.stages`` 负责。
    """

    platform: str
    status: str = STATUS_OK
    exit_code: int = 0
    crawled: int = 0
    passed: int = 0
    items: List[Dict] = field(default_factory=list)
    error: str = ""
    result_path: Path | None = None
    report_path: Path | None = None
    meta: Dict[str, object] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK

    @property
    def item_count(self) -> int:
        return len(self.items)
