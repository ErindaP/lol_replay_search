from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import requests


class ReplayAPIClient:
    """Client for local LoL Replay API (game client): https://127.0.0.1:2999/replay/*"""

    def __init__(self, base_url: str = "https://127.0.0.1:2999", timeout: int = 10, verify_tls: bool = False) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.verify_tls = verify_tls

        if not verify_tls:
            requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    def _get(self, path: str) -> dict[str, Any]:
        response = self.session.get(self._url(path), timeout=self.timeout, verify=self.verify_tls)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {"value": data}

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = self.session.post(self._url(path), json=payload, timeout=self.timeout, verify=self.verify_tls)
        response.raise_for_status()
        if response.text.strip():
            data = response.json()
            return data if isinstance(data, dict) else {"value": data}
        return {}

    def get_game(self) -> dict[str, Any]:
        return self._get("/replay/game")

    def get_playback(self) -> dict[str, Any]:
        return self._get("/replay/playback")

    def set_playback(self, *, paused: bool | None = None, time_s: float | None = None, speed: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if paused is not None:
            payload["paused"] = paused
        if time_s is not None:
            payload["time"] = float(time_s)
        if speed is not None:
            payload["speed"] = float(speed)
        return self._post("/replay/playback", payload)

    def get_render(self) -> dict[str, Any]:
        return self._get("/replay/render")

    def set_render(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._post("/replay/render", payload)

    def set_camera_position(self, *, x: float, y: float, z: float = 0.0) -> dict[str, Any]:
        return self.set_render(
            {
                "cameraPosition": {
                    "x": float(x),
                    "y": float(y),
                    "z": float(z),
                }
            }
        )

    def get_recording(self) -> dict[str, Any]:
        return self._get("/replay/recording")

    def start_recording(self, path: Path, codec: str = "webm", start_time_s: float | None = None, end_time_s: float | None = None) -> dict[str, Any]:
        codec_value = {"libx264": "webm", "h264": "webm", "mp4": "webm"}.get(str(codec).lower(), codec)
        payload: dict[str, Any] = {
            "path": str(path),
            "codec": codec_value,
        }
        if start_time_s is not None:
            payload["startTime"] = float(start_time_s)
        if end_time_s is not None:
            payload["endTime"] = float(end_time_s)
        return self._post("/replay/recording", payload)

    def wait_until_time(self, target_time_s: float, tolerance_s: float = 0.35, timeout_s: float = 20.0, poll_s: float = 0.2) -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            pb = self.get_playback()
            current = float(pb.get("time", 0.0))
            if abs(current - target_time_s) <= tolerance_s:
                return
            time.sleep(poll_s)
        raise TimeoutError(f"Replay playback did not reach target time={target_time_s}s")

    def wait_recording_done(self, timeout_s: float = 300.0, poll_s: float = 0.5) -> dict[str, Any]:
        deadline = time.time() + timeout_s
        last = {}
        while time.time() < deadline:
            status = self.get_recording()
            last = status
            phase = str(status.get("phase", "")).lower()
            # Common phases observed: idle, recording, complete, error
            if phase in {"complete", "completed", "idle"} and not bool(status.get("recording", False)):
                return status
            if phase in {"error", "failed"}:
                raise RuntimeError(f"Replay recording failed: {status}")
            time.sleep(poll_s)
        raise TimeoutError(f"Replay recording timeout. Last status: {last}")



def ensure_replay_api_reachable(client: ReplayAPIClient) -> None:
    try:
        client.get_game()
    except Exception as exc:
        raise RuntimeError(
            "Replay API unreachable at https://127.0.0.1:2999. "
            "Start LoL client, load a replay, and enable Replay API in game.cfg (EnableReplayApi=1)."
        ) from exc
