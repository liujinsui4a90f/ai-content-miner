"""评分请求的 max_tokens 必须来自配置。

推理模型的思维链计入 completion_tokens，800 会在 JSON 写到一半时被截断
（finish_reason=length）。这里锁死"评分调用使用 config.SCORE_MAX_TOKENS"，
并防止默认值回退到截断区间。
"""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import config


class ScoreMaxTokensTest(unittest.TestCase):
    def test_scoring_uses_configured_max_tokens(self):
        try:
            from analyzer import scorer
        except Exception as exc:  # noqa: BLE001 - 未配置 API key 时无法构造客户端
            self.skipTest(f"无法导入 scorer：{exc}")

        captured = {}

        class FakeCompletions:
            @staticmethod
            def create(**kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content='{"scores":[2,2,2,2],"reasons":[],'
                                '"category":"技术","summary":"摘要"}'
                            )
                        )
                    ]
                )

        fake = SimpleNamespace(
            chat=SimpleNamespace(completions=FakeCompletions())
        )
        with patch.object(scorer, "client", fake), patch.object(
            config, "SCORE_MAX_TOKENS", 2000
        ):
            result = scorer.score_and_classify(
                {"title": "t", "content": "x" * 600, "source": "zhihu"}
            )

        self.assertEqual(captured["max_tokens"], 2000)
        self.assertGreater(result["total_score"], 0)

    @unittest.skipIf(
        os.environ.get("SCORE_MAX_TOKENS"),
        "环境变量覆盖了默认值",
    )
    def test_default_is_outside_the_truncating_range(self):
        self.assertGreaterEqual(int(config.SCORE_MAX_TOKENS), 1500)


if __name__ == "__main__":
    unittest.main()
