#!/usr/bin/env python3
"""Portable, read-only checks for an extracted project archive."""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values


def wsl_path(value: str) -> Path:
    if len(value) >= 3 and value[1:3] in {":/", ":\\"}:
        return Path("/mnt") / value[0].lower() / value[3:].replace("\\", "/")
    return Path(value)


def main() -> int:
    env = env_values()
    issues = 0

    def report(ok: bool, label: str, detail: str = "", *, required: bool = False) -> None:
        nonlocal issues
        print(f"{'OK' if ok else 'À vérifier'}  {label}{': ' + detail if detail else ''}")
        if required and not ok:
            issues += 1

    python = ROOT / ".venv/bin/python"
    valid_python = python.is_file() and python.stat().st_size > 0
    if valid_python:
        check = subprocess.run(
            [str(python), "-c", "import lol_search, streamlit, numpy, pandas, plotly, sklearn, cv2"],
            capture_output=True,
            text=True,
            check=False,
        )
        valid_python = check.returncode == 0
    report(valid_python, "Environnement Python", "./start.sh setup" if not valid_python else str(python), required=True)

    index = ROOT / "data/index"
    required_index = (index / "embeddings.npy", index / "metadata.jsonl", index / "index_info.json")
    report(all(path.is_file() and path.stat().st_size > 0 for path in required_index), "Index de recherche", required=True)
    if (index / "index_info.json").is_file():
        try:
            info = json.loads((index / "index_info.json").read_text(encoding="utf-8"))
            print(f"    {info.get('rows', '?')} fenêtres, {info.get('embedder_provider', '?')} / {info.get('dim', '?')} dimensions")
            if info.get("use_pca") and not (index / "pca.joblib").is_file():
                report(False, "Ancien index PCA", "reconstruire avec ./start.sh cli build-index")
        except (OSError, ValueError):
            report(False, "Métadonnées de l'index", "fichier illisible", required=True)

    matches = len(list((ROOT / "data/raw").glob("*.match.json")))
    clips = sum(1 for p in (ROOT / "data/clips").glob("*") if p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"})
    print(f"Données conservées : {matches} matchs, {clips} clips")

    key = env.get("RIOT_API_KEY", "")
    report(bool(key and key != "RGAPI-your-key"), "Clé Riot renseignée", "renouveler si l'API renvoie 401" if key else "requise pour importer de nouveaux matchs")
    if "--live" in sys.argv[1:] and key:
        sample = next((ROOT / "data/raw").glob("*.match.json"), None)
        if sample is not None:
            region = env.get("RIOT_REGION") or "europe"
            match_id = sample.name.removesuffix(".match.json")
            url = f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_id}"
            request = urllib.request.Request(url, headers={"X-Riot-Token": key})
            try:
                with urllib.request.urlopen(request, timeout=10) as response:
                    report(response.status == 200, "Clé Riot valide", f"HTTP {response.status}")
            except urllib.error.HTTPError as exc:
                report(False, "Clé Riot valide", f"HTTP {exc.code}; renouveler RIOT_API_KEY si 401 ou 403")
            except (urllib.error.URLError, TimeoutError) as exc:
                report(False, "API Riot joignable", type(exc).__name__)

    installs = Path("/mnt/c/ProgramData/Riot Games/RiotClientInstalls.json")
    active_client = ""
    active_league = ""
    if installs.is_file():
        try:
            data = json.loads(installs.read_text(encoding="utf-8"))
            active_client = data.get("rc_live") or data.get("rc_default") or ""
            active_league = next((p for p in data.get("associated_client", {}) if "league of legends" in p.lower()), "")
        except (OSError, ValueError):
            pass
    client = wsl_path(env.get("RIOT_CLIENT_PATH") or active_client or "/mnt/c/Riot Games/Riot Client/RiotClientServices.exe")
    league = wsl_path(env.get("LEAGUE_DIR") or active_league or "/mnt/c/Riot Games/League of Legends")
    report(client.is_file(), "Client Riot", str(client))
    report((league / "LeagueClient.exe").is_file(), "League of Legends", str(league))
    game_cfg = league / "Config/game.cfg"
    replay_api_enabled = game_cfg.is_file() and b"EnableReplayApi=1" in game_cfg.read_bytes()
    report(replay_api_enabled, "Replay API activee", str(game_cfg))
    report((league / "lockfile").is_file(), "Client League ouvert", "nécessaire seulement pour ouvrir un replay")
    report(bool(env.get("REPLAY_USE_LCU") in {"1", "true", "True"}), "Ouverture automatique des replays", "REPLAY_USE_LCU=1 dans .env")

    for key in ("REPLAY_LCU_WINDOWS_SCRIPT", "REPLAY_API_WINDOWS_SCRIPT"):
        value = env.get(key, "")
        if value:
            report(Path(value).is_file(), key, value)

    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main())
