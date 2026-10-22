from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any
import time
import joblib

from .clip_export import ClipRequest, build_web_manifest, export_clip
from .config import load_settings
from .embedding import Embedder, EmbeddingConfig
from .feature_extraction import build_windows_dataset
from .index_store import IndexStore
from .lcu_replay import watch_match_via_lcu
from .map_zones import normalize_token, zone_center
from .play_similarity import PlaySimilarity
from .query_engine import QueryEngine, load_windows_jsonl
from .rag import QueryInterpreter
from .replay_api import ReplayAPIClient, ensure_replay_api_reachable
from .replay_orchestrator import ReplayOrchestrator
from .riot_api import RiotAPI, save_json



def _parse_riot_id(riot_id: str) -> tuple[str, str]:
    if "#" not in riot_id:
        raise ValueError("Riot ID must be in format gameName#tagLine")
    game_name, tag_line = riot_id.split("#", 1)
    return game_name, tag_line



def cmd_ingest_summoner(args: argparse.Namespace) -> None:
    settings = load_settings()
    platform = args.platform or settings.riot_platform
    region = args.region or settings.riot_region

    api = RiotAPI(api_key=settings.riot_api_key, platform=platform, region=region)
    game_name, tag_line = _parse_riot_id(args.riot_id)

    account = api.get_account_by_riot_id(game_name, tag_line)
    puuid = account["puuid"]
    match_ids = api.get_match_ids(puuid, count=args.count)

    for match_id in match_ids:
        match = api.get_match(match_id)
        timeline = api.get_timeline(match_id)
        save_json(settings.data_raw_dir / f"{match_id}.match.json", match)
        save_json(settings.data_raw_dir / f"{match_id}.timeline.json", timeline)

    print(json.dumps({"status": "ok", "fetched_matches": len(match_ids), "raw_dir": str(settings.data_raw_dir)}))



def cmd_build_windows(args: argparse.Namespace) -> None:
    settings = load_settings()
    output_path = settings.data_processed_dir / "windows.jsonl"
    count = build_windows_dataset(
        settings.data_raw_dir,
        output_path,
        window_seconds=args.window_seconds,
        step_seconds=args.step_seconds,
    )
    print(json.dumps({"status": "ok", "windows": count, "output": str(output_path)}))



def cmd_build_index(args: argparse.Namespace) -> None:
    settings = load_settings()
    windows_path = settings.data_processed_dir / "windows.jsonl"
    rows = load_windows_jsonl(windows_path)
    if not rows:
        raise RuntimeError(f"No windows found in {windows_path}. Run build-windows first.")

    texts = [str(r.get("text", "")) for r in rows]
    embedder = Embedder(
        EmbeddingConfig(
            model_name=args.embedding_model,
            use_pca=args.use_pca,
            pca_dim=args.pca_dim,
        )
    )
    embeddings = embedder.fit_transform(texts)

    store = IndexStore(settings.data_index_dir)
    info = {
        "embedder_provider": embedder.provider,
        "embedding_model": args.embedding_model,
        "use_pca": args.use_pca,
        "pca_dim": args.pca_dim,
        "rows": len(rows),
        "dim": int(embeddings.shape[1]),
    }
    store.save(embeddings=embeddings, metadata=rows, info=info)
    if embedder._pca is not None:
        joblib.dump(embedder._pca, store.pca_path)
    elif store.pca_path.exists():
        store.pca_path.unlink()
    print(json.dumps({"status": "ok", "index_dir": str(settings.data_index_dir), "info": info}))



def _print_results(rows: list[dict[str, Any]]) -> None:
    for i, row in enumerate(rows, start=1):
        head = (
            f"[{i}] {row.get('match_id')} {int(row.get('start_ms', 0))//1000}-{int(row.get('end_ms', 0))//1000}s"
            f" | kills={row.get('kills_total')} dragon={row.get('dragon_kills')}"
            f" baron={row.get('baron_kills')} gold_swing={row.get('gold_abs_swing')}"
        )
        if row.get("phase"):
            head += f" phase={row.get('phase')}"
        if row.get("jungler_invade"):
            head += f" invade={row.get('invade_team')}:{','.join(row.get('invade_zones', []))}"
        if row.get("mid_roam"):
            head += f" roam={row.get('roam_team')}:{','.join(row.get('roam_target_zones', []))}"
        if row.get("mid_jungle_pair"):
            head += f" mid_jungle={row.get('pair_team')}:{row.get('pair_zone')}"
        if int(row.get("multikill_max", 0)) >= 2:
            head += f" multikill={row.get('multikill_max')}:{','.join(row.get('multikill_champions', []))}"
        if row.get("ace"):
            head += " ace=1"
        if row.get("first_blood"):
            head += " first_blood=1"
        if row.get("matched_player_alias"):
            head += (
                f" player={row.get('matched_player_alias')}"
                f":{row.get('matched_player_role')}"
                f":{row.get('matched_player_champion')}"
            )
        if "score" in row:
            head += f" score={float(row['score']):.4f}"
        print(head)
        print(f"    {row.get('text', '')}")



