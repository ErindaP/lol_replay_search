from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .replay_api import ReplayAPIClient, ensure_replay_api_reachable


@dataclass
class ClipRequest:
    match_id: str
    start_ms: int
    end_ms: int
    title: str = ""



def export_clip(
    request: ClipRequest,
    output_dir: Path,
    *,
    pad_before_s: float = 2.0,
    pad_after_s: float = 2.0,
    codec: str = "webm",
    verify_tls: bool = False,
    timeout_s: float = 300.0,
    camera_x: float | None = None,
    camera_y: float | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    start_s = max(0.0, request.start_ms / 1000.0 - pad_before_s)
    end_s = max(start_s + 0.5, request.end_ms / 1000.0 + pad_after_s)

    clip_name = f"{request.match_id}_{int(start_s*1000)}_{int(end_s*1000)}.mp4"
    clip_path = output_dir / clip_name

    client = ReplayAPIClient(verify_tls=verify_tls)
    ensure_replay_api_reachable(client)

    client.set_playback(paused=True, time_s=start_s, speed=1.0)
    client.wait_until_time(start_s)
    camera_applied = False
    if camera_x is not None and camera_y is not None:
        try:
            client.set_camera_position(x=camera_x, y=camera_y, z=0.0)
            camera_applied = True
        except Exception:
            camera_applied = False

    client.start_recording(path=clip_path, codec=codec, start_time_s=start_s, end_time_s=end_s)
    status = client.wait_recording_done(timeout_s=timeout_s)

    return {
        "match_id": request.match_id,
        "title": request.title,
        "start_ms": request.start_ms,
        "end_ms": request.end_ms,
        "clip_start_s": start_s,
        "clip_end_s": end_s,
        "codec": codec,
        "output": str(clip_path),
        "camera_x": camera_x,
        "camera_y": camera_y,
        "camera_applied": camera_applied,
        "recording_status": status,
    }



def build_web_manifest(items: list[dict[str, Any]], output_path: Path, base_url: str = "") -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    clips = []
    for it in items:
        path = Path(str(it["output"]))
        rel = path.name
        video_url = f"{base_url.rstrip('/')}/{rel}" if base_url else rel
        clips.append(
            {
                "match_id": it.get("match_id"),
                "title": it.get("title", ""),
                "start_ms": it.get("start_ms"),
                "end_ms": it.get("end_ms"),
                "video": video_url,
                "local_path": str(path),
                "meta": {
                    "clip_start_s": it.get("clip_start_s"),
                    "clip_end_s": it.get("clip_end_s"),
                },
            }
        )

    payload = {"clips": clips}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
