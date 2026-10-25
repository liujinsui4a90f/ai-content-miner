"""Shared OpenAI-compatible Embedding client for all platform filters."""

from __future__ import annotations

import math
import re
import time
from numbers import Real
from typing import Callable, List, Optional, Sequence, TypeVar, Union

import config

_T = TypeVar("_T")


class EmbeddingError(RuntimeError):
    """Embedding configuration, transport, or response error."""


def _field(value: object, name: str) -> object:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _error_message(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    request_id = getattr(exc, "request_id", None)
    message = re.sub(r"\s+", " ", str(exc)).strip()[:500]
    details = [type(exc).__name__]
    if status is not None:
        details.append(f"HTTP {status}")
    if request_id:
        details.append(f"request_id={request_id}")
    if message:
        details.append(message)
    return "；".join(details)


class _DashScopeEmbeddings:
    """Expose DashScope native embeddings through the local client contract."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.embeddings = self

    def create(self, model: str, input: Sequence[str]) -> dict:
        try:
            from dashscope import TextEmbedding
            response = TextEmbedding.call(
                model=model,
                input=list(input),
                api_key=self.api_key,
            )
        except Exception as exc:
            raise EmbeddingError(
                f"DashScope Embedding 请求失败：{_error_message(exc)}"
            ) from exc
        if getattr(response, "status_code", None) != 200:
            raise EmbeddingError(
                "DashScope Embedding 请求失败："
                f"code={getattr(response, 'code', '')}；"
                f"message={getattr(response, 'message', '')}；"
                f"request_id={getattr(response, 'request_id', '')}"
            )
        output = getattr(response, "output", {})
        entries = output.get("embeddings", []) if isinstance(output, dict) else []
        data = []
        for fallback_index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise EmbeddingError("DashScope Embedding 返回了无效向量项")
            data.append({
                "index": entry.get("text_index", fallback_index),
                "embedding": entry.get("embedding"),
            })
        return {"data": data}


def create_client(
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    provider: Optional[str] = None,
) -> object:
    active_provider = str(provider or config.EMBEDDING_PROVIDER).strip().lower()
    active_key = str(api_key or config.EMBEDDING_API_KEY).strip()
    if active_provider == "dashscope":
        return _DashScopeEmbeddings(active_key)
    if active_provider != "openai":
        raise EmbeddingError("EMBEDDING_PROVIDER 只能是 openai 或 dashscope")

    from openai import OpenAI
    return OpenAI(
        api_key=active_key,
        base_url=base_url or config.EMBEDDING_BASE_URL,
        timeout=float(config.EMBEDDING_TIMEOUT_SECONDS),
        max_retries=int(config.EMBEDDING_MAX_RETRIES),
    )

def validate_config(require_credentials: bool = True) -> None:
    """Validate shared Embedding settings before making a request."""
    provider = str(getattr(config, "EMBEDDING_PROVIDER", "openai")).strip().lower()
    if provider not in {"openai", "dashscope"}:
        raise EmbeddingError("EMBEDDING_PROVIDER 只能是 openai 或 dashscope")
    if require_credentials and not str(config.EMBEDDING_API_KEY or "").strip():
        required = "DASHSCOPE_API_KEY" if provider == "dashscope" else "EMBEDDING_API_KEY"
        raise EmbeddingError(f"{required} 未配置")
    if provider == "openai" and require_credentials and not str(config.EMBEDDING_BASE_URL or "").strip():
        raise EmbeddingError("EMBEDDING_BASE_URL 未配置")
    if not str(config.EMBEDDING_MODEL or "").strip():
        raise EmbeddingError("EMBEDDING_MODEL 不能为空")
    if not isinstance(config.EMBEDDING_BATCH_SIZE, int) or config.EMBEDDING_BATCH_SIZE <= 0:
        raise EmbeddingError("EMBEDDING_BATCH_SIZE 必须是正整数")
    if not isinstance(config.EMBEDDING_TIMEOUT_SECONDS, (int, float)) or config.EMBEDDING_TIMEOUT_SECONDS <= 0:
        raise EmbeddingError("EMBEDDING_TIMEOUT_SECONDS 必须大于 0")
    if not isinstance(config.EMBEDDING_MAX_RETRIES, int) or config.EMBEDDING_MAX_RETRIES < 0:
        raise EmbeddingError("EMBEDDING_MAX_RETRIES 必须是非负整数")

def _normalize_texts(texts: Union[str, Sequence[str]]) -> List[str]:
    values = [texts] if isinstance(texts, str) else list(texts)
    if any(not isinstance(value, str) for value in values):
        raise EmbeddingError("Embedding 输入必须是字符串或字符串列表")
    normalized = [value.strip() for value in values]
    if any(not value for value in normalized):
        raise EmbeddingError("Embedding 输入不能包含空文本")
    return normalized


def _call_with_retry(call: Callable[[], _T], attempts: int) -> _T:
    """调用外部接口并在失败时重试，最多重试 attempts 次。"""
    total = max(0, int(attempts))
    last: Optional[Exception] = None
    for attempt in range(total + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001 - 统一转换为 EmbeddingError
            last = exc
            if attempt >= total:
                break
            delay = min(2.0 ** attempt, 8.0)
            print(
                f"   ⚠️ Embedding 请求失败（第 {attempt + 1}/{total + 1} 次尝试）："
                f"{_error_message(exc)}；{delay:.0f} 秒后重试"
            )
            time.sleep(delay)
    raise EmbeddingError(
        f"Embedding API 请求失败：{_error_message(last)}"
    ) from last


def encode(
    texts: Union[str, Sequence[str]],
    *,
    client: Optional[object] = None,
    model: Optional[str] = None,
    batch_size: Optional[int] = None,
) -> List[List[float]]:
    """Batch-encode texts and preserve the exact input order."""
    validate_config(require_credentials=client is None)
    values = _normalize_texts(texts)
    if not values:
        return []
    active_client = client or create_client()
    active_model = str(model or config.EMBEDDING_MODEL).strip()
    active_batch_size = int(batch_size or config.EMBEDDING_BATCH_SIZE)
    if not active_model:
        raise EmbeddingError("Embedding model 不能为空")
    if active_batch_size <= 0:
        raise EmbeddingError("Embedding batch_size 必须是正整数")

    vectors: List[List[float]] = []
    for start in range(0, len(values), active_batch_size):
        batch = values[start : start + active_batch_size]
        response = _call_with_retry(
            lambda: active_client.embeddings.create(
                model=active_model,
                input=batch,
            ),
            attempts=int(getattr(config, "EMBEDDING_MAX_RETRIES", 0)),
        )

        data = _field(response, "data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError("Embedding API 返回数量与输入不一致")
        ordered: List[Optional[List[float]]] = [None] * len(batch)
        for entry in data:
            index = _field(entry, "index")
            vector = _field(entry, "embedding")
            if not isinstance(index, int) or not 0 <= index < len(batch):
                raise EmbeddingError("Embedding API 返回了无效 index")
            if ordered[index] is not None:
                raise EmbeddingError("Embedding API 返回了重复 index")
            if not isinstance(vector, list) or any(
                not isinstance(value, Real) for value in vector
            ):
                raise EmbeddingError("Embedding API 返回了无效向量")
            ordered[index] = [float(value) for value in vector]
        if any(vector is None for vector in ordered):
            raise EmbeddingError("Embedding API 返回的 index 不完整")
        vectors.extend(vector for vector in ordered if vector is not None)
    return vectors


def cosine_similarity(
    vector_a: Sequence[Real],
    vector_b: Sequence[Real],
) -> float:
    """Return cosine similarity, or zero for an empty/zero vector."""
    if len(vector_a) != len(vector_b):
        raise ValueError("向量维度不一致")
    if not vector_a:
        return 0.0
    if any(not isinstance(value, Real) for value in vector_a) or any(
        not isinstance(value, Real) for value in vector_b
    ):
        raise ValueError("向量必须只包含数字")
    norm_a = math.sqrt(sum(float(value) ** 2 for value in vector_a))
    norm_b = math.sqrt(sum(float(value) ** 2 for value in vector_b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return sum(
        float(value_a) * float(value_b)
        for value_a, value_b in zip(vector_a, vector_b)
    ) / (norm_a * norm_b)
