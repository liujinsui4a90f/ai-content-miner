"""跨平台日报聚合页。"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import config
from pipeline.stages import platform_label
from pipeline.types import DigestItem

PLATFORM_ORDER = ("xhs", "zhihu", "github", "x", "reddit", "youtube")


def _escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _link(url: str, label: str) -> str:
    if not url.lower().startswith(("http://", "https://")):
        return ""
    return (
        f'<a class="link" href="{_escape(url)}" target="_blank" '
        f'rel="noopener">{_escape(label)}</a>'
    )


def _card(index: int, item: DigestItem) -> str:
    score = item.display_score
    links = " ".join(
        link
        for link in (
            _link(item.report_url or "", "在线阅读研报"),
            _link(item.download_url or "", "下载"),
            _link(item.url, "原文"),
        )
        if link
    )
    tags = "".join(
        f'<span class="tag">{_escape(tag)}</span>' for tag in item.tags[:3]
    )
    return f"""
      <article class="card">
        <div class="rank">{index:02d}</div>
        <div class="body">
          <div class="meta">
            <span class="badge">{_escape(platform_label(item.platform))}</span>
            <span class="score">{score:.1f}<small>/10</small></span>
            {tags}
          </div>
          <h2>{_escape(item.title)}</h2>
          <p>{_escape(item.abstract)}</p>
          <div class="links">{links}</div>
        </div>
      </article>"""


def render_digest_feed(
    items: List[DigestItem],
    digest_date: Optional[str] = None,
    path: Optional[Path] = None,
) -> Path:
    date = digest_date or datetime.now().strftime("%Y-%m-%d")
    target = Path(path or Path(config.REPORT_DIR) / f"digest-{date}.html")
    target.parent.mkdir(parents=True, exist_ok=True)

    grouped = {platform: [] for platform in PLATFORM_ORDER}
    for item in items:
        grouped.setdefault(item.platform, []).append(item)

    sections = []
    for platform in PLATFORM_ORDER:
        bucket = grouped.get(platform) or []
        if not bucket:
            continue
        cards = "".join(
            _card(index, item) for index, item in enumerate(bucket, start=1)
        )
        sections.append(
            f"""
    <section class="platform">
      <h2 class="section-title">{_escape(platform_label(platform))}
        <span>{len(bucket)} 条</span>
      </h2>{cards}
    </section>"""
        )

    counts = " · ".join(
        f"{_escape(platform_label(platform))} {len(grouped[platform])}"
        for platform in PLATFORM_ORDER
        if grouped.get(platform)
    )

    document = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 情报日报 · {_escape(date)}</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 40px 20px 80px;
    background: #0d1117; color: #e6edf3;
    font-family: -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    line-height: 1.6;
  }}
  .wrap {{ max-width: 860px; margin: 0 auto; }}
  header {{ border-bottom: 1px solid #21262d; padding-bottom: 20px; margin-bottom: 28px; }}
  h1 {{ font-size: 26px; margin: 0 0 8px; }}
  .sub {{ color: #8b949e; font-size: 13px; }}
  .section-title {{
    font-size: 16px; color: #58a6ff; margin: 32px 0 12px;
    display: flex; align-items: baseline; gap: 10px;
  }}
  .section-title span {{ font-size: 12px; color: #8b949e; font-weight: 400; }}
  .card {{
    display: flex; gap: 16px; padding: 16px 18px; margin-bottom: 12px;
    background: #161b22; border: 1px solid #21262d; border-radius: 10px;
  }}
  .rank {{ font-size: 22px; color: #30363d; font-weight: 700; min-width: 34px; }}
  .body {{ flex: 1; min-width: 0; }}
  .meta {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 6px; }}
  .badge {{
    font-size: 11px; padding: 2px 8px; border-radius: 999px;
    background: #1f6feb22; color: #58a6ff; border: 1px solid #1f6feb55;
  }}
  .score {{ color: #f0883e; font-weight: 600; }}
  .score small {{ color: #8b949e; font-weight: 400; }}
  .tag {{ font-size: 11px; color: #8b949e; }}
  h2 {{ font-size: 16px; margin: 4px 0 6px; }}
  p {{ margin: 0 0 10px; color: #c9d1d9; font-size: 14px; }}
  .links {{ display: flex; gap: 14px; flex-wrap: wrap; }}
  .link {{ color: #58a6ff; text-decoration: none; font-size: 13px; }}
  .link:hover {{ text-decoration: underline; }}
  footer {{ margin-top: 48px; color: #484f58; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>AI 情报日报</h1>
      <div class="sub">{_escape(date)} · 共 {len(items)} 条 · {counts or "无内容"}</div>
    </header>
    {"".join(sections) or '<p class="sub">今日没有可选内容。</p>'}
    <footer>由 ai-content-miner 多平台统一流程生成</footer>
  </div>
</body>
</html>
"""
    target.write_text(document, encoding="utf-8")
    return target
