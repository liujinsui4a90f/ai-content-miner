"""阶段函数契约：只做采集/筛选/存储/渲染，不推送、不确认状态。"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from crawler.base import CrawlRunResult
from workflows import reddit, twitter
from workflows.outcome import STATUS_OK


def twitter_item():
    return {
        "id": "x:1",
        "platform_item_id": "1",
        "platform": "x",
        "title": "title",
        "content": "content",
        "filter_metadata": {
            "stages": [],
            "final_decision": "pending",
            "final_reason_codes": [],
        },
        "processed_at": None,
    }


def twitter_bridge():
    return SimpleNamespace(
        platform="x",
        validate=lambda: [],
        run=MagicMock(
            return_value=CrawlRunResult(
                success=True,
                data_files=(Path("current.jsonl"),),
            )
        ),
        acknowledge=MagicMock(return_value=""),
        fetch_replies=MagicMock(),
    )


def reddit_article():
    return {
        "title": "Reddit item",
        "content": "Technical content",
        "url": "https://www.reddit.com/r/test/comments/abc123/example/",
        "raw": {"id": "abc123", "subreddit": "test"},
        "reddit_filter_metadata": {
            "stages": [],
            "final_decision": "keep",
            "final_reason_codes": ["REDDIT_FILTER_PIPELINE_PASSED"],
        },
    }


def reddit_record():
    return {
        "id": "reddit:abc123",
        "platform": "reddit",
        "title": "Reddit item",
        "abstract": "Technical content",
        "source_url": (
            "https://www.reddit.com/r/test/comments/abc123/example/"
        ),
        "subreddit": "test",
        "score": 8.0,
        "filter_metadata": {"final_decision": "keep"},
    }


def reddit_bridge():
    return SimpleNamespace(
        platform="reddit",
        validate=lambda: [],
        run=MagicMock(
            return_value=CrawlRunResult(
                success=True,
                data_files=(Path("current.jsonl"),),
            )
        ),
        acknowledge=MagicMock(return_value=""),
    )


class TwitterStageTest(unittest.TestCase):
    def test_build_digest_does_not_push_or_acknowledge(self):
        item = twitter_item()
        bridge = twitter_bridge()
        selection = {
            "digest_date": "2026-07-29",
            "primary": [item],
            "more": [],
            "selected": [item],
            "archived": [],
            "comment_dropped": [],
        }
        with patch.object(
            twitter.config, "TWITTER_EMBEDDING_ENABLED", False
        ), patch(
            "workflows.twitter.load_twitter_items", return_value=[item]
        ), patch(
            "workflows.twitter.run_twitter_preselection_filters",
            return_value={"all_items": [item], "passed": [item], "dropped": []},
        ), patch(
            "workflows.twitter.load_recent_twitter_digest_items",
            return_value=[],
        ), patch(
            "workflows.twitter.select_twitter_daily_items",
            return_value=selection,
        ), patch(
            "workflows.twitter.enrich_twitter_items", return_value=[item]
        ), patch(
            "workflows.twitter.append_twitter_results",
            return_value=Path("data/processed/x.jsonl"),
        ), patch(
            "workflows.twitter.render_twitter_feed",
            return_value=Path("reports/x.html"),
        ), patch(
            "workflows.twitter.send_twitter_wecom"
        ) as send:
            outcome = twitter.build_twitter_digest(bridge)

        self.assertEqual(outcome.status, STATUS_OK)
        self.assertEqual(outcome.items, [item])
        self.assertEqual(outcome.crawled, 1)
        self.assertEqual(outcome.passed, 1)
        send.assert_not_called()
        bridge.acknowledge.assert_not_called()


class RedditStageTest(unittest.TestCase):
    def test_build_digest_does_not_push_or_acknowledge(self):
        article = reddit_article()
        record = reddit_record()
        bridge = reddit_bridge()
        with patch(
            "workflows.reddit.load_articles", return_value=[article]
        ), patch(
            "workflows.reddit.run_reddit_filters",
            return_value={
                "all_items": [article],
                "passed": [article],
                "dropped": [],
            },
        ), patch(
            "workflows.reddit.enrich_reddit_items", return_value=[article]
        ), patch(
            "workflows.reddit.build_reddit_result", return_value=record
        ), patch(
            "workflows.reddit.append_reddit_results",
            return_value=Path("data/processed/reddit.jsonl"),
        ), patch(
            "workflows.reddit.render_reddit_feed",
            return_value=Path("reports/reddit.html"),
        ), patch("workflows.reddit._print_filter_results"), patch(
            "workflows.reddit.send_reddit_wecom"
        ) as send:
            outcome = reddit.build_reddit_digest(bridge)

        self.assertEqual(outcome.status, STATUS_OK)
        self.assertEqual(outcome.items, [record])
        self.assertEqual(outcome.crawled, 1)
        self.assertEqual(outcome.passed, 1)
        send.assert_not_called()
        bridge.acknowledge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
