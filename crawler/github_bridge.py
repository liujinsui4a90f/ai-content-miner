"""GitHub repository discovery bridge using the GitHub REST API."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from uuid import uuid4

import config
from crawler.base import CrawlRunResult

import time

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_PLATFORMS = {"github", "gh"}
API_VERSION = "2022-11-28"


def _resolve_project_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


class GithubApiError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GithubBridge:
    """Search public repositories and emit one normalized JSONL file."""

    def __init__(
        self,
        session: Optional[object] = None,
        now_provider: Optional[Callable[[], datetime]] = None,
        platform: Optional[str] = None,
    ) -> None:
        self.platform = "github"
        self.configured_platform = str(
            platform or getattr(config, "CRAWL_PLATFORM", "")
        ).strip().lower()
        self.token = str(
            os.environ.get("GITHUB_TOKEN", getattr(config, "GITHUB_TOKEN", ""))
        ).strip()
        self.api_base_url = str(
            getattr(config, "GITHUB_API_BASE_URL", "https://api.github.com")
        ).rstrip("/")
        configured_keywords = getattr(
            config,
            "GITHUB_SEARCH_KEYWORDS",
            getattr(config, "CONTENT_SEARCH_KEYWORDS", config.SEARCH_KEYWORDS),
        )
        self.keywords = [
            str(keyword).strip()
            for keyword in configured_keywords
            if str(keyword).strip()
        ]
        self.limit = int(getattr(config, "CRAWL_LIMIT", 20))
        self.per_query_limit = int(
            getattr(config, "GITHUB_RESULTS_PER_QUERY", 20)
        )
        self.lookback_days = float(getattr(config, "GITHUB_LOOKBACK_DAYS", 7))
        self.min_stars = int(getattr(config, "GITHUB_MIN_STARS", 0))
        self.timeout = float(getattr(config, "GITHUB_TIMEOUT_SECONDS", 30))
        self.request_max_retries = int(
            getattr(config, "GITHUB_REQUEST_MAX_RETRIES", 3)
        )
        self.readme_max_chars = int(
            getattr(config, "GITHUB_README_MAX_CHARS", 6000)
        )
        self.code_evidence_max_chars = int(
            getattr(config, "GITHUB_CODE_EVIDENCE_MAX_CHARS", 9000)
        )
        self.code_evidence_max_files = int(
            getattr(config, "GITHUB_CODE_EVIDENCE_MAX_FILES", 3)
        )
        self.state_file = _resolve_project_path(
            getattr(
                config,
                "GITHUB_STATE_FILE",
                os.path.join(config.DATA_DIR, "state", "github_seen_ids.json"),
            )
        )
        self.seen_id_limit = int(getattr(config, "GITHUB_SEEN_ID_LIMIT", 5000))
        self.target_data_dir = _resolve_project_path(config.DATA_DIR)
        self._session = session
        self._now_provider = now_provider or (lambda: datetime.now(timezone.utc))
        self._pending_ids: Tuple[str, ...] = ()

    def validate(self) -> List[str]:
        errors: List[str] = []
        if self.configured_platform not in SUPPORTED_PLATFORMS:
            errors.append(
                f"CRAWL_PLATFORM={self.configured_platform!r} 不能由 GithubBridge 处理；"
                "请使用 github"
            )
        if not self.token or self.token.lower() in {
            "your-github-token-here",
            "your-token",
            "ghp_xxxxx",
        }:
            errors.append("GITHUB_TOKEN 未配置或仍是占位值")
        if not self.keywords:
            errors.append("SEARCH_KEYWORDS 必须至少包含一个非空关键词")
        if self.limit <= 0:
            errors.append("CRAWL_LIMIT 必须是正整数")
        if self.per_query_limit <= 0 or self.per_query_limit > 100:
            errors.append("GITHUB_RESULTS_PER_QUERY 必须在 1 到 100 之间")
        if self.lookback_days <= 0:
            errors.append("GITHUB_LOOKBACK_DAYS 必须大于 0")
        if self.min_stars < 0:
            errors.append("GITHUB_MIN_STARS 不能小于 0")
        if self.timeout <= 0:
            errors.append("GITHUB_TIMEOUT_SECONDS 必须大于 0")
        if self.readme_max_chars < 0:
            errors.append("GITHUB_README_MAX_CHARS 不能小于 0")
        if self.code_evidence_max_chars < 0:
            errors.append("GITHUB_CODE_EVIDENCE_MAX_CHARS 不能小于 0")
        if self.code_evidence_max_files < 0:
            errors.append("GITHUB_CODE_EVIDENCE_MAX_FILES 不能小于 0")
        if self.seen_id_limit <= 0:
            errors.append("GITHUB_SEEN_ID_LIMIT 必须是正整数")
        if requests is None:
            errors.append(
                "缺少 requests；请运行 python3 -m pip install -r requirements.txt"
            )
        try:
            self._load_seen_ids()
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"GitHub 状态文件无效：{exc}")
        return errors

    def run(self) -> CrawlRunResult:
        errors = self.validate()
        if errors:
            return CrawlRunResult(success=False, error="；".join(errors))

        run_id = f"{datetime.now():%Y%m%dT%H%M%S%f}-{uuid4().hex[:8]}"
        output_dir = self.target_data_dir / "crawler_runs" / run_id
        output_file = (
            output_dir
            / self.platform
            / "jsonl"
            / f"search_repositories_{datetime.now():%Y-%m-%d}.jsonl"
        )
        self._pending_ids = ()
        cutoff = self._now_provider() - timedelta(days=self.lookback_days)
        seen_ids = set(self._load_seen_ids())
        candidates: Dict[str, Dict[str, Any]] = {}

        try:
            for keyword in self.keywords:
                response = self._request(
                    "GET",
                    f"{self.api_base_url}/search/repositories",
                    params={
                        "q": f"{keyword} pushed:>={cutoff.date().isoformat()}",
                        "sort": "updated",
                        "order": "desc",
                        "per_page": self.per_query_limit,
                        "page": 1,
                    },
                )
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(
                    payload.get("items"), list
                ):
                    raise ValueError("GitHub 搜索响应缺少 items 列表")
                for item in payload["items"]:
                    candidate = self._candidate_from_item(item, keyword, cutoff)
                    if candidate is None:
                        continue
                    repository_id = candidate["repository_id"]
                    if candidate["id"] in seen_ids:
                        continue
                    existing = candidates.get(repository_id)
                    if existing:
                        if keyword not in existing["matched_keywords"]:
                            existing["matched_keywords"].append(keyword)
                    else:
                        candidates[repository_id] = candidate

            ordered = sorted(
                candidates.values(),
                key=lambda item: (item["pushed_at"], item["stars"]),
                reverse=True,
            )[: self.limit]
            if not ordered:
                return CrawlRunResult(
                    success=False,
                    output_dir=output_dir,
                    error="GitHub 搜索完成，但没有新的符合条件的仓库",
                )

            rows = []
            for candidate in ordered:
                candidate["readme"] = self._fetch_readme(candidate["full_name"])
                code_evidence, evidence_manifest = self._fetch_code_evidence(candidate)
                candidate["code_evidence"] = code_evidence
                candidate["evidence_manifest"] = evidence_manifest
                rows.append(self._to_row(candidate))
        except GithubApiError as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"GitHub API 失败：{exc}",
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"GitHub 数据处理失败：{type(exc).__name__}: {exc}",
            )

        try:
            output_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_file = output_file.with_suffix(".jsonl.tmp")
            with temporary_file.open("w", encoding="utf-8") as file_obj:
                for row in rows:
                    file_obj.write(json.dumps(row, ensure_ascii=False) + "\n")
            temporary_file.replace(output_file)
        except OSError as exc:
            return CrawlRunResult(
                success=False,
                output_dir=output_dir,
                error=f"GitHub 数据写入失败：{exc}",
            )

        self._pending_ids = tuple(row["id"] for row in rows)
        return CrawlRunResult(
            success=True,
            data_files=(output_file,),
            output_dir=output_dir,
            returncode=0,
        )

    def acknowledge(self) -> str:
        if not self._pending_ids:
            return ""
        try:
            existing = self._load_seen_ids()
            ordered = list(existing)
            ordered.extend(
                item_id for item_id in self._pending_ids if item_id not in existing
            )
            ordered = ordered[-self.seen_id_limit :]
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temporary_file = self.state_file.with_suffix(
                self.state_file.suffix + ".tmp"
            )
            temporary_file.write_text(
                json.dumps(
                    {"version": 1, "seen_ids": ordered},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            temporary_file.replace(self.state_file)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return f"无法保存 GitHub 已处理状态：{exc}"
        self._pending_ids = ()
        return ""

    def _candidate_from_item(
        self, item: Dict[str, Any], keyword: str, cutoff: datetime
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        repository_id = str(item.get("id", "")).strip()
        full_name = str(item.get("full_name", "")).strip()
        pushed_at = self._parse_datetime(item.get("pushed_at"))
        stars = int(item.get("stargazers_count", 0) or 0)
        if not repository_id or not full_name or pushed_at is None:
            return None
        if pushed_at < cutoff or stars < self.min_stars:
            return None
        return {
            "id": f"github:{repository_id}",
            "repository_id": repository_id,
            "full_name": full_name,
            "description": str(item.get("description", "") or "").strip(),
            "html_url": str(item.get("html_url", "") or "").strip(),
            "clone_url": str(item.get("clone_url", "") or "").strip(),
            "owner": (item.get("owner") or {}).get("login", ""),
            "pushed_at": pushed_at.isoformat(),
            "stars": stars,
            "forks": int(item.get("forks_count", 0) or 0),
            "watchers": int(item.get("watchers_count", 0) or 0),
            "open_issues": int(item.get("open_issues_count", 0) or 0),
            "language": item.get("language"),
            "topics": list(item.get("topics") or []),
            "license": (item.get("license") or {}).get("spdx_id"),
            "archived": bool(item.get("archived", False)),
            "disabled": bool(item.get("disabled", False)),
            "fork": bool(item.get("fork", False)),
            "matched_keywords": [keyword],
            "readme": "",
            "default_branch": str(item.get("default_branch", "main") or "main"),
        }

    def _fetch_readme(self, full_name: str) -> str:
        if self.readme_max_chars == 0:
            return ""
        try:
            response = self._request(
                "GET",
                f"{self.api_base_url}/repos/{full_name}/readme",
                headers={"Accept": "application/vnd.github.raw+json"},
            )
        except GithubApiError as exc:
            if exc.status_code == 404:
                return ""
            raise
        return response.text[: self.readme_max_chars]

    def _fetch_code_evidence(
        self, candidate: Dict[str, Any]
    ) -> Tuple[str, List[str]]:
        """Read a small, prioritized sample of repository source files."""
        if self.code_evidence_max_chars == 0 or self.code_evidence_max_files == 0:
            return "", []

        full_name = candidate["full_name"]
        branch = candidate.get("default_branch") or "main"
        try:
            response = self._request(
                "GET",
                f"{self.api_base_url}/repos/{full_name}/git/trees/{branch}",
                params={"recursive": "1"},
            )
        except GithubApiError as exc:
            # Empty repositories and stale default branches have no tree to read.
            if exc.status_code in {404, 409}:
                return "", []
            raise
        payload = response.json()
        tree = payload.get("tree", []) if isinstance(payload, dict) else []
        paths = [
            str(node.get("path", ""))
            for node in tree
            if isinstance(node, dict)
            and node.get("type") == "blob"
            and str(node.get("path", "")).lower().endswith(
                (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".yaml", ".yml", ".json")
            )
        ]
        ignored = ("node_modules/", ".git/", "dist/", "build/", "vendor/", "test", "spec")
        paths = [path for path in paths if not path.lower().startswith(ignored)]

        def priority(path: str) -> Tuple[int, int, str]:
            name = path.rsplit("/", 1)[-1].lower()
            preferred = {
                "main.py": 0, "app.py": 1, "server.py": 2,
                "main.ts": 3, "index.ts": 4, "index.js": 5,
                "config.py": 6, "settings.py": 7,
            }
            return preferred.get(name, 20), path.count("/"), path

        selected = sorted(paths, key=priority)[: self.code_evidence_max_files]
        chunks: List[str] = []
        manifest: List[str] = []
        remaining = self.code_evidence_max_chars
        for path in selected:
            if remaining <= 0:
                break
            try:
                file_response = self._request(
                    "GET",
                    f"{self.api_base_url}/repos/{full_name}/contents/{path}",
                    params={"ref": branch},
                    headers={"Accept": "application/vnd.github.raw+json"},
                )
                text = file_response.text[:remaining]
            except GithubApiError as exc:
                if exc.status_code == 404:
                    continue
                raise
            if not text.strip():
                continue
            chunks.append(f"\n===== {path} =====\n{text}")
            manifest.append(path)
            remaining -= len(text)
        return "".join(chunks), manifest

    def _to_row(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        description = candidate["description"]
        readme = candidate["readme"]
        code_evidence = candidate.get("code_evidence", "")
        content = "\n\n".join(
            part for part in (description, readme, code_evidence) if part
        )
        return {
            "id": candidate["id"],
            "repository_id": candidate["repository_id"],
            "title": candidate["full_name"],
            "content": content,
            "description": description,
            "readme": readme,
            "code_evidence": code_evidence,
            "evidence_manifest": candidate.get("evidence_manifest", []),
                        "source": "GitHub",
            "url": candidate["html_url"],
            "clone_url": candidate["clone_url"],
            "author": candidate["owner"],
            "owner": candidate["owner"],
            "publish_time": candidate["pushed_at"],
            "pushed_at": candidate["pushed_at"],
            "stars": candidate["stars"],
            "stargazers_count": candidate["stars"],
            "forks": candidate["forks"],
            "forks_count": candidate["forks"],
            "watchers": candidate["watchers"],
            "watchers_count": candidate["watchers"],
            "open_issues": candidate["open_issues"],
            "open_issues_count": candidate["open_issues"],
            "language": candidate["language"],
            "topics": candidate["topics"],
            "license": candidate["license"],
            "archived": candidate["archived"],
            "disabled": candidate["disabled"],
            "fork": candidate["fork"],
            "matched_keywords": candidate["matched_keywords"],
        }

    def _request(self, method: str, url: str, **kwargs: Any) -> Any:
        if requests is None:
            raise GithubApiError("requests 未安装")
        headers = self._headers()
        headers.update(kwargs.pop("headers", {}))
        kwargs.setdefault("timeout", self.timeout)
        for attempt in range(1, self.request_max_retries + 1):
            try:
                response = self._get_session().request(
                    method, url, headers=headers, **kwargs
                )
                break
            except requests.RequestException as exc:
                if attempt == self.request_max_retries:
                    raise GithubApiError(
                        f"网络请求失败：{type(exc).__name__}: {exc}"
                    ) from exc
                delay = attempt
                print(
                    f"   GitHub 请求中断（第 {attempt} 次）："
                    f"{type(exc).__name__}；{delay} 秒后重试"
                )
                time.sleep(delay)
        if response.status_code in {403, 429}:
            retry_after = response.headers.get("Retry-After", "")
            detail = "GitHub API 限流"
            if retry_after:
                detail += f"；Retry-After={retry_after}"
            raise GithubApiError(detail, response.status_code)
        if response.status_code >= 400:
            raise GithubApiError(
                f"HTTP {response.status_code}: {response.text[:300]}",
                response.status_code,
            )
        return response

    def _headers(self) -> Dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "Authorization": f"Bearer {self.token}",
        }

    def _get_session(self) -> object:
        if self._session is None:
            self._session = requests.Session()
        return self._session

    def _load_seen_ids(self) -> List[str]:
        if not self.state_file.exists():
            return []
        data = json.loads(self.state_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1:
            raise ValueError("不支持的 GitHub 状态文件格式")
        seen_ids = data.get("seen_ids")
        if not isinstance(seen_ids, list) or any(
            not isinstance(item_id, str) for item_id in seen_ids
        ):
            raise ValueError("seen_ids 必须是字符串列表")
        return seen_ids

    @staticmethod
    def _parse_datetime(value: object) -> Optional[datetime]:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
