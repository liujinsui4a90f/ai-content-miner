"""平台采集适配层：从采集结果到统一候选的接线。"""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from analyzer.filter import FilterResult
from crawler.base import CrawlRunResult
from pipeline import stages
from workflows.outcome import STATUS_OK


class FakeBridge:
    def __init__(self, data_file):
        self.platform = "zhihu"
        self.configured_platform = "zhihu"
        self.data_file = data_file
        self.acknowledged = False

    def validate(self):
        return []

    def run(self):
        return CrawlRunResult(success=True, data_files=(self.data_file,))

    def acknowledge(self):
        self.acknowledged = True
        return ""


class StageCollectTest(unittest.TestCase):
    def setUp(self):
        # 直接写在临时目录根下：部分沙箱不允许在新建子目录里写文件。
        self.data_file = (
            Path(tempfile.gettempdir()) / f"acm-stage-{uuid4().hex}.jsonl"
        )
        self.addCleanup(lambda: self.data_file.unlink(missing_ok=True))

    def write_records(self, records):
        self.data_file.write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

    def zhihu_record(self, title="大模型推理优化实践", content="正文" * 300):
        return {
            "content_id": "1",
            "content_type": "article",
            "title": title,
            "content_text": content,
            "content_url": "https://zhuanlan.zhihu.com/p/1",
            "created_time": int(time.time()) - 3600,
            "voteup_count": 10,
            "comment_count": 2,
        }

    def test_generic_stage_reuses_production_filters(self):
        self.write_records([self.zhihu_record()])
        bridge = FakeBridge(self.data_file)

        with patch(
            "analyzer.filter.topic_embedding_filter",
            return_value=FilterResult(semantic_score=1.12),
        ), patch(
            "analyzer.filter.comment_analysis",
            return_value=FilterResult(comment_score=0.8),
        ), patch(
            "analyzer.filter.author_profile_analysis",
            return_value=FilterResult(author_score=1.0),
        ):
            outcome = stages.collect_platform("zhihu", bridge)

        self.assertEqual(outcome.status, STATUS_OK)
        self.assertEqual(outcome.crawled, 1)
        self.assertEqual(outcome.passed, 1)
        self.assertEqual(outcome.items[0]["article"]["title"], "大模型推理优化实践")
        self.assertGreater(outcome.items[0]["total_score"], 0)

    def test_generic_stage_marks_empty_when_time_window_excludes_all(self):
        record = self.zhihu_record()
        record["created_time"] = int(time.time()) - 3600 * 24 * 30
        self.write_records([record])
        bridge = FakeBridge(self.data_file)

        outcome = stages.collect_platform("zhihu", bridge)

        self.assertEqual(outcome.status, "empty")
        self.assertEqual(outcome.items, [])

    def test_unknown_platform_is_reported_not_raised(self):
        outcome = stages.collect_platform("weibo", FakeBridge(self.data_file))
        self.assertEqual(outcome.status, "failed")
        self.assertIn("不支持", outcome.error)


if __name__ == "__main__":
    unittest.main()
