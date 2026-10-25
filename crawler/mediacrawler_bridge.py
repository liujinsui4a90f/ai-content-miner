"""MediaCrawler 命令行桥接器。

当前实现对齐 MediaCrawler commit
``c9a111be73586bdf6fc44536f088e4db6ed86d64``，仅接入本项目实际消费的
小红书和知乎搜索内容。所有运行参数都通过 CLI 传递，不修改 MediaCrawler
仓库中的配置文件。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Sequence
from uuid import uuid4

import config
from crawler.base import CrawlRunResult


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PLATFORMS = {"xhs", "zhihu"}
SUPPORTED_CRAWL_TYPES = {"search"}
SUPPORTED_LOGIN_TYPES = {"qrcode", "phone"}


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


class MediaCrawlerBridge:
    """校验、启动 MediaCrawler，并返回本次运行产生的内容文件。"""

    def __init__(
        self,
        command_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
        platform: Optional[str] = None,
    ) -> None:
        self.base_path = _resolve_project_path(config.MEDIACRAWLER_PATH)
        self.configured_platform = str(platform or config.CRAWL_PLATFORM).strip()
        self.platform = self._platform_mapping(self.configured_platform)
        self.crawl_type = config.CRAWL_TYPE
        self.search_specs = self._build_search_specs()
        self.keywords = [spec["query"] for spec in self.search_specs]
        self.limit = getattr(
            config, "MEDIACRAWLER_LIMIT", getattr(config, "CRAWL_LIMIT", 20)
        )
        self.login_type = getattr(config, "MEDIACRAWLER_LOGIN_TYPE", "qrcode")
        self.expected_commit = getattr(config, "MEDIACRAWLER_COMMIT", "")
        self.timeout = getattr(config, "MEDIACRAWLER_TIMEOUT_SECONDS", 900)
        self.target_data_dir = _resolve_project_path(config.DATA_DIR)
        self.python_executable = getattr(config, "MEDIACRAWLER_PYTHON", "").strip()
        self._command_runner = command_runner

    def validate(self) -> List[str]:
        """返回阻止本次爬取启动的配置错误。"""
        errors: List[str] = []

        if self.platform not in SUPPORTED_PLATFORMS:
            errors.append(
                f"CRAWL_PLATFORM={self.configured_platform!r} 不受支持；"
                "当前仅支持 xhs、zhihu"
            )
        if self.crawl_type not in SUPPORTED_CRAWL_TYPES:
            errors.append(
                f"CRAWL_TYPE={self.crawl_type!r} 尚未接入；当前仅支持 search"
            )
        if self.login_type not in SUPPORTED_LOGIN_TYPES:
            errors.append(
                f"MEDIACRAWLER_LOGIN_TYPE={self.login_type!r} 尚未安全接入；"
                "当前仅支持 qrcode、phone"
            )
        if not self.search_specs:
            errors.append("CONTENT_SEARCH_KEYWORDS 与 CONTENT_SEARCH_INTENTS 不能都为空")
        if not isinstance(self.limit, int) or self.limit <= 0:
            errors.append("CRAWL_LIMIT 必须是正整数")
        if not isinstance(self.timeout, (int, float)) or self.timeout <= 0:
            errors.append("MEDIACRAWLER_TIMEOUT_SECONDS 必须大于 0")

        if not self.base_path.is_dir():
            errors.append(f"MediaCrawler 目录不存在：{self.base_path}")
            return errors

        for required_file in ("main.py", "pyproject.toml", "uv.lock"):
            if not (self.base_path / required_file).is_file():
                errors.append(
                    f"MediaCrawler 缺少 {required_file}：{self.base_path / required_file}"
                )

        actual_commit = self._read_actual_commit()
        if not actual_commit:
            errors.append("无法读取 MediaCrawler git commit")
        elif self.expected_commit and actual_commit != self.expected_commit:
            errors.append(
                "MediaCrawler 版本不匹配："
                f"期望 {self.expected_commit}，实际 {actual_commit}"
            )

        runtime_error = self._runtime_error()
        if runtime_error:
            errors.append(runtime_error)

        node_error = self._node_runtime_error()
        if node_error:
            errors.append(node_error)

        return errors

    def build_command(self, output_dir: Path) -> List[str]:
        """构造与已对齐 MediaCrawler CLI 一致的命令。"""
        runtime_prefix = self._runtime_prefix()
        return [
            *runtime_prefix,
            "main.py",
            "--platform",
            self.platform,
            "--lt",
            self.login_type,
            "--type",
            self.crawl_type,
            "--keywords",
            ",".join(self.keywords),
            "--crawler_max_notes_count",
            str(self.limit),
            "--save_data_option",
            "jsonl",
            "--save_data_path",
            str(output_dir),
            "--get_comment",
            "false",
            "--get_sub_comment",
            "false",
        ]

    def run(self) -> CrawlRunResult:
        """运行一次爬虫；任何配置、进程或数据失败都会返回失败结果。"""
        errors = self.validate()
        if errors:
            return CrawlRunResult(success=False, error="；".join(errors))

        run_id = f"{datetime.now():%Y%m%dT%H%M%S%f}-{uuid4().hex[:8]}"
        output_dir = self.target_data_dir / "crawler_runs" / run_id
        command = self.build_command(output_dir)

        print(f"   📡 工作目录: {self.base_path}")
        print(f"   📂 本次输出: {output_dir}")
        print(f"   📡 执行: {' '.join(command)}")

        try:
            completed = self._command_runner(
                command,
                cwd=self.base_path,
                timeout=self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CrawlRunResult(
                success=False,
                command=tuple(command),
                output_dir=output_dir,
                error=f"MediaCrawler 执行超时（{self.timeout} 秒）",
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CrawlRunResult(
                success=False,
                command=tuple(command),
                output_dir=output_dir,
                error=f"MediaCrawler 启动失败：{exc}",
            )

        if completed.returncode != 0:
            diagnostics = self._diagnostics(completed)
            error = f"MediaCrawler 退出码为 {completed.returncode}"
            if diagnostics:
                error += f"；子进程输出：{diagnostics}"
            error += (
                "。请检查 MediaCrawler 登录状态、验证码/风控响应、"
                "当前 IP 网络和请求频率"
            )
            return CrawlRunResult(
                success=False,
                command=tuple(command),
                output_dir=output_dir,
                returncode=completed.returncode,
                error=error,
            )

        data_files = self._find_content_files(output_dir)
        if not data_files:
            return CrawlRunResult(
                success=False,
                command=tuple(command),
                output_dir=output_dir,
                returncode=completed.returncode,
                error="MediaCrawler 正常退出，但本次运行没有产生内容数据",
            )

        return CrawlRunResult(
            success=True,
            data_files=tuple(data_files),
            command=tuple(command),
            output_dir=output_dir,
            returncode=completed.returncode,
        )

    @staticmethod
    def _diagnostics(completed: subprocess.CompletedProcess) -> str:
        """Return a bounded child-process tail without flooding the workflow log."""
        parts = []
        for value in (getattr(completed, "stdout", ""), getattr(completed, "stderr", "")):
            if value:
                parts.extend(str(value).splitlines())
        return " | ".join(line.strip() for line in parts[-12:] if line.strip())[-3000:]

    def acknowledge(self) -> str:
        """MediaCrawler 不维护额外的本地已处理状态。"""
        return ""

    @staticmethod
    def _build_search_specs() -> List[dict]:
        topics = getattr(config, "CONTENT_SEARCH_KEYWORDS", [])
        intents = getattr(config, "CONTENT_SEARCH_INTENTS", {})
        if isinstance(topics, str):
            topics = [topics]
        specs: List[dict] = []
        seen = set()
        for topic in topics if isinstance(topics, (list, tuple)) else []:
            base = str(topic or "").strip()
            if not base:
                continue
            for group, values in intents.items() if isinstance(intents, dict) else []:
                for value in values if isinstance(values, (list, tuple)) else []:
                    intent = str(value or "").strip()
                    query = f"{base} {intent}".strip()
                    key = query.casefold()
                    if intent and key not in seen:
                        seen.add(key)
                        specs.append({
                            "base_keyword": base,
                            "intent_group": str(group),
                            "intent_keyword": intent,
                            "query": query,
                        })
        # Round-robin by topic so a cap does not starve later topics.
        grouped = {}
        for spec in specs:
            grouped.setdefault(spec["base_keyword"], []).append(spec)
        balanced = []
        while any(grouped.values()):
            for topic in list(grouped):
                if grouped[topic]:
                    balanced.append(grouped[topic].pop(0))
        maximum = int(getattr(config, "CONTENT_SEARCH_MAX_QUERIES", 90))
        return balanced[:maximum] if maximum > 0 else balanced
    def _find_content_files(self, output_dir: Path) -> List[Path]:
        content_dir = output_dir / self.platform / "jsonl"
        pattern = f"{self.crawl_type}_contents_*.jsonl"
        return sorted(
            path
            for path in content_dir.glob(pattern)
            if path.is_file() and path.stat().st_size > 0
        )

    def _runtime_prefix(self) -> List[str]:
        if self.python_executable:
            executable = self._resolve_python_executable(self.python_executable)
            if executable:
                return [executable]
            return [self.python_executable]

        uv_path = shutil.which("uv")
        if uv_path:
            return [uv_path, "run"]

        for candidate in self._venv_python_candidates():
            if candidate.is_file():
                return [str(candidate)]

        return [sys.executable]

    def _runtime_error(self) -> str:
        prefix = self._runtime_prefix()
        if (
            len(prefix) == 2
            and Path(prefix[0]).stem == "uv"
            and prefix[1] == "run"
        ):
            return ""

        executable = prefix[0]
        resolved = self._resolve_python_executable(executable)
        if not resolved:
            return f"找不到 MediaCrawler Python 解释器：{executable}"

        try:
            completed = subprocess.run(
                [
                    resolved,
                    "-c",
                    "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"无法检查 MediaCrawler Python 解释器：{exc}"

        if completed.returncode != 0:
            return f"MediaCrawler Python 解释器不可用：{resolved}"

        try:
            major, minor = (int(part) for part in completed.stdout.strip().split(".")[:2])
        except (TypeError, ValueError):
            return f"无法识别 MediaCrawler Python 版本：{completed.stdout.strip()!r}"

        if (major, minor) < (3, 11):
            return (
                f"MediaCrawler commit {self.expected_commit or '当前版本'} "
                "要求 Python >=3.11，"
                f"当前解释器为 {major}.{minor}"
            )
        return ""

    def _read_actual_commit(self) -> str:
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.base_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _node_runtime_error(self) -> str:
        """知乎实现依赖 Node.js；按上游说明要求版本不低于 16。"""
        if self.platform != "zhihu":
            return ""

        node_path = shutil.which("node")
        if not node_path:
            return "知乎爬取需要 Node.js >=16，但当前未找到 node"
        try:
            completed = subprocess.run(
                [node_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"无法检查 Node.js：{exc}"

        version = completed.stdout.strip().lstrip("v")
        try:
            major = int(version.split(".", 1)[0])
        except (TypeError, ValueError):
            return f"无法识别 Node.js 版本：{completed.stdout.strip()!r}"
        if completed.returncode != 0 or major < 16:
            return f"知乎爬取需要 Node.js >=16，当前版本为 {version or '未知'}"
        return ""

    def _venv_python_candidates(self) -> Sequence[Path]:
        if sys.platform == "win32":
            return (
                self.base_path / ".venv" / "Scripts" / "python.exe",
                self.base_path / "venv" / "Scripts" / "python.exe",
            )
        return (
            self.base_path / ".venv" / "bin" / "python",
            self.base_path / "venv" / "bin" / "python",
        )

    @staticmethod
    def _resolve_python_executable(value: str) -> Optional[str]:
        if not value:
            return None
        if Path(value).expanduser().is_file():
            return str(Path(value).expanduser().resolve())
        return shutil.which(value)

    @staticmethod
    def _platform_mapping(platform: str) -> str:
        mapping = {
            "xiaohongshu": "xhs",
            "xhs": "xhs",
            "zhihu": "zhihu",
        }
        return mapping.get(platform, platform)
