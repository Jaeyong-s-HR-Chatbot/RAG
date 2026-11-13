"""임베딩 모델. BGE-M3 (dense 1024차원, 정규화 → 내적 = 코사인).

RAG_EMBEDDER=mock 이면 모델 다운로드 없이 글자 n-gram 해시 벡터를 쓴다 (코드 흐름 점검용, 점수는 의미 없음).
"""

from __future__ import annotations

import hashlib
import re

import numpy as np


class BgeM3Embedder:
    name = "bge-m3"

    def __init__(self, cfg):
        import torch
        from sentence_transformers import SentenceTransformer

        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = SentenceTransformer(cfg.model_name, device=device)
        if device == "cuda":
            self.model.half()
        self.model.max_seq_length = cfg.max_seq_len
        self.batch_size = cfg.batch_size
        self.device = device
        self.dim = self.model.get_sentence_embedding_dimension()

    def encode(self, texts: list[str], is_query: bool = False, show_progress: bool = False) -> np.ndarray:
        v = self.model.encode(
            list(texts), batch_size=self.batch_size, normalize_embeddings=True,
            show_progress_bar=show_progress, convert_to_numpy=True,
        )
        return v.astype(np.float32)


class MockEmbedder:
    """글자 2·3-gram 해시 → 고정 차원. 같은 글자가 많이 겹치면 코사인이 높다."""
    name = "mock"

    def __init__(self, cfg=None, dim: int = 1024):
        self.dim = dim
        self.device = "cpu"

    def _vec(self, t: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        s = re.sub(r"\s+", " ", t)
        for n in (2, 3):
            for i in range(len(s) - n + 1):
                g = s[i:i + n]
                if " " in g:
                    continue
                h = int(hashlib.md5(g.encode()).hexdigest()[:8], 16)
                v[h % self.dim] += 1.0
        return v / (np.linalg.norm(v) + 1e-9)

    def encode(self, texts: list[str], is_query: bool = False, show_progress: bool = False) -> np.ndarray:
        return np.stack([self._vec(t) for t in texts]) if texts else np.zeros((0, self.dim), np.float32)


def make_embedder(cfg):
    if cfg.embedder == "mock":
        return MockEmbedder(cfg)
    return BgeM3Embedder(cfg)
