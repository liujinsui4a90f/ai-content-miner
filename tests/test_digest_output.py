"""混合日报的推送分片与跨平台条目转换。"""

import unittest

from notifier.digest_wecom import build_messages
from pipeline.stages import to_digest_items
from pipeline.types import DigestItem
from workflows.outcome import STATUS_OK, PlatformOutcome


def make_item(index, platform="xhs", abstract="摘要" * 40, url="https://example.com/a"):
    return DigestItem(
        platform=platform,
        uid=f"{platform}:{index}",
        title=f"标题{index}",
        abstract=abstract,
        score=8.0,
        url=url,
        report_url="https://cos.example.com/report.html",
        judge_score=7.5,
    )


class DigestMessageTest(unittest.TestCase):
    def test_each_message_stays_within_byte_limit(self):
        items = [make_item(index) for index in range(20)]
        messages = build_messages(items, digest_date="2026-10-25", max_bytes=1200)
        self.assertGreater(len(messages), 1)
        for message in messages:
            self.assertLessEqual(len(message.encode("utf-8")), 1200)

    def test_all_items_survive_when_they_fit(self):
        items = [make_item(index) for index in range(5)]
        messages = build_messages(items, digest_date="2026-10-25")
        joined = "\n".join(messages)
        for index in range(5):
            self.assertIn(f"标题{index}", joined)

    def test_header_reports_counts_and_date(self):
        items = [make_item(1, "xhs"), make_item(2, "reddit")]
        messages = build_messages(items, digest_date="2026-10-25")
        self.assertIn("2026-10-25", messages[0])
        self.assertIn("共 2 条", messages[0])
        self.assertIn("小红书 1", messages[0])
        self.assertIn("Reddit 1", messages[0])

    def test_non_http_urls_are_dropped(self):
        item = make_item(1)
        item.url = "javascript:alert(1)"
        item.report_url = ""
        message = build_messages([item], digest_date="2026-10-25")[0]
        self.assertNotIn("javascript", message)
        self.assertNotIn("原文", message)

    def test_brackets_in_title_are_neutralized(self):
        item = make_item(1)
        item.title = "标题[测试]"
        message = build_messages([item], digest_date="2026-10-25")[0]
        self.assertIn("【测试】", message)


class DigestConversionTest(unittest.TestCase):
    def test_twitter_items_use_publication_score(self):
        outcome = PlatformOutcome(
            "x",
            STATUS_OK,
            items=[
                {
                    "id": "x:1",
                    "title": "tweet",
                    "content": "body",
                    "abstract": "abs",
                    "source_url": "https://x.com/a/1",
                    "tags": ["AI"],
                    "publication_metadata": {"score": 42.5},
                }
            ],
        )
        items = to_digest_items(outcome)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].platform, "x")
        self.assertEqual(items[0].score, 42.5)
        self.assertFalse(items[0].report_eligible)
        self.assertEqual(items[0].rank_in_platform, 1)

    def test_reddit_items_mark_subreddit(self):
        outcome = PlatformOutcome(
            "reddit",
            STATUS_OK,
            items=[
                {
                    "id": "reddit:abc",
                    "title": "post",
                    "abstract": "abs",
                    "source_url": "https://reddit.com/r/x/abc",
                    "subreddit": "LocalLLaMA",
                    "score": 7.2,
                }
            ],
        )
        items = to_digest_items(outcome)
        self.assertEqual(items[0].tags, ["LocalLLaMA"])
        self.assertEqual(items[0].score, 7.2)

    def test_report_platforms_are_marked_eligible(self):
        outcome = PlatformOutcome(
            "zhihu",
            STATUS_OK,
            items=[
                {
                    "article": {
                        "title": "长文",
                        "content": "正文",
                        "url": "https://zhuanlan.zhihu.com/p/1",
                    },
                    "total_score": 6.4,
                    "summary": "摘要",
                    "category": "论文解读",
                }
            ],
        )
        items = to_digest_items(outcome)
        self.assertTrue(items[0].report_eligible)
        self.assertEqual(items[0].score, 6.4)
        self.assertEqual(items[0].display_score, 6.4)


if __name__ == "__main__":
    unittest.main()
