"""Explainable clip-to-clip retrieval from text, gameplay, and video signals.

This is an unsupervised multimodal baseline. It deliberately reports each
modality's score; no toxicity or play-type labels are inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .query_engine import QueryEngine


VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}


@dataclass(frozen=True)
class ClipPlay:
    path: Path
    match_id: str
    start_ms: int
    end_ms: int
    row_index: int


def _clip_identity(path: Path) -> tuple[str, int, int] | None:
    if path.suffix.lower() not in VIDEO_SUFFIXES:
        return None
    # Replay API files may have a double extension such as .mp4.webm.
    name = path.name
    while Path(name).suffix.lower() in VIDEO_SUFFIXES:
        name = Path(name).stem
    try:
        match_id, start, end = name.rsplit("_", 2)
        start_ms, end_ms = int(start), int(end)
    except (ValueError, TypeError):
        return None
    if not match_id or end_ms <= start_ms:
        return None
    return match_id, start_ms, end_ms


def _unit_rows(values: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=np.float32)
    return values / np.maximum(np.linalg.norm(values, axis=1, keepdims=True), 1e-12)


def _gameplay_features(row: dict[str, Any]) -> np.ndarray:
    phase = str(row.get("phase", ""))
    return np.asarray(
        [
            min(float(row.get("kills_total") or 0) / 5, 2),
            min(float(row.get("kills_blue") or 0) / 5, 2),
            min(float(row.get("kills_red") or 0) / 5, 2),
            float(bool(row.get("dragon_kills"))),
            float(bool(row.get("baron_kills"))),
            min(float(row.get("tower_kills") or 0) / 3, 2),
            min(np.log1p(max(0, float(row.get("gold_abs_swing") or 0))) / 9, 2),
            float(bool(row.get("jungler_invade"))),
            float(bool(row.get("mid_roam"))),
            float(bool(row.get("mid_jungle_pair"))),
            float(bool(row.get("before_dragon"))),
            float(phase == "early"),
            float(phase == "mid"),
            float(phase == "late"),
        ],
        dtype=np.float32,
    )


def _video_features(path: Path) -> np.ndarray:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("Install the video extra: .venv/bin/python -m pip install -e '.[video]'") from exc

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        raise ValueError(f"Video unreadable: {path}")
    try:
        frames = max(1, int(capture.get(cv2.CAP_PROP_FRAME_COUNT)))
        descriptions: list[np.ndarray] = []
        grays: list[np.ndarray] = []
        for fraction in (0.15, 0.35, 0.55, 0.75, 0.9):
            capture.set(cv2.CAP_PROP_POS_FRAMES, min(frames - 1, round(frames * fraction)))
            ok, frame = capture.read()
            if not ok:
                continue
            height, width = frame.shape[:2]
            # Remove the chat, HUD and minimap; compare the play scene.
            scene = frame[int(height * 0.08):int(height * 0.78), int(width * 0.08):int(width * 0.86)]
            scene = cv2.resize(scene, (160, 90), interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(scene, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [12, 8], [0, 180, 0, 256]).ravel()
            hist = hist / max(float(hist.sum()), 1.0)
            gray = cv2.cvtColor(scene, cv2.COLOR_BGR2GRAY)
            edges = cv2.resize(cv2.Canny(gray, 70, 140), (8, 4), interpolation=cv2.INTER_AREA).ravel() / 255.0
            descriptions.append(np.concatenate([hist, edges]).astype(np.float32))
            grays.append(cv2.resize(gray, (32, 18), interpolation=cv2.INTER_AREA).astype(np.float32))
        if not descriptions:
            raise ValueError(f"No readable frames: {path}")
        images = np.stack(descriptions)
        motion = float(np.mean([np.mean(np.abs(b - a)) / 255.0 for a, b in zip(grays, grays[1:])])) if len(grays) > 1 else 0.0
        # Visual statistics summarize appearance and temporal variation.
        return np.concatenate([images.mean(axis=0), images.std(axis=0), [motion]]).astype(np.float32)
    finally:
        capture.release()


class PlaySimilarity:
    def __init__(self, index_dir: Path, clips_dir: Path) -> None:
        self.engine = QueryEngine(index_dir)
        by_match: dict[str, list[int]] = {}
        for index, row in enumerate(self.engine.metadata):
            by_match.setdefault(str(row.get("match_id", "")), []).append(index)

        clips: list[ClipPlay] = []
        visual: list[np.ndarray] = []
        for path in sorted(clips_dir.iterdir()) if clips_dir.exists() else []:
            identity = _clip_identity(path)
            if identity is None:
                continue
            match_id, start_ms, end_ms = identity
            candidates = by_match.get(match_id, [])
            if not candidates:
                continue

            def overlap(index: int) -> tuple[int, int]:
                row = self.engine.metadata[index]
                left = max(start_ms, int(row.get("start_ms", 0)))
                right = min(end_ms, int(row.get("end_ms", 0)))
                common = max(0, right - left)
                midpoint_gap = abs(start_ms + end_ms - int(row.get("start_ms", 0)) - int(row.get("end_ms", 0)))
                return common, -midpoint_gap

            row_index = max(candidates, key=overlap)
            if overlap(row_index)[0] <= 0:
                continue
            try:
                features = _video_features(path)
            except ValueError:
                continue
            clips.append(ClipPlay(path, match_id, start_ms, end_ms, row_index))
            visual.append(features)

        if len(clips) < 2:
            raise RuntimeError("At least two complete clips aligned with the match index are required")
        self.clips = clips
        indices = np.asarray([clip.row_index for clip in clips], dtype=int)
        summaries = []
        for index in indices:
            full_text = str(self.engine.metadata[int(index)].get("text", ""))
            parts = full_text.split("|", 2)
            # The short play summary excludes match IDs, champion rosters and
            # player names, which can create spurious nearest neighbours.
            summaries.append(parts[1].strip() if len(parts) >= 2 else full_text)
        self.text_vectors = _unit_rows(self.engine.embedder.transform(summaries))
        self.gameplay_vectors = _unit_rows(np.stack([_gameplay_features(self.engine.metadata[i]) for i in indices]))
        raw_visual = np.stack(visual)
        # Per-feature scaling prevents pixel magnitudes from dominating the video score.
        spread = raw_visual.std(axis=0)
        raw_visual = (raw_visual - raw_visual.mean(axis=0)) / np.where(spread > 1e-5, spread, 1.0)
        self.video_vectors = _unit_rows(raw_visual)

    def similar(
        self,
        clip_name: str,
        *,
        text_weight: float = 1.0,
        gameplay_weight: float = 1.0,
        video_weight: float = 1.0,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        weights = np.asarray([text_weight, gameplay_weight, video_weight], dtype=np.float32)
        if np.any(weights < 0) or float(weights.sum()) <= 0:
            raise ValueError("Weights must be nonnegative and at least one must be positive")
        if top_k <= 0:
            return []
        anchor = next((i for i, clip in enumerate(self.clips) if clip.path.name == clip_name), None)
        if anchor is None:
            raise ValueError(f"Unknown clip: {clip_name}")
        raw_scores = np.stack([
            self.text_vectors @ self.text_vectors[anchor],
            self.gameplay_vectors @ self.gameplay_vectors[anchor],
            self.video_vectors @ self.video_vectors[anchor],
        ], axis=1)
        # Cosine ranges differ by modality. Rank candidates on a common 0..1
        # scale so a weight of 1 has the same influence for every modality.
        others = np.arange(len(self.clips)) != anchor
        scores = np.empty_like(raw_scores)
        for column in range(raw_scores.shape[1]):
            low = float(raw_scores[others, column].min())
            high = float(raw_scores[others, column].max())
            if high - low < 1e-6:
                scores[:, column] = 0.5
            else:
                scores[:, column] = np.clip((raw_scores[:, column] - low) / (high - low), 0, 1)
            scores[anchor, column] = 1.0
        combined = scores @ (weights / weights.sum())
        order = np.argsort(-combined)
        results = []
        for i in order:
            if int(i) == anchor:
                continue
            clip = self.clips[int(i)]
            row = self.engine.metadata[clip.row_index]
            results.append({
                "clip": clip.path.name,
                "path": str(clip.path),
                "match_id": clip.match_id,
                "start_ms": int(row.get("start_ms", clip.start_ms)),
                "end_ms": int(row.get("end_ms", clip.end_ms)),
                "text": str(row.get("text", "")),
                "score": float(combined[i]),
                "text_score": float(scores[i, 0]),
                "gameplay_score": float(scores[i, 1]),
                "video_score": float(scores[i, 2]),
            })
            if len(results) >= max(0, top_k):
                break
        return results
