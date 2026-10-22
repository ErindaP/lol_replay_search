from __future__ import annotations

import base64
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import requests

from .config import Settings


def _log(msg: str) -> None:
    print(f"[lcu] {msg}", flush=True)



def _extract_game_id(match_id: str) -> str:
    raw = match_id.strip()
    for sep in ("_", "-"):
        if sep in raw:
            raw = raw.split(sep)[-1]
            break
    if not raw.isdigit():
        raise ValueError(f"Cannot extract numeric gameId from match_id={match_id}")
    return raw



def _to_windows_path(path: str) -> str:
    try:
        proc = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, check=False)
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and out:
            return out
    except Exception:
        pass
    return path


def _load_match_replay_context(settings: Settings, match_id: str, game_id: str) -> dict[str, str]:
    candidates = [
        settings.data_raw_dir / f"{match_id}.match.json",
        settings.data_raw_dir / f"{match_id.replace('-', '_')}.match.json",
    ]
    if "_" in match_id:
        candidates.append(settings.data_raw_dir / f"{match_id.replace('_', '-')}.match.json")
    candidates.append(settings.data_raw_dir / f"EUW1_{game_id}.match.json")
    candidates.append(settings.data_raw_dir / f"EUW1-{game_id}.match.json")

    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            info = data.get("info", {})
            game_version = str(info.get("gameVersion") or "").strip()
            game_type = str(info.get("gameType") or "").strip()
            queue_id = str(info.get("queueId") or "").strip()
            out: dict[str, str] = {}
            if game_version:
                out["game_version"] = game_version
            if game_type:
                out["game_type"] = game_type
            if queue_id:
                out["queue_id"] = queue_id
            if out:
                _log(f"loaded replay context from {path.name}: {out}")
            return out
        except Exception:
            continue
    return {}


