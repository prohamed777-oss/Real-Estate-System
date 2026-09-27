"""Embedding providers — part of the Model Gateway (§61).

mock: deterministic hash-based vectors (tests/dev, no network)
gemini: text-embedding-004 via REST (768 dims)

The gateway is provider-agnostic: swap via AI_PROVIDER setting.
"""

from __future__ import annotations

import hashlib
import math

import httpx

from app.core.config import settings
from app.core.errors import ExternalProviderError


class EmbeddingProvider:
    name = "mock"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class MockEmbeddingProvider(EmbeddingProvider):
    """Deterministic bag-of-words hashing — same text → same vector.

    Good enough for tests: similar texts share tokens, so cosine similarity
    behaves sensibly without any network dependency."""

    name = "mock"

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    def _token_vector(self, token: str) -> list[float]:
        digest = hashlib.sha256(token.encode()).digest()
        vec = [0.0] * self.dimensions
        for i in range(self.dimensions):
            byte = digest[i % len(digest)]
            vec[i] = ((byte / 255.0) - 0.5) * 0.2
        return vec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            tokens = text.lower().split()
            vec = [0.0] * self.dimensions
            for token in tokens:
                tv = self._token_vector(token)
                vec = [a + b for a, b in zip(vec, tv)]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            out.append([x / norm for x in vec])
        return out


class GeminiEmbeddingProvider(EmbeddingProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str, dimensions: int = 768) -> None:
        self.api_key = api_key
        self.model = model
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:batchEmbedContents"
        )
        body = {
            "requests": [
                {
                    "model": f"models/{self.model}",
                    "content": {"parts": [{"text": t[:8000]}]},
                    "outputDimensionality": self.dimensions,
                }
                for t in texts
            ]
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=body, headers={"x-goog-api-key": self.api_key})
        except httpx.HTTPError as exc:
            raise ExternalProviderError(f"Gemini embedding unreachable: {exc}") from exc
        if resp.status_code >= 400:
            raise ExternalProviderError(f"Gemini embedding error {resp.status_code}: {resp.text[:300]}")
        data = resp.json()
        return [item["values"] for item in data.get("embeddings", [])]


def get_embedding_provider() -> EmbeddingProvider:
    if settings.ai_provider == "gemini" and settings.gemini_api_key:
        return GeminiEmbeddingProvider(
            settings.gemini_api_key, settings.gemini_embedding_model, settings.embedding_dimensions
        )
    return MockEmbeddingProvider(settings.embedding_dimensions)
