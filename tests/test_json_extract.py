"""LLM 文本回复的 JSON 提取。"""

import unittest

from utils.json_extract import extract_json_object, strip_code_fence


class JsonExtractTest(unittest.TestCase):
    def test_plain_object(self):
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})

    def test_markdown_fence(self):
        raw = '```json\n{"a": 1, "b": [1, 2]}\n```'
        self.assertEqual(extract_json_object(raw), {"a": 1, "b": [1, 2]})

    def test_two_objects_returns_first(self):
        # 之前用贪婪正则会把两个对象拼在一起，触发 Extra data
        raw = '{"a": 1}\n\n{"b": 2}'
        self.assertEqual(extract_json_object(raw), {"a": 1})

    def test_prose_around_object(self):
        raw = '好的，结果如下：\n{"a": 1}\n以上。'
        self.assertEqual(extract_json_object(raw), {"a": 1})

    def test_nested_braces(self):
        raw = '{"a": {"b": 2}, "c": "}"}'
        self.assertEqual(extract_json_object(raw), {"a": {"b": 2}, "c": "}"})

    def test_missing_object_raises(self):
        with self.assertRaisesRegex(ValueError, "未找到 JSON"):
            extract_json_object("这里没有任何 JSON")

    def test_truncated_object_raises(self):
        with self.assertRaisesRegex(ValueError, "JSON 不完整"):
            extract_json_object('{"a": 1')

    def test_non_object_payload_raises(self):
        with self.assertRaises(ValueError):
            extract_json_object("[1, 2]")

    def test_strip_code_fence_keeps_inner_text(self):
        self.assertEqual(strip_code_fence('```json\n{"a":1}\n```'), '{"a":1}')


if __name__ == "__main__":
    unittest.main()