def _run_lcu_via_windows_script(settings: Settings, match_id: str, game_id: str, launch_client: bool) -> tuple[bool, str]:
    script_path = settings.replay_lcu_windows_script
    if not os.path.exists(script_path):
        return False, f"Windows LCU script not found: {script_path}"

    script_win = _to_windows_path(script_path)
    riot_client_win = _to_windows_path(settings.riot_client_path)
    league_dir_win = _to_windows_path(settings.league_dir)
    replay_dir_win = ""
    if settings.replay_file_template:
        try:
            replay_dir = Path(settings.replay_file_template.format(match_id=match_id)).parent
            replay_dir_win = _to_windows_path(str(replay_dir))
        except Exception:
            replay_dir_win = ""

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        script_win,
        "-GameId",
        str(game_id),
        "-RiotClientPath",
        riot_client_win,
        "-LeagueDir",
        league_dir_win,
    ]
    if replay_dir_win:
        cmd.extend(["-ReplayDir", replay_dir_win])
    if launch_client:
        cmd.append("-LaunchClient")
    ctx = _load_match_replay_context(settings, match_id, game_id)
    if "game_version" in ctx:
        cmd.extend(["-GameVersion", ctx["game_version"]])
    if "game_type" in ctx:
        cmd.extend(["-GameType", ctx["game_type"]])
    if "queue_id" in ctx:
        cmd.extend(["-QueueId", ctx["queue_id"]])

    _log("starting Windows LCU script...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    lines: list[str] = []
    started = time.time()
    timeout_s = 420.0
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if line:
            s = line.rstrip()
            lines.append(s)
            print(s, flush=True)
        if proc.poll() is not None:
            break
        if time.time() - started > timeout_s:
            proc.kill()
            return False, f"Windows LCU script timeout after {timeout_s:.0f}s"

    rc = int(proc.returncode or 0)
    merged = "\n".join(lines).strip()
    if rc == 0:
        return True, merged or "Windows LCU script succeeded"
    return False, f"Windows LCU script failed rc={rc} output={merged[-1200:]}"


def _launch_lol_client(riot_client_path: str) -> None:
    if not os.path.exists(riot_client_path):
        raise FileNotFoundError(f"RiotClientServices.exe not found at {riot_client_path}")

    riot_client_win = _to_windows_path(riot_client_path)
    _log(f"launching Riot client: {riot_client_win}")
    cmd = (
        f'cmd.exe /C start "" "{riot_client_win}" '
        "--launch-product=league_of_legends --launch-patchline=live"
    )
    proc = subprocess.run(
        ["bash", "-lc", cmd],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode != 0:
        detail = {"stdout": (proc.stdout or "")[-500:], "stderr": (proc.stderr or "")[-500:]}
        raise RuntimeError(f"Failed launching Riot client: {json.dumps(detail, ensure_ascii=False)}")



def _wait_for_lockfile(lockfile: Path, timeout_s: float = 90.0) -> str:
    _log(f"waiting for lockfile: {lockfile}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if lockfile.exists():
            return lockfile.read_text(encoding="utf-8").strip()
        time.sleep(1.0)
    raise TimeoutError(f"Lockfile not found: {lockfile}")



def _parse_lockfile(raw: str) -> dict[str, Any]:
    parts = raw.split(":")
    if len(parts) != 5:
        raise ValueError(f"Unexpected lockfile format: {raw}")
    process_name, pid, port, password, protocol = parts
    return {
        "process_name": process_name,
        "pid": int(pid),
        "port": int(port),
        "password": password,
        "protocol": protocol,
        "base_url": f"{protocol}://127.0.0.1:{port}",
    }



def _make_session(lock: dict[str, Any]) -> requests.Session:
    auth = base64.b64encode(f"riot:{lock['password']}".encode("utf-8")).decode("ascii")
    session = requests.Session()
    session.headers.update(
        {
            "Authorization": f"Basic {auth}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
    )
    session.verify = False
    requests.packages.urllib3.disable_warnings()  # type: ignore[attr-defined]
    return session



def _wait_for_lcu_ready(session: requests.Session, base_url: str, timeout_s: float = 120.0) -> None:
    _log(f"waiting for LCU ready: {base_url}")
    endpoints = [
        "/lol-service-status/v1/lcu-status",
        "/lol-login/v1/session",
        "/lol-summoner/v1/current-summoner",
    ]
    deadline = time.time() + timeout_s
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            for ep in endpoints:
                response = session.get(f"{base_url}{ep}", timeout=5)
                # Treat non-5xx as signal that LCU HTTP API is up.
                if response.status_code < 500:
                    _log(f"LCU endpoint reachable on attempt={attempt} ep={ep} status={response.status_code}")
                    return
                _log(f"LCU probe attempt={attempt} ep={ep} status={response.status_code}")
        except Exception as exc:
            _log(f"LCU probe attempt={attempt} error={exc}")
        time.sleep(2.0)
    raise TimeoutError("LCU did not become ready in time")


def _wait_for_lcu_from_lockfile(lockfile: Path, timeout_s: float = 120.0) -> tuple[dict[str, Any], requests.Session, str]:
    endpoints = [
        "/lol-service-status/v1/lcu-status",
        "/lol-login/v1/session",
        "/lol-summoner/v1/current-summoner",
    ]
    deadline = time.time() + timeout_s
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            raw = lockfile.read_text(encoding="utf-8").strip()
            lock = _parse_lockfile(raw)
            session = _make_session(lock)
            base_url = str(lock["base_url"])

            for ep in endpoints:
                try:
                    response = session.get(f"{base_url}{ep}", timeout=5)
                    if response.status_code < 500:
                        _log(
                            "LCU connected via lockfile "
                            f"attempt={attempt} port={lock['port']} ep={ep} status={response.status_code}"
                        )
                        return lock, session, base_url
                    _log(
                        "LCU probe via lockfile "
                        f"attempt={attempt} port={lock['port']} ep={ep} status={response.status_code}"
                    )
                except Exception as ep_exc:
                    _log(
                        "LCU endpoint connect error "
                        f"attempt={attempt} port={lock['port']} ep={ep} error={ep_exc}"
                    )
        except Exception as exc:
            _log(f"LCU lockfile parse/read error attempt={attempt} error={exc}")
        time.sleep(2.0)
    raise TimeoutError("LCU did not become reachable from lockfile in time")



def _watch_replay(session: requests.Session, base_url: str, game_id: str) -> tuple[bool, int, str]:
    response = session.post(f"{base_url}/lol-replays/v1/rofls/{game_id}/watch", timeout=15)
    return response.status_code in (200, 201, 204), response.status_code, response.text



def _download_replay(session: requests.Session, base_url: str, game_id: str) -> tuple[bool, int, str]:
    response = session.post(f"{base_url}/lol-replays/v1/rofls/{game_id}/download", timeout=15)
    return response.status_code in (200, 201, 204), response.status_code, response.text



def watch_match_via_lcu(
    settings: Settings,
    match_id: str,
    *,
    launch_client: bool = True,
    lockfile_timeout_s: float = 90.0,
    lcu_ready_timeout_s: float = 120.0,
    download_wait_s: float = 10.0,
    watch_retries: int = 12,
    watch_retry_sleep_s: float = 5.0,
) -> tuple[bool, str]:
    game_id = _extract_game_id(match_id)
    _log(f"match_id={match_id} game_id={game_id}")
    executor = (settings.replay_lcu_executor or "auto").lower().strip()
    if executor not in {"auto", "wsl", "windows"}:
        executor = "auto"

    if executor == "windows":
        _log("executor=windows: delegating to PowerShell LCU script")
        return _run_lcu_via_windows_script(settings, match_id, game_id, launch_client=launch_client)

    league_dir = Path(settings.league_dir)
    lockfile = league_dir / "lockfile"
    _log(f"league_dir={league_dir}")

    if launch_client:
        _launch_lol_client(settings.riot_client_path)

    _wait_for_lockfile(lockfile, timeout_s=lockfile_timeout_s)
    try:
        lock, session, base_url = _wait_for_lcu_from_lockfile(lockfile, timeout_s=lcu_ready_timeout_s)
    except Exception as exc:
        if executor == "auto":
            _log(f"executor=auto: WSL LCU failed ({exc}), trying Windows script fallback")
            return _run_lcu_via_windows_script(settings, match_id, game_id, launch_client=launch_client)
        raise
    _log("LCU ready, trying watch")

    ok_watch, code_watch, body_watch = _watch_replay(session, base_url, game_id)
    if ok_watch:
        return True, f"LCU watch started directly for gameId={game_id}"

    _log(f"direct watch failed (http {code_watch}), trying download")
    ok_dl, code_dl, body_dl = _download_replay(session, base_url, game_id)
    if not ok_dl:
        return (
            False,
            "LCU watch failed and download failed. "
            f"watch_http={code_watch} watch_body={body_watch[:240]} "
            f"download_http={code_dl} download_body={body_dl[:240]}",
        )

    time.sleep(download_wait_s)
    _log("download requested, retrying watch")
    for _ in range(watch_retries):
        ok_watch, code_watch, body_watch = _watch_replay(session, base_url, game_id)
        if ok_watch:
            return True, f"LCU watch started after download for gameId={game_id}"
        time.sleep(watch_retry_sleep_s)

    return (
        False,
        "LCU download succeeded but watch still failed after retries. "
        f"last_watch_http={code_watch} last_watch_body={body_watch[:240]}",
    )
