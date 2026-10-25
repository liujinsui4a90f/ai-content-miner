import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from utils.embedding import EmbeddingError, create_client, encode


class FakeClient:
    def __init__(self, data):
        self.data = data
        self.calls = []
        self.embeddings = self

    def create(self, model, input):
        self.calls.append((model, list(input)))
        return SimpleNamespace(data=self.data)


class FlakyClient:
    """前 failures 次调用抛异常，之后返回正常结果。"""

    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.embeddings = self

    def create(self, model, input):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("ChunkedEncodingError: Response ended prematurely")
        return SimpleNamespace(
            data=[
                SimpleNamespace(index=index, embedding=[0.0, 0.0])
                for index in range(len(input))
            ]
        )


class EmbeddingUtilityTest(unittest.TestCase):
    def test_encode_batches_and_restores_api_order(self):
        client = FakeClient([
            SimpleNamespace(index=1, embedding=[2.0, 0.0]),
            SimpleNamespace(index=0, embedding=[1.0, 0.0]),
        ])
        with patch("utils.embedding.config.EMBEDDING_MODEL", "fake-model"):
            vectors = encode(["first", "second"], client=client, batch_size=2)
        self.assertEqual(vectors, [[1.0, 0.0], [2.0, 0.0]])
        self.assertEqual(client.calls, [("fake-model", ["first", "second"])])

    def test_dashscope_native_response_is_normalized(self):
        text_embedding = SimpleNamespace(call=Mock(return_value=SimpleNamespace(
            status_code=200,
            output={"embeddings": [
                {"text_index": 1, "embedding": [2.0, 0.0]},
                {"text_index": 0, "embedding": [1.0, 0.0]},
            ]},
        )))
        fake_module = SimpleNamespace(TextEmbedding=text_embedding)
        with patch.dict("sys.modules", {"dashscope": fake_module}), patch(
            "utils.embedding.config.EMBEDDING_MODEL", "text-embedding-v3"
        ):
            client = create_client(api_key="test-key", provider="dashscope")
            vectors = encode(["first", "second"], client=client, batch_size=2)

        self.assertEqual(vectors, [[1.0, 0.0], [2.0, 0.0]])
        text_embedding.call.assert_called_once_with(
            model="text-embedding-v3",
            input=["first", "second"],
            api_key="test-key",
        )
    def test_encode_rejects_empty_text_without_shifting_results(self):
        with self.assertRaises(EmbeddingError):
            encode(["valid", "  "], client=FakeClient([]))

    def test_encode_reports_response_count_errors(self):
        client = FakeClient([SimpleNamespace(index=0, embedding=[1.0])])
        with self.assertRaisesRegex(EmbeddingError, "返回数量"):
            encode(["first", "second"], client=client)

    def test_retries_transient_failures(self):
        client = FlakyClient(failures=2)
        with patch(
            "utils.embedding.config.EMBEDDING_MODEL", "fake-model"
        ), patch(
            "utils.embedding.config.EMBEDDING_MAX_RETRIES", 2
        ), patch("utils.embedding.time.sleep") as sleep:
            vectors = encode(["first"], client=client, batch_size=1)

        self.assertEqual(client.calls, 3)
        self.assertEqual(vectors, [[0.0, 0.0]])
        self.assertEqual(sleep.call_count, 2)

    def test_gives_up_after_retries(self):
        client = FlakyClient(failures=99)
        with patch(
            "utils.embedding.config.EMBEDDING_MODEL", "fake-model"
        ), patch(
            "utils.embedding.config.EMBEDDING_MAX_RETRIES", 1
        ), patch("utils.embedding.time.sleep"):
            with self.assertRaisesRegex(EmbeddingError, "Embedding API 请求失败"):
                encode(["first"], client=client, batch_size=1)

        self.assertEqual(client.calls, 2)

    def test_zero_retries_means_single_attempt(self):
        client = FlakyClient(failures=99)
        with patch(
            "utils.embedding.config.EMBEDDING_MODEL", "fake-model"
        ), patch("utils.embedding.config.EMBEDDING_MAX_RETRIES", 0):
            with self.assertRaises(EmbeddingError):
                encode(["first"], client=client, batch_size=1)

        self.assertEqual(client.calls, 1)


if __name__ == "__main__":
    unittest.main()
