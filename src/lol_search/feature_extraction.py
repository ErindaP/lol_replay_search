from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .map_zones import (
    is_bot_side_zone,
    is_enemy_jungle_for_team,
    is_jungle_zone,
    is_top_side_zone,
    lane_for_zone,
    normalize_token,
    primary_zone,
    zones_for_point,
)
from .models import WindowRecord

KILL_EVENT = "CHAMPION_KILL"
SPECIAL_KILL_EVENT = "CHAMPION_SPECIAL_KILL"
ELITE_MONSTER_EVENT = "ELITE_MONSTER_KILL"
BUILDING_EVENT = "BUILDING_KILL"
LANING_END_MS = 14 * 60 * 1000
EARLY_GAME_END_MS = 14 * 60 * 1000
MID_GAME_END_MS = 25 * 60 * 1000



def _team_for_participant(participant_id: int | None) -> str | None:
    if participant_id is None:
        return None
    return "blue" if participant_id <= 5 else "red"


def _phase_for_window(start_ms: int, end_ms: int) -> str:
    mid = (start_ms + end_ms) // 2
    if mid <= EARLY_GAME_END_MS:
        return "early"
    if mid <= MID_GAME_END_MS:
        return "mid"
    return "late"


def _participant_context(match: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ctx: dict[str, dict[str, Any]] = {}
    for participant in match.get("info", {}).get("participants", []):
        pid = str(participant.get("participantId", ""))
        if not pid:
            continue
        team_side = "blue" if int(participant.get("teamId", 0)) == 100 else "red"
        champion = str(participant.get("championName") or "")
        role = str(participant.get("teamPosition") or participant.get("individualPosition") or participant.get("lane") or "").lower()
        player_name = str(participant.get("riotIdGameName") or participant.get("summonerName") or "").strip()
        tagline = str(participant.get("riotIdTagline") or "").strip()
        player_aliases = _player_aliases(player_name)
        ctx[pid] = {
            "participant_id": int(participant.get("participantId", 0)),
            "team_side": team_side,
            "champion": champion,
            "champion_norm": normalize_token(champion),
            "role": role,
            "lane": role,
            "player_name": player_name,
            "player_name_norm": normalize_token(player_name),
            "player_aliases_norm": player_aliases,
            "riot_id": f"{player_name}#{tagline}" if player_name and tagline else player_name,
        }
    return ctx


def _player_aliases(player_name: str) -> list[str]:
    aliases: list[str] = []
    full = normalize_token(player_name)
    if full:
        aliases.append(full)
    for chunk in re_split_player_name(player_name):
        norm = normalize_token(chunk)
        if len(norm) >= 3:
            aliases.append(norm)
    return _unique(aliases)


def re_split_player_name(player_name: str) -> list[str]:
    cleaned = "".join(ch if ch.isalnum() else " " for ch in player_name)
    return [part for part in cleaned.split() if part]


def _role_pid(ctx: dict[str, dict[str, Any]], team_side: str, role: str) -> str | None:
    role = role.lower()
    for pid, pdata in ctx.items():
        if pdata.get("team_side") == team_side and pdata.get("role") == role:
            return pid
    return None


def _lane_matchups(ctx: dict[str, dict[str, Any]]) -> tuple[dict[str, str], list[str]]:
    out: dict[str, str] = {}
    pairs: list[str] = []
    for role in ("top", "jungle", "middle", "bottom", "utility"):
        blue_pid = _role_pid(ctx, "blue", role)
        red_pid = _role_pid(ctx, "red", role)
        if not blue_pid or not red_pid:
            continue
        blue = str(ctx[blue_pid].get("champion", ""))
        red = str(ctx[red_pid].get("champion", ""))
        out[role] = f"{blue} vs {red}"
        bn = normalize_token(blue)
        rn = normalize_token(red)
        pairs.append(f"{role}:{bn}:{rn}")
        pairs.append(f"{role}:{rn}:{bn}")
    return out, pairs


def _frame_positions(frame: dict[str, Any], ctx: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pid, pdata in frame.get("participantFrames", {}).items():
        pos = pdata.get("position")
        if not isinstance(pos, dict) or "x" not in pos or "y" not in pos:
            continue
        champ = ctx.get(str(pid), {})
        x = float(pos["x"])
        y = float(pos["y"])
        zone = primary_zone(x, y)
        out[str(pid)] = {
            "x": x,
            "y": y,
            "zone": zone,
            "zones": zones_for_point(x, y),
            "team_side": champ.get("team_side", ""),
            "champion": champ.get("champion", ""),
            "role": champ.get("role", ""),
        }
    return out


def _frames_for_window(frames: list[dict[str, Any]], start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    selected = [f for f in frames if start_ms <= int(f.get("timestamp", 0)) < end_ms]
    if selected:
        return selected
    mid = (start_ms + end_ms) // 2
    best = None
    best_dt = 10**18
    for frame in frames:
        dt = abs(int(frame.get("timestamp", 0)) - mid)
        if dt < best_dt:
            best = frame
            best_dt = dt
    # The public timeline is sparse; keep the nearest snapshot only when it is
    # close enough to describe this window's macro state.
    if best is not None and best_dt <= 45_000:
        return [best]
    return []


def _event_zone(event: dict[str, Any]) -> str:
    pos = event.get("position")
    if isinstance(pos, dict) and "x" in pos and "y" in pos:
        return primary_zone(float(pos["x"]), float(pos["y"]))
    return ""


def _next_objective_delta(events: list[dict[str, Any]], end_ms: int, monster_type: str, max_delta_ms: int) -> int | None:
    candidates: list[int] = []
    for event in events:
        if event.get("type") != ELITE_MONSTER_EVENT or event.get("monsterType") != monster_type:
            continue
        ts = int(event.get("timestamp", 0))
        if end_ms <= ts <= end_ms + max_delta_ms:
            candidates.append(ts - end_ms)
    return min(candidates) if candidates else None


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            out.append(value)
            seen.add(value)
    return out


def _multikill_label(length: int) -> str:
    labels = {
        2: "double kill",
        3: "triple kill",
        4: "quadra kill",
        5: "penta kill",
    }
    return labels.get(length, f"{length} kill multi")



def _iter_events(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    frames = timeline.get("info", {}).get("frames", [])
    events: list[dict[str, Any]] = []
    for frame in frames:
        for event in frame.get("events", []):
            events.append(event)
    events.sort(key=lambda e: e.get("timestamp", 0))
    return events



def _iter_frames(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    frames = timeline.get("info", {}).get("frames", [])
    return sorted(frames, key=lambda f: f.get("timestamp", 0))



def _frame_at_or_before(frames: list[dict[str, Any]], timestamp: int) -> dict[str, Any]:
    candidate = frames[0]
    for frame in frames:
        if frame.get("timestamp", 0) <= timestamp:
            candidate = frame
        else:
            break
    return candidate



def _team_gold(frame: dict[str, Any], team: str) -> int:
    pframes = frame.get("participantFrames", {})
    pids = range(1, 6) if team == "blue" else range(6, 11)
    total = 0
    for pid in pids:
        pdata = pframes.get(str(pid), {})
        total += int(pdata.get("totalGold", 0))
    return total



def _team_move(frames: list[dict[str, Any]], start_ms: int, end_ms: int, team: str) -> float:
    seq = [f for f in frames if start_ms <= f.get("timestamp", 0) <= end_ms]
    if len(seq) < 2:
        return 0.0

    pids = range(1, 6) if team == "blue" else range(6, 11)
    total_dist = 0.0
    for pid in pids:
        prev = None
        for frame in seq:
            pframe = frame.get("participantFrames", {}).get(str(pid), {})
            pos = pframe.get("position")
            if not pos:
                continue
            point = (float(pos.get("x", 0.0)), float(pos.get("y", 0.0)))
            if prev is not None:
                total_dist += math.dist(prev, point)
            prev = point
    return total_dist



def _event_summary(events: list[dict[str, Any]]) -> str:
    if not events:
        return "No major events"

    labels: list[str] = []
    for event in events:
        etype = event.get("type", "")
        if etype == KILL_EVENT:
            labels.append("kill")
        elif etype == ELITE_MONSTER_EVENT:
            monster = event.get("monsterType", "monster").lower()
            labels.append(monster)
        elif etype == BUILDING_EVENT:
            btype = event.get("buildingType", "building").lower()
            labels.append(btype)

    if not labels:
        return "Skirmish / rotations"
    return ", ".join(labels[:8])



def _record_text(record: WindowRecord) -> str:
    tags = []
    if record.kills_total >= 4:
        tags.append("teamfight")
    if record.dragon_kills > 0:
        tags.append("dragon fight")
    if record.baron_kills > 0:
        tags.append("baron contest")
    if record.kills_total == 0 and record.gold_abs_swing > 900:
        tags.append("macro gain no kills")
    if record.multikill_max >= 2:
        tags.append(f"{_multikill_label(record.multikill_max)} {'/'.join(record.multikill_champions)}")
    if record.ace:
        tags.append("ace")
    if record.first_blood:
        tags.append("first blood")
    if record.jungler_invade:
        tags.append(f"{record.invade_team} jungler invade {','.join(record.invade_zones)}")
    if record.mid_roam:
        tags.append(f"{record.roam_team} mid roam {','.join(record.roam_target_zones)}")
    if record.mid_jungle_pair:
        tags.append(f"{record.pair_team} mid jungle pair {record.pair_zone}")
    if record.before_dragon:
        tags.append("before dragon")

    tag_text = ", ".join(tags) if tags else "rotation"
    matchup_text = " ".join(f"{lane}:{matchup}" for lane, matchup in record.lane_matchups.items())
    champion_text = " champions=" + ",".join(record.champions) if record.champions else ""
    player_text = " players=" + ",".join(record.player_names) if record.player_names else ""
    kill_zone_text = " kill_zones=" + ",".join(record.kill_zones) if record.kill_zones else ""
    multikill_text = (
        " multikills=" + ",".join(
            f"{event.get('champion')}:{event.get('length')}" for event in record.multikill_events
        )
        if record.multikill_events
        else ""
    )
    return (
        f"{record.match_id} window {record.start_ms//1000}-{record.end_ms//1000}s | "
        f"{tag_text} | kills={record.kills_total} blue={record.kills_blue} red={record.kills_red} "
        f"dragons={record.dragon_kills} barons={record.baron_kills} towers={record.tower_kills} "
        f"gold_swing={record.gold_abs_swing} phase={record.phase} "
        f"blue_jungler={record.blue_jungler_champion} red_jungler={record.red_jungler_champion} "
        f"blue_mid={record.blue_mid_champion} red_mid={record.red_mid_champion}{champion_text}{player_text}{kill_zone_text}{multikill_text} "
        f"matchups={matchup_text} move_blue={record.move_blue:.0f} move_red={record.move_red:.0f}"
    )



def extract_windows_from_match(
    match: dict[str, Any],
    timeline: dict[str, Any],
    window_seconds: int = 30,
    step_seconds: int = 10,
) -> list[WindowRecord]:
    frames = _iter_frames(timeline)
    if not frames:
        return []
    events = _iter_events(timeline)
    participants = _participant_context(match)
    lane_matchups, lane_matchup_pairs = _lane_matchups(participants)
    champions = _unique([str(p.get("champion", "")) for p in participants.values()])
    champions_norm = _unique([str(p.get("champion_norm", "")) for p in participants.values()])
    blue_champions = _unique([str(p.get("champion", "")) for p in participants.values() if p.get("team_side") == "blue"])
    red_champions = _unique([str(p.get("champion", "")) for p in participants.values() if p.get("team_side") == "red"])
    player_names = _unique([str(p.get("player_name", "")) for p in participants.values()])
    player_aliases_norm = _unique([alias for p in participants.values() for alias in p.get("player_aliases_norm", [])])
    player_roles: dict[str, str] = {}
    player_teams: dict[str, str] = {}
    player_champions: dict[str, str] = {}
    for pdata in participants.values():
        for alias in pdata.get("player_aliases_norm", []):
            player_roles[str(alias)] = str(pdata.get("role", ""))
            player_teams[str(alias)] = str(pdata.get("team_side", ""))
            player_champions[str(alias)] = str(pdata.get("champion", ""))
    blue_jungle_pid = _role_pid(participants, "blue", "jungle")
    red_jungle_pid = _role_pid(participants, "red", "jungle")
    blue_mid_pid = _role_pid(participants, "blue", "middle")
    red_mid_pid = _role_pid(participants, "red", "middle")
    blue_jungler_champion = str(participants.get(blue_jungle_pid or "", {}).get("champion", ""))
    red_jungler_champion = str(participants.get(red_jungle_pid or "", {}).get("champion", ""))
    blue_mid_champion = str(participants.get(blue_mid_pid or "", {}).get("champion", ""))
    red_mid_champion = str(participants.get(red_mid_pid or "", {}).get("champion", ""))

    duration_ms = int(frames[-1].get("timestamp", 0))
    window_ms = window_seconds * 1000
    step_ms = step_seconds * 1000

    match_id = match.get("metadata", {}).get("matchId", "unknown_match")
    out: list[WindowRecord] = []

    start = 0
    while start + window_ms <= duration_ms:
        end = start + window_ms
        win_events = [e for e in events if start <= e.get("timestamp", 0) < end]

        kills_blue = 0
        kills_red = 0
        dragon_kills = 0
        baron_kills = 0
        tower_kills = 0
        kill_champions: list[str] = []
        kill_champions_norm: list[str] = []
        multikill_max = 0
        multikill_champions: list[str] = []
        multikill_champions_norm: list[str] = []
        multikill_events: list[dict[str, Any]] = []
        ace = False
        first_blood = False
        involved_champions: list[str] = []
        involved_champions_norm: list[str] = []
        kill_lanes: list[str] = []
        kill_zones: list[str] = []

        for event in win_events:
            etype = event.get("type")
            killer_id = event.get("killerId")
            killer_team = _team_for_participant(killer_id)

            if etype == KILL_EVENT:
                if killer_team == "blue":
                    kills_blue += 1
                elif killer_team == "red":
                    kills_red += 1
                ids = [event.get("killerId"), event.get("victimId"), *event.get("assistingParticipantIds", [])]
                for raw_pid in ids:
                    if raw_pid is None:
                        continue
                    pdata = participants.get(str(raw_pid))
                    if not pdata:
                        continue
                    champion = str(pdata.get("champion", ""))
                    champion_norm = str(pdata.get("champion_norm", ""))
                    involved_champions.append(champion)
                    involved_champions_norm.append(champion_norm)
                for raw_pid in (event.get("killerId"), event.get("victimId")):
                    pdata = participants.get(str(raw_pid))
                    if pdata:
                        kill_champions.append(str(pdata.get("champion", "")))
                        kill_champions_norm.append(str(pdata.get("champion_norm", "")))
                zone = _event_zone(event)
                if zone:
                    kill_zones.append(zone)
                    lane = lane_for_zone(zone)
                    if lane:
                        kill_lanes.append(lane)
            elif etype == ELITE_MONSTER_EVENT:
                if event.get("monsterType") == "DRAGON":
                    dragon_kills += 1
                if event.get("monsterType") == "BARON_NASHOR":
                    baron_kills += 1
            elif etype == BUILDING_EVENT:
                tower_kills += 1
            elif etype == SPECIAL_KILL_EVENT:
                kill_type = str(event.get("killType") or "")
                pdata = participants.get(str(killer_id))
                champion = str(pdata.get("champion", "")) if pdata else ""
                champion_norm = str(pdata.get("champion_norm", "")) if pdata else ""
                zone = _event_zone(event)
                if kill_type == "KILL_MULTI":
                    length = int(event.get("multiKillLength") or 0)
                    if length >= 2:
                        multikill_max = max(multikill_max, length)
                        multikill_champions.append(champion)
                        multikill_champions_norm.append(champion_norm)
                        multikill_events.append(
                            {
                                "champion": champion,
                                "champion_norm": champion_norm,
                                "length": length,
                                "timestamp": int(event.get("timestamp", 0)),
                                "zone": zone,
                            }
                        )
                        if champion:
                            involved_champions.append(champion)
                            involved_champions_norm.append(champion_norm)
                        if zone:
                            kill_zones.append(zone)
                            lane = lane_for_zone(zone)
                            if lane:
                                kill_lanes.append(lane)
                elif kill_type == "KILL_ACE":
                    ace = True
                elif kill_type == "KILL_FIRST_BLOOD":
                    first_blood = True

        start_frame = _frame_at_or_before(frames, start)
        end_frame = _frame_at_or_before(frames, end)

        blue_gold_delta = _team_gold(end_frame, "blue") - _team_gold(start_frame, "blue")
        red_gold_delta = _team_gold(end_frame, "red") - _team_gold(start_frame, "red")
        active_zones: list[str] = []
        invade_team = ""
        invade_champion = ""
        invade_zones: list[str] = []
        roam_team = ""
        roam_champion = ""
        roam_target_zones: list[str] = []
        pair_team = ""
        pair_zone = ""

        for frame in _frames_for_window(frames, start, end):
            positions = _frame_positions(frame, participants)
            for pdata in positions.values():
                active_zones.extend(pdata.get("zones", []))

            for team_side, jungle_pid in (("blue", blue_jungle_pid), ("red", red_jungle_pid)):
                if not jungle_pid or jungle_pid not in positions:
                    continue
                zone = str(positions[jungle_pid].get("zone", ""))
                if is_enemy_jungle_for_team(zone, team_side):
                    invade_team = team_side
                    invade_champion = str(positions[jungle_pid].get("champion", ""))
                    invade_zones.append(zone)

            for team_side, mid_pid in (("blue", blue_mid_pid), ("red", red_mid_pid)):
                if not mid_pid or mid_pid not in positions:
                    continue
                zone = str(positions[mid_pid].get("zone", ""))
                if zone in {"top_lane", "bot_lane"} or is_jungle_zone(zone):
                    roam_team = team_side
                    roam_champion = str(positions[mid_pid].get("champion", ""))
                    roam_target_zones.append(zone)

            for team_side, mid_pid, jungle_pid in (
                ("blue", blue_mid_pid, blue_jungle_pid),
                ("red", red_mid_pid, red_jungle_pid),
            ):
                if not mid_pid or not jungle_pid or mid_pid not in positions or jungle_pid not in positions:
                    continue
                mid_pos = positions[mid_pid]
                jungle_pos = positions[jungle_pid]
                dist = math.dist((float(mid_pos["x"]), float(mid_pos["y"])), (float(jungle_pos["x"]), float(jungle_pos["y"])))
                if dist <= 2500:
                    pair_team = team_side
                    pair_zone = str(mid_pos.get("zone") or jungle_pos.get("zone") or "")

        seconds_to_next_dragon_ms = _next_objective_delta(events, end, "DRAGON", 120_000)
        seconds_to_next_dragon = None if seconds_to_next_dragon_ms is None else int(seconds_to_next_dragon_ms // 1000)
        before_dragon = seconds_to_next_dragon is not None

        record = WindowRecord(
            match_id=match_id,
            start_ms=start,
            end_ms=end,
            duration_s=window_seconds,
            kills_total=kills_blue + kills_red,
            kills_blue=kills_blue,
            kills_red=kills_red,
            dragon_kills=dragon_kills,
            baron_kills=baron_kills,
            tower_kills=tower_kills,
            gold_blue_delta=blue_gold_delta,
            gold_red_delta=red_gold_delta,
            gold_abs_swing=abs(blue_gold_delta - red_gold_delta),
            move_blue=_team_move(frames, start, end, "blue"),
            move_red=_team_move(frames, start, end, "red"),
            text="",
            phase=_phase_for_window(start, end),
            champions=champions,
            champions_norm=champions_norm,
            blue_champions=blue_champions,
            red_champions=red_champions,
            player_names=player_names,
            player_aliases_norm=player_aliases_norm,
            player_roles=player_roles,
            player_teams=player_teams,
            player_champions=player_champions,
            lane_matchups=lane_matchups,
            lane_matchup_pairs=lane_matchup_pairs,
            kill_champions=_unique(kill_champions),
            kill_champions_norm=_unique(kill_champions_norm),
            multikill_max=multikill_max,
            multikill_champions=_unique(multikill_champions),
            multikill_champions_norm=_unique(multikill_champions_norm),
            multikill_events=multikill_events,
            ace=ace,
            first_blood=first_blood,
            involved_champions=_unique(involved_champions),
            involved_champions_norm=_unique(involved_champions_norm),
            kill_lanes=_unique(kill_lanes),
            kill_zones=_unique(kill_zones),
            active_zones=_unique(active_zones),
            blue_jungler_champion=blue_jungler_champion,
            red_jungler_champion=red_jungler_champion,
            blue_mid_champion=blue_mid_champion,
            red_mid_champion=red_mid_champion,
            jungler_invade=bool(invade_zones),
            invade_team=invade_team,
            invade_champion=invade_champion,
            invade_zones=_unique(invade_zones),
            mid_roam=bool(roam_target_zones),
            roam_team=roam_team,
            roam_champion=roam_champion,
            roam_target_zones=_unique(roam_target_zones),
            mid_jungle_pair=bool(pair_zone),
            pair_team=pair_team,
            pair_zone=pair_zone,
            before_dragon=before_dragon,
            seconds_to_next_dragon=seconds_to_next_dragon,
        )

        summary = _event_summary(win_events)
        record.text = f"{_record_text(record)} | events: {summary}"
        out.append(record)
        start += step_ms

    return out



def build_windows_dataset(raw_dir: Path, output_path: Path, window_seconds: int = 30, step_seconds: int = 10) -> int:
    match_files = sorted(raw_dir.glob("*.match.json"))
    total = 0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for match_path in match_files:
            timeline_path = match_path.with_name(match_path.name.replace(".match.json", ".timeline.json"))
            if not timeline_path.exists():
                continue

            match = json.loads(match_path.read_text(encoding="utf-8"))
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            records = extract_windows_from_match(match, timeline, window_seconds=window_seconds, step_seconds=step_seconds)

            for record in records:
                f.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")
                total += 1

    return total
