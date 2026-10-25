"""多平台统一运行的前置条件：同一进程内可共存多个采集器。"""

import unittest

import config
from crawler.factory import build_collector


class MultiPlatformFactoryTest(unittest.TestCase):
    def test_explicit_platform_is_recorded(self):
        for platform in ("xhs", "zhihu", "github", "reddit", "x", "youtube"):
            with self.subTest(platform=platform):
                bridge = build_collector(platform)
                self.assertEqual(bridge.configured_platform, platform)

    def test_media_crawler_platform_is_mapped(self):
        self.assertEqual(build_collector("xhs").platform, "xhs")
        self.assertEqual(build_collector("zhihu").platform, "zhihu")

    def test_bridges_do_not_depend_on_global_config(self):
        original = config.CRAWL_PLATFORM
        try:
            config.CRAWL_PLATFORM = "github"
            first = build_collector("zhihu")
            second = build_collector("reddit")
            # 构造第二个采集器不应改变第一个采集器的判定结果
            self.assertEqual(first.configured_platform, "zhihu")
            self.assertEqual(second.configured_platform, "reddit")
            self.assertEqual(first.platform, "zhihu")
            self.assertEqual(second.platform, "reddit")
        finally:
            config.CRAWL_PLATFORM = original

    def test_mismatch_is_reported_against_explicit_platform(self):
        original = config.CRAWL_PLATFORM
        try:
            config.CRAWL_PLATFORM = "zhihu"
            errors = build_collector("reddit").validate()
        finally:
            config.CRAWL_PLATFORM = original
        self.assertFalse(
            any("CRAWL_PLATFORM" in error for error in errors),
            f"显式平台不应触发 CRAWL_PLATFORM 不匹配：{errors}",
        )


if __name__ == "__main__":
    unittest.main()