def _resolve_query_rows(args: argparse.Namespace, engine: QueryEngine, interpreter: QueryInterpreter) -> list[dict[str, Any]]:
    parsed = interpreter.parse_with_llm(args.text) if args.use_llm_parser else interpreter.parse(args.text)
    filters = parsed.filters
    if filters:
        return engine.filter_search(top_k=args.top_k, **filters)
    return engine.semantic_search(parsed.semantic_text, top_k=args.top_k)


def _timeline_candidates(settings: Any, match_id: str) -> list[Path]:
    out = [
        settings.data_raw_dir / f"{match_id}.timeline.json",
        settings.data_raw_dir / f"{match_id.replace('-', '_')}.timeline.json",
    ]
    if "_" in match_id:
        out.append(settings.data_raw_dir / f"{match_id.replace('_', '-')}.timeline.json")
    return out


def _match_candidates(settings: Any, match_id: str) -> list[Path]:
    out = [
        settings.data_raw_dir / f"{match_id}.match.json",
        settings.data_raw_dir / f"{match_id.replace('-', '_')}.match.json",
    ]
    if "_" in match_id:
        out.append(settings.data_raw_dir / f"{match_id.replace('_', '-')}.match.json")
    return out


def _load_participant_scene_context(settings: Any, match_id: str) -> dict[str, dict[str, str]]:
    def player_aliases(player_name: str) -> list[str]:
        out: list[str] = []
        full = normalize_token(player_name)
        if full:
            out.append(full)
        cleaned = "".join(ch if ch.isalnum() else " " for ch in player_name)
        for part in cleaned.split():
            norm = normalize_token(part)
            if len(norm) >= 3 and norm not in out:
                out.append(norm)
        return out

    for path in _match_candidates(settings, match_id):
        if not path.exists():
            continue
        try:
            match = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out: dict[str, dict[str, str]] = {}
        for participant in match.get("info", {}).get("participants", []):
            pid = str(participant.get("participantId", ""))
            if not pid:
                continue
            team_side = "blue" if int(participant.get("teamId", 0)) == 100 else "red"
            champion = str(participant.get("championName") or "")
            role = str(participant.get("teamPosition") or participant.get("individualPosition") or participant.get("lane") or "").lower()
            player_name = str(participant.get("riotIdGameName") or participant.get("summonerName") or "").strip()
            out[pid] = {
                "team_side": team_side,
                "champion": champion,
                "champion_norm": normalize_token(champion),
                "role": role,
                "player_name": player_name,
                "player_aliases_norm": ",".join(player_aliases(player_name)),
            }
        return out
    return {}


def _to_windows_path(path: str) -> str:
    try:
        proc = subprocess.run(["wslpath", "-w", path], capture_output=True, text=True, check=False)
        out = (proc.stdout or "").strip()
        if proc.returncode == 0 and out:
            return out
    except Exception:
        pass
    return path


def _run_windows_replay_api_action(
    settings: Any,
    *,
    seek_s: float,
    camera_x: float | None,
    camera_y: float | None,
    no_record: bool,
    start_s: float,
    end_s: float,
    codec: str,
    output_path: Path | None,
    timeout_s: float,
    replay_api_timeout_s: float,
) -> dict[str, Any]:
    script_path = settings.replay_api_windows_script
    if not os.path.exists(script_path):
        raise RuntimeError(f"Replay API Windows script not found: {script_path}")

    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        _to_windows_path(script_path),
        "-SeekTimeS",
        str(float(seek_s)),
        "-ReplayApiBaseUrl",
        str(settings.replay_api_base_url),
        "-ReadyTimeoutSec",
        str(int(replay_api_timeout_s)),
        "-RecordTimeoutSec",
        str(int(max(30.0, timeout_s))),
    ]
    if camera_x is not None and camera_y is not None:
        cmd.extend(["-CameraX", str(float(camera_x)), "-CameraY", str(float(camera_y))])
    if no_record:
        cmd.append("-NoRecord")
    else:
        if output_path is not None:
            cmd.extend(["-OutputPath", _to_windows_path(str(output_path))])
        cmd.extend(
            [
                "-StartTimeS",
                str(float(start_s)),
                "-EndTimeS",
                str(float(end_s)),
                "-Codec",
                str(codec),
            ]
        )

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    lines: list[str] = []
    assert proc.stdout is not None
    while True:
        line = proc.stdout.readline()
        if line:
            s = line.rstrip()
            lines.append(s)
            print(s, flush=True)
        if proc.poll() is not None:
            break
    combined = "\n".join(lines).strip()
    parsed: dict[str, Any] | None = None
    for line in reversed([ln.strip() for ln in lines if ln.strip()]):
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                parsed = obj
                break
        except Exception:
            continue

    rc = int(proc.returncode or 0)
    if rc != 0:
        raise RuntimeError(f"Windows Replay API script failed rc={rc} output={combined[-1200:]}")
    if parsed is None:
        raise RuntimeError(f"Windows Replay API script returned no JSON payload: {combined[-1200:]}")
    return parsed


