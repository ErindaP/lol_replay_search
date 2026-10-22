from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


class IndexStore:
    def __init__(self, index_dir: Path) -> None:
        self.index_dir = index_dir
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.embeddings_path = self.index_dir / "embeddings.npy"
        self.metadata_path = self.index_dir / "metadata.jsonl"
        self.info_path = self.index_dir / "index_info.json"
        self.pca_path = self.index_dir / "pca.joblib"

    def save(self, embeddings: np.ndarray, metadata: list[dict[str, Any]], info: dict[str, Any]) -> None:
        np.save(self.embeddings_path, embeddings)
        with self.metadata_path.open("w", encoding="utf-8") as f:
            for row in metadata:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        self.info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> tuple[np.ndarray, list[dict[str, Any]], dict[str, Any]]:
        embeddings = np.load(self.embeddings_path)
        metadata: list[dict[str, Any]] = []
        with self.metadata_path.open("r", encoding="utf-8") as f:
            for line in f:
                metadata.append(json.loads(line))

        info = {}
        if self.info_path.exists():
            info = json.loads(self.info_path.read_text(encoding="utf-8"))
        return embeddings, metadata, info
