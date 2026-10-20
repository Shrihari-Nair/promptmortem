"""embed(texts) -> L2-normalised matrix. sentence-transformers if installed, else hashed char 3-grams."""
import hashlib
import logging

import numpy as np

from app.config import settings

log = logging.getLogger(__name__)
_model = None
_DIM = 1024


def scenario_text(inp: dict) -> str:
    return " ".join(str(v) for v in inp.values())


def _hash_embed(texts: list[str]) -> np.ndarray:
    out = np.zeros((len(texts), _DIM), dtype=np.float32)
    for i, t in enumerate(texts):
        t = f"  {t.lower()}  "
        for j in range(len(t) - 2):
            out[i, int(hashlib.md5(t[j : j + 3].encode()).hexdigest()[:8], 16) % _DIM] += 1
    return _norm(out)


def _norm(m: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(n == 0, 1, n)


def embed(texts: list[str]) -> np.ndarray:
    global _model
    if not texts:
        return np.zeros((0, _DIM), dtype=np.float32)
    if settings.embedding_model != "hash":
        try:
            if _model is None:
                from sentence_transformers import SentenceTransformer

                _model = SentenceTransformer(settings.embedding_model)
            return _norm(np.asarray(_model.encode(texts), dtype=np.float32))
        except ImportError:
            log.warning("sentence-transformers not installed (uv sync --extra embeddings); using hashed n-gram embeddings")
            settings.embedding_model = "hash"
    return _hash_embed(texts)