def _estimate_camera_xy(settings: Any, row: dict[str, Any]) -> tuple[float | None, float | None]:
    match_id = str(row.get("match_id", ""))
    start_ms = int(row.get("start_ms", 0))
    end_ms = int(row.get("end_ms", 0))
    if not match_id or end_ms <= start_ms:
        return None, None

    timeline = None
    for path in _timeline_candidates(settings, match_id):
        if path.exists():
            timeline = json.loads(path.read_text(encoding="utf-8"))
            break
    if not isinstance(timeline, dict):
        return None, None

    def as_list(value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return list(value)
        return [value]

    def average_points(points: list[tuple[float, float]]) -> tuple[float | None, float | None]:
        if not points:
            return None, None
        return sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points)

    def target_participant_ids() -> set[str]:
        participants = _load_participant_scene_context(settings, match_id)
        ids: set[str] = set()

        matched_player_alias = normalize_token(str(row.get("matched_player_alias", "")))
        if matched_player_alias:
            for pid, pdata in participants.items():
                aliases = {normalize_token(alias) for alias in str(pdata.get("player_aliases_norm", "")).split(",")}
                if matched_player_alias in aliases:
                    ids.add(pid)
            if ids:
                return ids

        def add_by_role(team_side: str, role: str) -> None:
            for pid, pdata in participants.items():
                if pdata.get("team_side") == team_side and pdata.get("role") == role:
                    ids.add(pid)

        if row.get("jungler_invade") and row.get("invade_team"):
            add_by_role(str(row.get("invade_team")), "jungle")
        if row.get("mid_roam") and row.get("roam_team"):
            add_by_role(str(row.get("roam_team")), "middle")
        if row.get("mid_jungle_pair") and row.get("pair_team"):
            add_by_role(str(row.get("pair_team")), "middle")
            add_by_role(str(row.get("pair_team")), "jungle")

        wanted_champions = {normalize_token(str(v)) for v in as_list(row.get("involved_champions_norm"))}
        wanted_champions.update(normalize_token(str(v)) for v in as_list(row.get("kill_champions_norm")))
        wanted_champions.update(normalize_token(str(v)) for v in as_list(row.get("multikill_champions_norm")))
        wanted_champions.discard("")
        for pid, pdata in participants.items():
            if pdata.get("champion_norm") in wanted_champions:
                ids.add(pid)
        return ids

    def scene_zone_centers() -> list[tuple[float, float]]:
        zone_names: list[str] = []
        if row.get("jungler_invade"):
            zone_names.extend(str(v) for v in as_list(row.get("invade_zones")))
        if row.get("mid_roam"):
            zone_names.extend(str(v) for v in as_list(row.get("roam_target_zones")))
        if row.get("mid_jungle_pair") and row.get("pair_zone"):
            zone_names.append(str(row.get("pair_zone")))
        zone_names.extend(str(v) for v in as_list(row.get("kill_zones")))
        if int(row.get("dragon_kills", 0)) > 0 or row.get("before_dragon"):
            zone_names.append("dragon_pit")
        if int(row.get("baron_kills", 0)) > 0:
            zone_names.append("baron_pit")

        out: list[tuple[float, float]] = []
        seen: set[str] = set()
        for zone_name in zone_names:
            if not zone_name or zone_name in seen:
                continue
            seen.add(zone_name)
            center = zone_center(zone_name)
            if center is not None:
                out.append(center)
        return out

    frames = timeline.get("info", {}).get("frames", [])

    mid_ms = (start_ms + end_ms) // 2
    best = None
    best_dt = 10**18
    for frame in frames:
        ts = int(frame.get("timestamp", 0))
        dt = abs(ts - mid_ms)
        if dt < best_dt:
            best = frame
            best_dt = dt

    # If the query matched a player, keep the camera on that player first.
    if row.get("matched_player_alias") and isinstance(best, dict):
        pframes = best.get("participantFrames", {})
        target_points: list[tuple[float, float]] = []
        for pid in target_participant_ids():
            pos = pframes.get(str(pid), {}).get("position")
            if isinstance(pos, dict) and "x" in pos and "y" in pos:
                target_points.append((float(pos["x"]), float(pos["y"])))
        if target_points:
            return average_points(target_points)

    event_points: list[tuple[float, float]] = []
    for frame in frames:
        for event in frame.get("events", []):
            ts = int(event.get("timestamp", 0))
            if ts < start_ms or ts >= end_ms:
                continue
            pos = event.get("position")
            if isinstance(pos, dict) and "x" in pos and "y" in pos:
                event_points.append((float(pos["x"]), float(pos["y"])))

    # For fights, kills, towers and objectives, event coordinates are usually
    # the best camera anchor.
    if event_points and (
        int(row.get("kills_total", 0)) > 0
        or int(row.get("dragon_kills", 0)) > 0
        or int(row.get("baron_kills", 0)) > 0
        or int(row.get("tower_kills", 0)) > 0
    ):
        return average_points(event_points)

    # For invades/roams without an event, aim at the relevant role/champion.
    if isinstance(best, dict):
        pframes = best.get("participantFrames", {})
        target_ids = target_participant_ids()
        target_points: list[tuple[float, float]] = []
        for pid in target_ids:
            pos = pframes.get(str(pid), {}).get("position")
            if isinstance(pos, dict) and "x" in pos and "y" in pos:
                target_points.append((float(pos["x"]), float(pos["y"])))
        if target_points:
            return average_points(target_points)

    zone_points = scene_zone_centers()
    if zone_points:
        return average_points(zone_points)

    if event_points:
        return average_points(event_points)

    # Last fallback: participant positions around the middle of the window.
    if isinstance(best, dict):
        fallback_points: list[tuple[float, float]] = []
        pframes = best.get("participantFrames", {})
        for i in range(1, 11):
            pos = pframes.get(str(i), {}).get("position")
            if isinstance(pos, dict) and "x" in pos and "y" in pos:
                fallback_points.append((float(pos["x"]), float(pos["y"])))
        if fallback_points:
            return average_points(fallback_points)
    return None, None



