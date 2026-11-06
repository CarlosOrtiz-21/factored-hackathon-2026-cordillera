"""multilingual-e5 sentence embeddings, shared by the intent fallback and the policy RAG.

e5 expects a role prefix: "query: " for questions, "passage: " for documents.
The model (~470 MB) is loaded lazily, once per process.
"""

from functools import lru_cache
from typing import Protocol

import numpy as np

E5_MODEL = "intfloat/multilingual-e5-small"


class Encoder(Protocol):
    def encode(self, texts: list[str], kind: str = "query") -> np.ndarray: ...


class E5Encoder:
    def __init__(self, name: str = E5_MODEL):
        from sentence_transformers import SentenceTransformer

        self.name = name
        self.model = SentenceTransformer(name)

    def encode(self, texts: list[str], kind: str = "query") -> np.ndarray:
        return self.model.encode(
            [f"{kind}: {t}" for t in texts],
            normalize_embeddings=True,
            batch_size=64,
            show_progress_bar=False,
        )


@lru_cache
def get_encoder() -> E5Encoder:
    return E5Encoder()
