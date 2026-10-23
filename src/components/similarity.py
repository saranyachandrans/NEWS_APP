"""Similar-news lookup using the article vectors made by the preprocessing step.

Loads `data/processed/indian_news_vectors.npz` (ids + unit-length vectors) and
finds the articles whose vectors are closest to a given article. Because the
vectors are unit length, cosine similarity is just a dot product.

The file is re-read automatically when it changes on disk (for example after
running `python main.py --stage preprocess` again).
"""
import os
import re
import threading

import numpy as np

from src.utils import get_logger, make_id

logger = get_logger("Similarity")

_HEX16 = re.compile(r"[0-9a-f]{16}")


class SimilarityIndex:

    def __init__(self, npz_path: str):
        self.npz_path = npz_path
        self._lock = threading.Lock()
        self._mtime = None
        self._matrix = None          # (n_articles, dims), rows are unit length
        self._pos = {}               # article id -> row number

    @staticmethod
    def normalize_id(raw) -> str:
        """Article ids are 16 hex characters; older data used the URL as the id."""
        raw = str(raw)
        return raw if _HEX16.fullmatch(raw) else make_id(raw)

    # ------------------------------------------------------------ loading
    def _reset(self):
        self._matrix, self._pos = None, {}

    def _refresh(self):
        with self._lock:
            if not os.path.exists(self.npz_path):
                self._reset()
                self._mtime = None
                return
            mtime = os.path.getmtime(self.npz_path)
            if mtime == self._mtime:
                return
            self._mtime = mtime            # also stops a broken file being re-read every request
            try:
                data = np.load(self.npz_path, allow_pickle=False)
                ids = [self.normalize_id(i) for i in data["ids"]]
                matrix = np.asarray(data["vectors"], dtype=np.float32)
                if matrix.ndim != 2 or len(ids) != len(matrix) or not len(ids):
                    raise ValueError("ids and vectors do not match")
            except Exception as error:     # corrupt/partial file: behave as if there are no vectors
                logger.warning("Could not load %s: %s", self.npz_path, error)
                self._reset()
                return
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            self._matrix = matrix / norms
            self._pos = {article_id: row for row, article_id in enumerate(ids)}
            logger.info("Loaded %d vectors (%d dimensions)", *self._matrix.shape)

    # ------------------------------------------------------------ queries
    @property
    def available(self) -> bool:
        """True when a vectors file exists and could be loaded."""
        self._refresh()
        return self._matrix is not None

    def has(self, article_id: str) -> bool:
        self._refresh()
        return article_id in self._pos

    def similar(self, article_id: str, k: int = 3, min_score: float = 0.0):
        """Up to k most similar articles as [(article_id, cosine_score), ...], best first.

        The article itself is never returned. Returns [] if the article has no vector.
        """
        self._refresh()
        row = self._pos.get(article_id)
        if row is None or self._matrix is None or len(self._matrix) < 2:
            return []

        scores = self._matrix @ self._matrix[row]
        scores[row] = -np.inf                              # exclude the article itself
        k = min(k, len(scores) - 1)
        top = np.argpartition(-scores, k - 1)[:k]          # k best, unordered
        top = top[np.argsort(-scores[top])]                # sort them best first

        row_to_id = {r: a for a, r in self._pos.items()}
        return [(row_to_id[int(r)], float(scores[r])) for r in top
                if scores[r] >= min_score and int(r) in row_to_id]