def cmd_query(args: argparse.Namespace) -> None:
    settings = load_settings()
    engine = QueryEngine(settings.data_index_dir)
    interpreter = QueryInterpreter(openai_api_key=settings.openai_api_key)

    rows = _resolve_query_rows(args, engine, interpreter)
    if not rows:
        print("No results")
        return
    _print_results(rows)



def cmd_query_precise(args: argparse.Namespace) -> None:
    settings = load_settings()
    engine = QueryEngine(settings.data_index_dir)

    if args.no_kills:
        rows = engine.top_macro_no_kill(window_seconds=args.window_seconds, top_k=args.top_k)
    else:
        rows = engine.filter_search(
            min_kills=args.min_kills,
            max_kills=args.max_kills,
            requires_dragon=args.requires_dragon,
            requires_baron=args.requires_baron,
            duration_s=args.window_seconds,
            sort_by=args.sort_by,
            descending=not args.ascending,
            top_k=args.top_k,
        )

    if not rows:
        print("No results")
        return
    _print_results(rows)


def cmd_similar_clips(args: argparse.Namespace) -> None:
    settings = load_settings()
    engine = PlaySimilarity(settings.data_index_dir, settings.data_clips_dir)
    if not args.clip:
        print(json.dumps({"clips": [clip.path.name for clip in engine.clips]}, ensure_ascii=False, indent=2))
        return
    results = engine.similar(
        args.clip,
        text_weight=args.text_weight,
        gameplay_weight=args.gameplay_weight,
        video_weight=args.video_weight,
        top_k=args.top_k,
    )
    print(json.dumps({"anchor": args.clip, "results": results}, ensure_ascii=False, indent=2))



