from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .config import Settings
from .lcu_replay import watch_match_via_lcu


@dataclass
class ReplayPrepareResult:
    match_id: str
    replay_path: str
    fetched: bool
    loaded: bool
    fetch_message: str
    load_message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "replay_path": self.replay_path,
            "fetched": self.fetched,
            "loaded": self.loaded,
            "fetch_message": self.fetch_message,
            "load_message": self.load_message,
        }


class ReplayOrchestrator:
    """Best-effort automation layer for replay fetch/load before clip export.

    This module is intentionally configurable because replay fetch/load methods
    vary by environment and client version.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _template_vars(self, match_id: str, replay_path: Path) -> dict[str, str]:
        return {
            "match_id": match_id,
            "replay_path": str(replay_path),
            "project_root": str(self.settings.project_root),
            "data_replays_dir": str(self.settings.data_replays_dir),
            "riot_region": self.settings.riot_region,
            "riot_platform": self.settings.riot_platform,
            "riot_api_key": self.settings.riot_api_key,
        }

    def replay_path(self, match_id: str) -> Path:
        if self.settings.replay_file_template:
            candidate = Path(self.settings.replay_file_template.format(match_id=match_id))
            return candidate
        return self.settings.data_replays_dir / f"{match_id}.rofl"

    def _run_cmd_template(self, template: str, vars_map: dict[str, str], timeout_s: float = 120.0) -> tuple[bool, str]:
        command = template.format(**vars_map)
        proc = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        ok = proc.returncode == 0
        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        summary = {"returncode": proc.returncode, "stdout": out[-1200:], "stderr": err[-1200:]}
        return ok, json.dumps(summary, ensure_ascii=False)

    def _download_from_template_url(self, match_id: str, replay_path: Path, timeout_s: float = 60.0) -> tuple[bool, str]:
        tpl = self.settings.replay_download_url_template
        if not tpl:
            return False, "REPLAY_DOWNLOAD_URL_TEMPLATE is not set"

        url = tpl.format(match_id=match_id, riot_region=self.settings.riot_region, riot_platform=self.settings.riot_platform)
        headers = {}
        if self.settings.riot_api_key:
            headers["X-Riot-Token"] = self.settings.riot_api_key

        response = requests.get(url, headers=headers, timeout=timeout_s)
        if response.status_code >= 400:
            return False, f"download HTTP {response.status_code}: {response.text[:400]}"

        replay_path.parent.mkdir(parents=True, exist_ok=True)
        replay_path.write_bytes(response.content)
        return True, f"downloaded {len(response.content)} bytes from {url}"

    def fetch_replay(self, match_id: str) -> tuple[bool, str, Path]:
        replay_path = self.replay_path(match_id)
        if replay_path.exists():
            return True, "replay already exists", replay_path

        vars_map = self._template_vars(match_id, replay_path)

        if self.settings.replay_fetch_cmd_template:
            ok, msg = self._run_cmd_template(self.settings.replay_fetch_cmd_template, vars_map)
            if ok and replay_path.exists():
                return True, f"fetch cmd ok: {msg}", replay_path
            if ok:
                return False, f"fetch cmd succeeded but replay not found at {replay_path}", replay_path
            return False, f"fetch cmd failed: {msg}", replay_path

        if self.settings.replay_use_lcu:
            return True, "LCU mode enabled: replay download handled during load/watch", replay_path

        ok, msg = self._download_from_template_url(match_id, replay_path)
        if ok and replay_path.exists():
            return True, msg, replay_path
        return False, msg, replay_path

    def load_replay(self, match_id: str, replay_path: Path) -> tuple[bool, str]:
        if self.settings.replay_use_lcu:
            ok, msg = watch_match_via_lcu(self.settings, match_id, launch_client=True)
            if ok:
                return True, "LCU ok: " + msg
            if not self.settings.replay_load_cmd_template:
                return False, "LCU failed: " + msg
            vars_map = self._template_vars(match_id, replay_path)
            ok2, msg2 = self._run_cmd_template(self.settings.replay_load_cmd_template, vars_map)
            if ok2:
                return True, "LCU failed but cmd fallback ok: " + msg2
            return False, "LCU failed and cmd fallback failed. lcu=" + msg + " cmd=" + msg2

        if not self.settings.replay_load_cmd_template:
            return False, "REPLAY_LOAD_CMD_TEMPLATE is not set"

        vars_map = self._template_vars(match_id, replay_path)
        ok, msg = self._run_cmd_template(self.settings.replay_load_cmd_template, vars_map)
        return ok, ("load cmd ok: " + msg) if ok else ("load cmd failed: " + msg)

    def prepare_replay(self, match_id: str, auto_fetch: bool, auto_load: bool) -> ReplayPrepareResult:
        replay_path = self.replay_path(match_id)

        fetched = False
        fetch_message = "auto-fetch disabled"
        if auto_fetch:
            fetched, fetch_message, replay_path = self.fetch_replay(match_id)

        loaded = False
        load_message = "auto-load disabled"
        if auto_load:
            loaded, load_message = self.load_replay(match_id, replay_path)

        return ReplayPrepareResult(
            match_id=match_id,
            replay_path=str(replay_path),
            fetched=fetched,
            loaded=loaded,
            fetch_message=fetch_message,
            load_message=load_message,
        )
