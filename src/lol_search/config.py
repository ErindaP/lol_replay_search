from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    project_root: Path
    data_raw_dir: Path
    data_processed_dir: Path
    data_index_dir: Path
    data_clips_dir: Path
    data_replays_dir: Path
    riot_api_key: str
    riot_platform: str
    riot_region: str
    openai_api_key: str | None
    replay_fetch_cmd_template: str | None
    replay_load_cmd_template: str | None
    replay_download_url_template: str | None
    replay_file_template: str | None
    replay_use_lcu: bool
    replay_lcu_executor: str
    replay_api_executor: str
    replay_api_base_url: str
    riot_client_path: str
    league_dir: str
    replay_lcu_windows_script: str
    replay_api_windows_script: str



def load_settings(project_root: Path | None = None) -> Settings:
    root = project_root or Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")
    riot_api_key = os.getenv("RIOT_API_KEY", "").strip().strip("\"").strip("'")
    riot_platform = os.getenv("RIOT_PLATFORM", "euw1").strip()
    riot_region = os.getenv("RIOT_REGION", "europe").strip()
    openai_api_key = (os.getenv("OPENAI_API_KEY") or "").strip() or None
    def _clean(val: str | None) -> str:
        return (val or "").strip().strip("\"").strip("'")

    replay_fetch_cmd_template = _clean(os.getenv("REPLAY_FETCH_CMD_TEMPLATE")) or None
    replay_load_cmd_template = _clean(os.getenv("REPLAY_LOAD_CMD_TEMPLATE")) or None
    replay_download_url_template = _clean(os.getenv("REPLAY_DOWNLOAD_URL_TEMPLATE")) or None
    replay_file_template = _clean(os.getenv("REPLAY_FILE_TEMPLATE")) or None
    replay_use_lcu = (os.getenv("REPLAY_USE_LCU") or "0").strip() in {"1", "true", "True", "yes", "YES"}
    replay_lcu_executor = _clean(os.getenv("REPLAY_LCU_EXECUTOR")) or "auto"
    replay_api_executor = _clean(os.getenv("REPLAY_API_EXECUTOR")) or "auto"
    replay_api_base_url = _clean(os.getenv("REPLAY_API_BASE_URL")) or "https://127.0.0.1:2999"
    def _windows_to_wsl(value: str) -> str:
        if len(value) >= 3 and value[1:3] in {":/", ":\\"}:
            return f"/mnt/{value[0].lower()}/{value[3:].replace(chr(92), '/')}"
        return value

    installs_path = Path("/mnt/c/ProgramData/Riot Games/RiotClientInstalls.json")
    installed_client = ""
    installed_league = ""
    if installs_path.is_file():
        try:
            installs = json.loads(installs_path.read_text(encoding="utf-8"))
            installed_client = _windows_to_wsl(str(installs.get("rc_live") or installs.get("rc_default") or ""))
            for game_dir in installs.get("associated_client", {}):
                if "league of legends" in game_dir.lower():
                    installed_league = _windows_to_wsl(game_dir).rstrip("/")
                    break
        except (OSError, ValueError, TypeError):
            pass
    riot_client_path = _windows_to_wsl(_clean(os.getenv("RIOT_CLIENT_PATH"))) or installed_client or "/mnt/c/Riot Games/Riot Client/RiotClientServices.exe"
    league_dir = _windows_to_wsl(_clean(os.getenv("LEAGUE_DIR"))) or installed_league or "/mnt/c/Riot Games/League of Legends"
    replay_lcu_windows_script = _clean(os.getenv("REPLAY_LCU_WINDOWS_SCRIPT")) or str(root / "scripts" / "watch_replay_lcu_windows.ps1")
    replay_api_windows_script = _clean(os.getenv("REPLAY_API_WINDOWS_SCRIPT")) or str(root / "scripts" / "replay_api_action_windows.ps1")

    return Settings(
        project_root=root,
        data_raw_dir=root / "data" / "raw",
        data_processed_dir=root / "data" / "processed",
        data_index_dir=root / "data" / "index",
        data_clips_dir=root / "data" / "clips",
        data_replays_dir=root / "data" / "replays",
        riot_api_key=riot_api_key,
        riot_platform=riot_platform,
        riot_region=riot_region,
        openai_api_key=openai_api_key,
        replay_fetch_cmd_template=replay_fetch_cmd_template,
        replay_load_cmd_template=replay_load_cmd_template,
        replay_download_url_template=replay_download_url_template,
        replay_file_template=replay_file_template,
        replay_use_lcu=replay_use_lcu,
        replay_lcu_executor=replay_lcu_executor,
        replay_api_executor=replay_api_executor,
        replay_api_base_url=replay_api_base_url,
        riot_client_path=riot_client_path,
        league_dir=league_dir,
        replay_lcu_windows_script=replay_lcu_windows_script,
        replay_api_windows_script=replay_api_windows_script,
    )