def cmd_export_clip(args: argparse.Namespace) -> None:
    settings = load_settings()
    orchestrator = ReplayOrchestrator(settings)

    prep = orchestrator.prepare_replay(
        match_id=args.match_id,
        auto_fetch=args.auto_fetch,
        auto_load=args.auto_load,
    )
    request = ClipRequest(
        match_id=args.match_id,
        start_ms=args.start_ms,
        end_ms=args.end_ms,
        title=args.title or "",
    )
    item = export_clip(
        request=request,
        output_dir=settings.data_clips_dir,
        pad_before_s=args.pad_before_s,
        pad_after_s=args.pad_after_s,
        codec=args.codec,
        verify_tls=args.verify_tls,
        timeout_s=args.timeout_s,
    )
    print(
        json.dumps(
            {"status": "ok", "replay_prepare": prep.to_dict(), "clip": item},
            ensure_ascii=False,
            indent=2,
        )
    )



def cmd_export_clips_from_query(args: argparse.Namespace) -> None:
    settings = load_settings()
    orchestrator = ReplayOrchestrator(settings)
    engine = QueryEngine(settings.data_index_dir)
    interpreter = QueryInterpreter(openai_api_key=settings.openai_api_key)
    rows = _resolve_query_rows(args, engine, interpreter)
    if not rows:
        print("No results")
        return

    selected = rows[: args.export_k]
    exports: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    replay_prepare: list[dict[str, Any]] = []

    for i, row in enumerate(selected, start=1):
        title = f"result_{i}"
        request = ClipRequest(
            match_id=str(row.get("match_id", "")),
            start_ms=int(row.get("start_ms", 0)),
            end_ms=int(row.get("end_ms", 0)),
            title=title,
        )
        try:
            prep = orchestrator.prepare_replay(
                match_id=request.match_id,
                auto_fetch=args.auto_fetch,
                auto_load=args.auto_load,
            )
            replay_prepare.append(prep.to_dict())

            item = export_clip(
                request=request,
                output_dir=settings.data_clips_dir,
                pad_before_s=args.pad_before_s,
                pad_after_s=args.pad_after_s,
                codec=args.codec,
                verify_tls=args.verify_tls,
                timeout_s=args.timeout_s,
            )
            exports.append(item)
            print(f"[ok] exported {title} -> {item['output']}")
        except Exception as exc:
            failures.append(
                {
                    "title": title,
                    "match_id": request.match_id,
                    "start_ms": request.start_ms,
                    "end_ms": request.end_ms,
                    "error": str(exc),
                }
            )
            print(f"[error] {title}: {exc}")
            if not args.continue_on_error:
                break

    manifest_path = Path(args.manifest_path) if args.manifest_path else (settings.data_clips_dir / "manifest.json")
    manifest = build_web_manifest(exports, manifest_path, base_url=args.base_url or "")
    payload = {
        "status": "ok",
        "exported": len(exports),
        "failed": len(failures),
        "manifest_path": str(manifest_path),
        "manifest_clips": len(manifest.get("clips", [])),
        "replay_prepare": replay_prepare,
        "failures": failures,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_replay_watch_lcu(args: argparse.Namespace) -> None:
    settings = load_settings()
    ok, msg = watch_match_via_lcu(settings, args.match_id, launch_client=not args.no_launch_client)
    payload = {"status": "ok" if ok else "error", "match_id": args.match_id, "message": msg}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not ok:
        raise RuntimeError(msg)


def cmd_query_open_result(args: argparse.Namespace) -> None:
    settings = load_settings()
    engine = QueryEngine(settings.data_index_dir)
    orchestrator = ReplayOrchestrator(settings)
    direct = (args.match_id, args.start_ms, args.end_ms)
    if any(value is not None for value in direct):
        if not all(value is not None for value in direct):
            raise ValueError("--match-id, --start-ms and --end-ms must be provided together")
        row = next((item for item in engine.metadata if
                    str(item.get("match_id")) == args.match_id and
                    int(item.get("start_ms", -1)) == args.start_ms and
                    int(item.get("end_ms", -1)) == args.end_ms), None)
        if row is None:
            raise ValueError("Selected window is not present in the current index")
    else:
        if not args.text:
            raise ValueError("Provide --text or an indexed window (--match-id, --start-ms, --end-ms)")
        interpreter = QueryInterpreter(openai_api_key=settings.openai_api_key)
        rows = _resolve_query_rows(args, engine, interpreter)
        if not rows:
            raise RuntimeError("No results for query")
        idx = int(args.result_index) - 1
        if idx < 0 or idx >= len(rows):
            raise RuntimeError(f"result-index={args.result_index} out of range (top-k={len(rows)})")
        row = rows[idx]
    match_id = str(row.get("match_id", ""))
    start_ms = int(row.get("start_ms", 0))
    end_ms = int(row.get("end_ms", 0))
    if not match_id or end_ms <= start_ms:
        raise RuntimeError(f"Invalid selected row: {row}")

    prep = orchestrator.prepare_replay(
        match_id=match_id,
        auto_fetch=args.auto_fetch,
        auto_load=not args.no_auto_load,
    )

    camera_x, camera_y = _estimate_camera_xy(settings, row)
    seek_s = max(0.0, start_ms / 1000.0 - args.pad_before_s)
    start_s = start_ms / 1000.0
    end_s = end_ms / 1000.0
    print(
        "[query-open] target from query "
        f"match={match_id} window_ms={start_ms}-{end_ms} window_s={start_s:.3f}-{end_s:.3f} "
        f"seek_s={seek_s:.3f}",
        flush=True,
    )

    camera_set = False
    camera_error = ""
    clip_item: dict[str, Any] | None = None
    record_ok = bool(args.no_record)
    replay_api_mode = (settings.replay_api_executor or "auto").lower().strip()
    if replay_api_mode not in {"auto", "local", "windows"}:
        replay_api_mode = "auto"

    use_windows_fallback = replay_api_mode == "windows"
    local_error = ""
    if not use_windows_fallback:
        client = ReplayAPIClient(base_url=settings.replay_api_base_url, verify_tls=args.verify_tls)
        ready_deadline = time.time() + float(args.replay_api_timeout_s)
        last_err = ""
        while time.time() < ready_deadline:
            try:
                ensure_replay_api_reachable(client)
                last_err = ""
                break
            except Exception as exc:
                last_err = str(exc)
                time.sleep(1.5)
        if last_err:
            local_error = f"Replay API local unreachable: {last_err}"
            if replay_api_mode == "local":
                raise RuntimeError(
                    f"Replay API not ready (mode=local, timeout={args.replay_api_timeout_s}s). Last error: {last_err}"
                )
            use_windows_fallback = True
        else:
            client.set_playback(paused=True, time_s=seek_s, speed=1.0)
            client.wait_until_time(seek_s)
            if not args.no_camera and camera_x is not None and camera_y is not None:
                try:
                    client.set_camera_position(x=camera_x, y=camera_y, z=0.0)
                    camera_set = True
                except Exception as exc:
                    camera_error = str(exc)

            if not args.no_record:
                request = ClipRequest(
                    match_id=match_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    title=args.title or f"query_result_{args.result_index}",
                )
                clip_item = export_clip(
                    request=request,
                    output_dir=settings.data_clips_dir,
                    pad_before_s=args.pad_before_s,
                    pad_after_s=args.pad_after_s,
                    codec=args.codec,
                    verify_tls=args.verify_tls,
                    timeout_s=args.timeout_s,
                    camera_x=camera_x,
                    camera_y=camera_y,
                )
                record_ok = True

    windows_payload: dict[str, Any] | None = None
    if use_windows_fallback:
        start_s = max(0.0, start_ms / 1000.0 - args.pad_before_s)
        end_s = max(start_s + 0.5, end_ms / 1000.0 + args.pad_after_s)
        clip_path: Path | None = None
        if not args.no_record:
            settings.data_clips_dir.mkdir(parents=True, exist_ok=True)
            clip_name = f"{match_id}_{int(start_s*1000)}_{int(end_s*1000)}.mp4"
            clip_path = settings.data_clips_dir / clip_name

        last_exc: Exception | None = None
        for attempt in range(1, int(args.replay_api_retries) + 1):
            try:
                windows_payload = _run_windows_replay_api_action(
                    settings,
                    seek_s=seek_s,
                    camera_x=None if args.no_camera else camera_x,
                    camera_y=None if args.no_camera else camera_y,
                    no_record=bool(args.no_record),
                    start_s=start_s,
                    end_s=end_s,
                    codec=args.codec,
                    output_path=clip_path,
                    timeout_s=args.timeout_s,
                    replay_api_timeout_s=args.replay_api_timeout_s,
                )
                break
            except Exception as exc:
                last_exc = exc
                msg = str(exc)
                can_retry = ("Replay API not ready" in msg) and (attempt < int(args.replay_api_retries))
                if can_retry:
                    print(
                        f"[replay-api] not ready yet (attempt {attempt}/{int(args.replay_api_retries)}), "
                        f"retrying in {args.replay_api_retry_sleep_s}s...",
                        flush=True,
                    )
                    time.sleep(float(args.replay_api_retry_sleep_s))
                    continue
                # Do not hard-fail on persistent Replay API startup lag:
                # keep replay open and return a partial result with diagnostics.
                if "Replay API not ready" in msg:
                    windows_payload = {
                        "status": "error",
                        "message": "Replay API not ready after retries",
                        "detail": msg,
                    }
                    break
                raise
        if windows_payload is None:
            raise RuntimeError(f"Windows Replay API action failed after retries: {last_exc}")
        camera_set = bool(windows_payload.get("camera_set", False))
        replay_action_ok = windows_payload.get("status") == "ok"
        if args.no_record:
            record_ok = replay_action_ok
        if not args.no_record:
            clip_item = {
                "match_id": match_id,
                "title": args.title or f"query_result_{args.result_index}",
                "start_ms": start_ms,
                "end_ms": end_ms,
                "clip_start_s": start_s,
                "clip_end_s": end_s,
                "codec": args.codec,
                "output": windows_payload.get("output", ""),
                "camera_x": camera_x,
                "camera_y": camera_y,
                "camera_applied": camera_set,
                "recording_status": windows_payload,
            }
            record_ok = bool(replay_action_ok and windows_payload.get("output"))

    overall_status = "ok" if record_ok else "partial"

    payload = {
        "status": overall_status,
        "selected_index": int(args.result_index),
        "selected": row,
        "replay_prepare": prep.to_dict(),
        "seek_s": seek_s,
        "camera": {"x": camera_x, "y": camera_y, "set": camera_set, "error": camera_error},
        "recorded": record_ok,
        "clip": clip_item,
        "replay_api": {
            "mode": "windows" if use_windows_fallback else "local",
            "local_error": local_error,
            "windows_payload": windows_payload,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LoL replay semantic + precise search")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest-summoner", help="Fetch matches + timelines from Riot API")
    p_ingest.add_argument("--riot-id", required=True, help="Format gameName#tagLine")
    p_ingest.add_argument("--count", type=int, default=20)
    p_ingest.add_argument("--platform", default=None, help="euw1, na1, kr...")
    p_ingest.add_argument("--region", default=None, help="europe, americas, asia")
    p_ingest.set_defaults(func=cmd_ingest_summoner)

    p_windows = sub.add_parser("build-windows", help="Build time windows dataset from raw timelines")
    p_windows.add_argument("--window-seconds", type=int, default=30)
    p_windows.add_argument("--step-seconds", type=int, default=10)
    p_windows.set_defaults(func=cmd_build_windows)

    p_index = sub.add_parser("build-index", help="Build embedding index")
    p_index.add_argument("--embedding-model", default="sentence-transformers/all-MiniLM-L6-v2")
    p_index.add_argument("--use-pca", action="store_true")
    p_index.add_argument("--pca-dim", type=int, default=128)
    p_index.set_defaults(func=cmd_build_index)

    p_query = sub.add_parser("query", help="Query with natural language")
    p_query.add_argument("--text", required=True)
    p_query.add_argument("--top-k", type=int, default=5)
    p_query.add_argument("--use-llm-parser", action="store_true")
    p_query.set_defaults(func=cmd_query)

    p_precise = sub.add_parser("query-precise", help="Precise filters")
    p_precise.add_argument("--window-seconds", type=int, default=30)
    p_precise.add_argument("--min-kills", type=int, default=None)
    p_precise.add_argument("--max-kills", type=int, default=None)
    p_precise.add_argument("--requires-dragon", action="store_true")
    p_precise.add_argument("--requires-baron", action="store_true")
    p_precise.add_argument("--sort-by", default="gold_abs_swing")
    p_precise.add_argument("--ascending", action="store_true")
    p_precise.add_argument("--top-k", type=int, default=5)
    p_precise.add_argument("--no-kills", action="store_true", help="Shortcut: top windows with 0 kills and max gold swing")
    p_precise.set_defaults(func=cmd_query_precise)

    p_similar = sub.add_parser("similar-clips", help="Find similar plays using text, gameplay and video")
    p_similar.add_argument("--clip", help="Reference clip name; omit to list indexed clips")
    p_similar.add_argument("--top-k", type=int, default=3)
    p_similar.add_argument("--text-weight", type=float, default=1.0)
    p_similar.add_argument("--gameplay-weight", type=float, default=1.0)
    p_similar.add_argument("--video-weight", type=float, default=1.0)
    p_similar.set_defaults(func=cmd_similar_clips)

    p_export = sub.add_parser("export-clip", help="Export one clip from local Replay API")
    p_export.add_argument("--match-id", required=True)
    p_export.add_argument("--start-ms", type=int, required=True)
    p_export.add_argument("--end-ms", type=int, required=True)
    p_export.add_argument("--title", default="")
    p_export.add_argument("--pad-before-s", type=float, default=2.0)
    p_export.add_argument("--pad-after-s", type=float, default=2.0)
    p_export.add_argument("--codec", default="webm")
    p_export.add_argument("--timeout-s", type=float, default=300.0)
    p_export.add_argument("--auto-fetch", action="store_true", help="Attempt automatic replay fetch before export")
    p_export.add_argument("--auto-load", action="store_true", help="Attempt automatic replay load before export")
    p_export.add_argument("--verify-tls", action="store_true", help="Enable TLS cert verification for local replay API")
    p_export.set_defaults(func=cmd_export_clip)

    p_export_query = sub.add_parser("export-clips-from-query", help="Search and export top-k segments + web manifest")
    p_export_query.add_argument("--text", required=True)
    p_export_query.add_argument("--top-k", type=int, default=5, help="Search top-k candidates")
    p_export_query.add_argument("--export-k", type=int, default=3, help="Number of clips to export")
    p_export_query.add_argument("--use-llm-parser", action="store_true")
    p_export_query.add_argument("--pad-before-s", type=float, default=2.0)
    p_export_query.add_argument("--pad-after-s", type=float, default=2.0)
    p_export_query.add_argument("--codec", default="webm")
    p_export_query.add_argument("--timeout-s", type=float, default=300.0)
    p_export_query.add_argument("--auto-fetch", action="store_true", help="Attempt automatic replay fetch before each export")
    p_export_query.add_argument("--auto-load", action="store_true", help="Attempt automatic replay load before each export")
    p_export_query.add_argument("--verify-tls", action="store_true")
    p_export_query.add_argument("--continue-on-error", action="store_true")
    p_export_query.add_argument("--manifest-path", default="")
    p_export_query.add_argument("--base-url", default="", help="Public base URL prefix for videos in manifest")
    p_export_query.set_defaults(func=cmd_export_clips_from_query)

    p_lcu = sub.add_parser("replay-watch-lcu", help="Launch/watch replay through local LCU API")
    p_lcu.add_argument("--match-id", required=True, help="Match id or numeric game id")
    p_lcu.add_argument("--no-launch-client", action="store_true", help="Do not launch Riot client, only attach to running LCU")
    p_lcu.set_defaults(func=cmd_replay_watch_lcu)

    p_qopen = sub.add_parser("query-open-result", help="Query, open selected replay window, set camera and optionally auto-record")
    p_qopen.add_argument("--text", help="Query text (omit when opening an indexed window directly)")
    p_qopen.add_argument("--match-id", help="Exact match id of an indexed window")
    p_qopen.add_argument("--start-ms", type=int, help="Exact start of an indexed window")
    p_qopen.add_argument("--end-ms", type=int, help="Exact end of an indexed window")
    p_qopen.add_argument("--top-k", type=int, default=5)
    p_qopen.add_argument("--result-index", type=int, default=1, help="1-based index inside top-k")
    p_qopen.add_argument("--use-llm-parser", action="store_true")
    p_qopen.add_argument("--auto-fetch", action="store_true", help="Attempt automatic replay fetch before opening")
    p_qopen.add_argument("--no-auto-load", action="store_true", help="Skip automatic replay open/watch")
    p_qopen.add_argument("--pad-before-s", type=float, default=2.0)
    p_qopen.add_argument("--pad-after-s", type=float, default=2.0)
    p_qopen.add_argument("--codec", default="webm")
    p_qopen.add_argument("--timeout-s", type=float, default=300.0)
    p_qopen.add_argument("--replay-api-timeout-s", type=float, default=30.0, help="Max wait for local replay API (127.0.0.1:2999)")
    p_qopen.add_argument("--replay-api-retries", type=int, default=2, help="Retries when replay API is not ready after watch")
    p_qopen.add_argument("--replay-api-retry-sleep-s", type=float, default=5.0, help="Sleep between replay API retries")
    p_qopen.add_argument("--verify-tls", action="store_true")
    p_qopen.add_argument("--title", default="")
    p_qopen.add_argument("--no-camera", action="store_true", help="Disable automatic camera positioning")
    p_qopen.add_argument("--no-record", action="store_true", help="Open/seek/camera only, without MP4 export")
    p_qopen.set_defaults(func=cmd_query_open_result)

    return parser



def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
