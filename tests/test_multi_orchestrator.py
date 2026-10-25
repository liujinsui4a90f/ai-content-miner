"""多平台编排：候选截取、复评选取、失败隔离、推送与确认顺序。"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from crawler.base import CrawlRunResult
from pipeline import orchestrator
from pipeline.types import DigestItem
from workflows.outcome import STATUS_FAILED, STATUS_OK, PlatformOutcome


def report_item(title, score):
    return {
        "article": {"title": title, "content": "content " * 20, "url": ""},
        "total_score": score,
        "summary": f"summary of {title}",
        "category": "技术",
        "filter_result": {},
    }


def reddit_record(title, score):
    return {
        "id": f"reddit:{title}",
        "platform": "reddit",
        "title": title,
        "abstract": "abstract",
        "content": "content",
        "source_url": f"https://reddit.com/{title}",
        "subreddit": "test",
        "score": score,
        "filter_metadata": {"final_decision": "keep"},
    }


class FakeBridge:
    def __init__(self, platform, items, fail=False, validate_errors=None):
        self.platform = platform
        self.configured_platform = platform
        self.items = items
        self.fail = fail
        self.validate_errors = validate_errors or []
        self.acknowledged = False

    def validate(self):
        return list(self.validate_errors)

    def run(self):
        return CrawlRunResult(
            success=not self.fail,
            data_files=(Path("current.jsonl"),) if not self.fail else (),
            error="boom" if self.fail else "",
        )

    def acknowledge(self):
        self.acknowledged = True
        return ""


def make_collector(bridges):
    def collect(platform, bridge):
        if bridge.fail:
            return PlatformOutcome(platform, STATUS_FAILED, 3, error="boom")
        return PlatformOutcome(
            platform,
            STATUS_OK,
            crawled=len(bridge.items),
            passed=len(bridge.items),
            items=bridge.items,
        )

    return collect


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.bridges = {
            "xhs": FakeBridge(
                "xhs",
                [report_item("a1", 9.0), report_item("a2", 8.0), report_item("a3", 7.0)],
            ),
            "reddit": FakeBridge(
                "reddit",
                [reddit_record("r1", 8.0), reddit_record("r2", 6.0)],
            ),
        }
        self.collect = make_collector(self.bridges)
        self.factory = lambda platform: self.bridges[platform]

    def _run(self, **kwargs):
        with patch.object(
            orchestrator, "collect_platform", side_effect=self.collect
        ), patch.object(
            orchestrator,
            "render_digest_feed",
            return_value=Path("reports/digest-test.html"),
        ), patch.object(
            orchestrator, "_write_summary"
        ), patch.object(
            orchestrator, "validate_digest_wecom_config", return_value=[]
        ):
            return orchestrator.run_all(
                list(self.bridges.keys()),
                bridge_factory=self.factory,
                **kwargs,
            )

    def test_dry_run_builds_top_k_without_llm_or_push(self):
        with patch.object(orchestrator, "send_digest") as send, patch.object(
            orchestrator.ranking, "judge_items"
        ) as judge:
            summary = self._run(dry_run=True)

        self.assertEqual(summary.exit_code, orchestrator.EXIT_OK)
        # 小红书 3 条 + Reddit 2 条，都在配额内
        self.assertEqual(len(summary.candidates), 5)
        judge.assert_not_called()
        send.assert_not_called()
        self.assertFalse(self.bridges["xhs"].acknowledged)

    def test_quota_caps_candidates_per_platform(self):
        self.bridges["xhs"].items = [
            report_item(f"a{index}", 9.0 - index) for index in range(6)
        ]
        summary = self._run(dry_run=True)
        xhs = [item for item in summary.candidates if item.platform == "xhs"]
        self.assertEqual(len(xhs), 3)  # MULTI_PLATFORM_QUOTA["xhs"]

    def test_full_run_pushes_once_then_acknowledges(self):
        events = []

        def fake_send(items, digest_date=None):
            events.append(("push", len(items)))
            return True

        def fake_ack():
            events.append(("ack", None))
            return ""

        for bridge in self.bridges.values():
            bridge.acknowledge = fake_ack

        with patch.object(orchestrator, "send_digest", side_effect=fake_send):
            summary = self._run(llm_rescore=False)

        self.assertEqual(summary.exit_code, orchestrator.EXIT_OK)
        self.assertTrue(summary.pushed)
        self.assertEqual(events[0][0], "push")
        self.assertTrue(all(event[0] == "ack" for event in events[1:]))
        self.assertEqual(len([e for e in events if e[0] == "ack"]), 2)

    def test_push_failure_skips_acknowledge(self):
        with patch.object(orchestrator, "send_digest", return_value=False):
            summary = self._run(llm_rescore=False)

        self.assertEqual(summary.exit_code, orchestrator.EXIT_NOTIFY)
        self.assertFalse(self.bridges["xhs"].acknowledged)
        self.assertFalse(self.bridges["reddit"].acknowledged)

    def test_partial_failure_returns_partial_code(self):
        self.bridges["reddit"].fail = True
        with patch.object(orchestrator, "send_digest", return_value=True):
            summary = self._run(llm_rescore=False)

        self.assertEqual(summary.exit_code, orchestrator.EXIT_PARTIAL)
        self.assertTrue(self.bridges["xhs"].acknowledged)
        statuses = {outcome.platform: outcome.status for outcome in summary.outcomes}
        self.assertEqual(statuses["reddit"], STATUS_FAILED)

    def test_platform_with_validation_errors_is_skipped(self):
        self.bridges["reddit"].validate_errors = ["缺少 REDDIT_RSS_SUBREDDITS"]
        with patch.object(orchestrator, "send_digest", return_value=True):
            summary = self._run(llm_rescore=False)

        statuses = {outcome.platform: outcome.status for outcome in summary.outcomes}
        self.assertEqual(statuses["reddit"], "skipped")
        self.assertEqual(summary.exit_code, orchestrator.EXIT_PARTIAL)

    def test_all_platforms_failed_returns_crawler_error(self):
        self.bridges["xhs"].fail = True
        self.bridges["reddit"].fail = True
        with patch.object(orchestrator, "send_digest") as send:
            summary = self._run(llm_rescore=False)

        self.assertEqual(summary.exit_code, orchestrator.EXIT_CRAWLER)
        send.assert_not_called()


class RankingTest(unittest.TestCase):
    def make(self, platform, score, rank):
        return DigestItem(
            platform=platform,
            uid=f"{platform}:{rank}",
            title=f"{platform}{rank}",
            score=score,
            rank_in_platform=rank,
        )

    def test_take_top_k_sorts_by_platform_score(self):
        from pipeline import ranking

        grouped = {
            "xhs": [self.make("xhs", 5.0, 1), self.make("xhs", 9.0, 2)],
        }
        picked = ranking.take_top_k(grouped, {"xhs": 1})
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0].score, 9.0)
        self.assertEqual(picked[0].rank_in_platform, 1)

    def test_select_final_prefers_judged_items(self):
        from pipeline import ranking

        judged = self.make("xhs", 5.0, 1)
        judged.judge_score = 9.0
        unjudged = self.make("reddit", 9.9, 1)
        ordered = ranking.select_final([unjudged, judged], total=2)
        self.assertEqual(ordered[0], judged)

    def test_interleave_is_round_robin(self):
        from pipeline import ranking

        items = [
            self.make("xhs", 1, 1),
            self.make("xhs", 1, 2),
            self.make("reddit", 1, 1),
        ]
        ordered = ranking.interleave(items)
        self.assertEqual(
            [(item.platform, item.rank_in_platform) for item in ordered],
            [("reddit", 1), ("xhs", 1), ("xhs", 2)],
        )


if __name__ == "__main__":
    unittest.main()
