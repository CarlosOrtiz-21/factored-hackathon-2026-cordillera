"""Hybrid retrieval: BM25 (exact words) + e5 embeddings (meaning, cross-lingual),
combined with Reciprocal Rank Fusion.

Why RRF: BM25 scores and cosine similarities live on different scales, so adding them
is meaningless. RRF only uses each method's *rank*: score = sum(1 / (k + rank)).

The corpus has a few dozen sections, so exact search in memory (a matrix product) is
simpler and faster than a vector database.
"""

import re
import unicodedata
from dataclasses import dataclass

import numpy as np
from rank_bm25 import BM25Okapi

from cardagent.embeddings import Encoder
from cardagent.rag.corpus import Chunk

RRF_K = 60


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.findall(r"[a-z0-9]+", text)


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float  # RRF score
    dense_similarity: float  # cosine; a low best value hints the corpus can't answer


class HybridRetriever:
    def __init__(self, chunks: list[Chunk], encoder: Encoder):
        self.chunks = chunks
        self._encoder = encoder
        self._bm25 = BM25Okapi([tokenize(c.for_embedding()) for c in chunks])
        self._matrix = encoder.encode([c.for_embedding() for c in chunks], kind="passage")

    def search(self, query: str, k: int = 4) -> list[Hit]:
        sparse = self._bm25.get_scores(tokenize(query))
        dense = self._matrix @ self._encoder.encode([query], kind="query")[0]
        fused = np.zeros(len(self.chunks))
        for scores in (sparse, dense):
            ranks = np.argsort(-scores, kind="stable")
            fused[ranks] += 1.0 / (RRF_K + np.arange(1, len(ranks) + 1))
        top = np.argsort(-fused, kind="stable")[:k]
        return [Hit(self.chunks[i], float(fused[i]), float(dense[i])) for i in top]
