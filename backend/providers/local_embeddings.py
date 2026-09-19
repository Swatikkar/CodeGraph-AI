from __future__ import annotations

import hashlib
import math
import re

from langchain_core.embeddings import Embeddings


TOKEN_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[^\s]")


class LocalHashingEmbeddings(Embeddings):
    """Deterministic, dependency-free embeddings for reliable code retrieval."""

    def __init__(self, dimensions: int = 384):
        if dimensions < 64:
            raise ValueError("Local embedding dimensions must be at least 64.")
        self.dimensions = dimensions

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = [token.lower() for token in TOKEN_PATTERN.findall(text)]
        features = tokens + [
            f"{tokens[index]}::{tokens[index + 1]}"
            for index in range(len(tokens) - 1)
        ]

        for feature in features:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        magnitude = math.sqrt(sum(value * value for value in vector))
        if magnitude:
            return [value / magnitude for value in vector]
        return vector

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)
