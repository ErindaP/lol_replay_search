from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import HashingVectorizer


@dataclass
class EmbeddingConfig:
    model_name: str = "sentence-transformers/all-MiniLM-L6-v2"
    use_pca: bool = False
    pca_dim: int = 128


class Embedder:
    def __init__(self, config: EmbeddingConfig | None = None, provider: str = "auto") -> None:
        self.config = config or EmbeddingConfig()
        self.provider = "hashing"
        self._pca: PCA | None = None
        self._st_model = None
        self._hash = HashingVectorizer(n_features=512, alternate_sign=False, norm=None)

        if provider != "hashing":
            try:
                from sentence_transformers import SentenceTransformer

                self._st_model = SentenceTransformer(self.config.model_name)
                self.provider = "sentence-transformers"
            except Exception:
                if provider == "sentence-transformers":
                    raise
                self._st_model = None

    @staticmethod
    def _l2_normalize(x: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(x, axis=1, keepdims=True) + 1e-12
        return x / norms

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        if self._st_model is not None:
            vec = self._st_model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        else:
            vec = self._hash.transform(texts).toarray().astype(np.float32)

        if self.config.use_pca:
            dim = min(self.config.pca_dim, vec.shape[1], vec.shape[0])
            if dim >= 2:
                self._pca = PCA(n_components=dim)
                vec = self._pca.fit_transform(vec)

        return self._l2_normalize(vec.astype(np.float32))

    def transform(self, texts: list[str]) -> np.ndarray:
        if self._st_model is not None:
            vec = self._st_model.encode(texts, show_progress_bar=False, convert_to_numpy=True)
        else:
            vec = self._hash.transform(texts).toarray().astype(np.float32)

        if self._pca is not None:
            vec = self._pca.transform(vec)

        return self._l2_normalize(vec.astype(np.float32))
