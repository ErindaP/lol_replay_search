from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class WindowRecord:
    match_id: str
    start_ms: int
    end_ms: int
    duration_s: int
    kills_total: int
    kills_blue: int
    kills_red: int
    dragon_kills: int
    baron_kills: int
    tower_kills: int
    gold_blue_delta: int
    gold_red_delta: int
    gold_abs_swing: int
    move_blue: float
    move_red: float
    text: str
    phase: str = ""
    champions: list[str] = field(default_factory=list)
    champions_norm: list[str] = field(default_factory=list)
    blue_champions: list[str] = field(default_factory=list)
    red_champions: list[str] = field(default_factory=list)
    player_names: list[str] = field(default_factory=list)
    player_aliases_norm: list[str] = field(default_factory=list)
    player_roles: dict[str, str] = field(default_factory=dict)
    player_teams: dict[str, str] = field(default_factory=dict)
    player_champions: dict[str, str] = field(default_factory=dict)
    lane_matchups: dict[str, str] = field(default_factory=dict)
    lane_matchup_pairs: list[str] = field(default_factory=list)
    kill_champions: list[str] = field(default_factory=list)
    kill_champions_norm: list[str] = field(default_factory=list)
    multikill_max: int = 0
    multikill_champions: list[str] = field(default_factory=list)
    multikill_champions_norm: list[str] = field(default_factory=list)
    multikill_events: list[dict] = field(default_factory=list)
    ace: bool = False
    first_blood: bool = False
    involved_champions: list[str] = field(default_factory=list)
    involved_champions_norm: list[str] = field(default_factory=list)
    kill_lanes: list[str] = field(default_factory=list)
    kill_zones: list[str] = field(default_factory=list)
    active_zones: list[str] = field(default_factory=list)
    blue_jungler_champion: str = ""
    red_jungler_champion: str = ""
    blue_mid_champion: str = ""
    red_mid_champion: str = ""
    jungler_invade: bool = False
    invade_team: str = ""
    invade_champion: str = ""
    invade_zones: list[str] = field(default_factory=list)
    mid_roam: bool = False
    roam_team: str = ""
    roam_champion: str = ""
    roam_target_zones: list[str] = field(default_factory=list)
    mid_jungle_pair: bool = False
    pair_team: str = ""
    pair_zone: str = ""
    before_dragon: bool = False
    seconds_to_next_dragon: int | None = None

    def to_dict(self) -> dict:
        return {
            "match_id": self.match_id,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "duration_s": self.duration_s,
            "kills_total": self.kills_total,
            "kills_blue": self.kills_blue,
            "kills_red": self.kills_red,
            "dragon_kills": self.dragon_kills,
            "baron_kills": self.baron_kills,
            "tower_kills": self.tower_kills,
            "gold_blue_delta": self.gold_blue_delta,
            "gold_red_delta": self.gold_red_delta,
            "gold_abs_swing": self.gold_abs_swing,
            "move_blue": self.move_blue,
            "move_red": self.move_red,
            "text": self.text,
            "phase": self.phase,
            "champions": self.champions,
            "champions_norm": self.champions_norm,
            "blue_champions": self.blue_champions,
            "red_champions": self.red_champions,
            "player_names": self.player_names,
            "player_aliases_norm": self.player_aliases_norm,
            "player_roles": self.player_roles,
            "player_teams": self.player_teams,
            "player_champions": self.player_champions,
            "lane_matchups": self.lane_matchups,
            "lane_matchup_pairs": self.lane_matchup_pairs,
            "kill_champions": self.kill_champions,
            "kill_champions_norm": self.kill_champions_norm,
            "multikill_max": self.multikill_max,
            "multikill_champions": self.multikill_champions,
            "multikill_champions_norm": self.multikill_champions_norm,
            "multikill_events": self.multikill_events,
            "ace": self.ace,
            "first_blood": self.first_blood,
            "involved_champions": self.involved_champions,
            "involved_champions_norm": self.involved_champions_norm,
            "kill_lanes": self.kill_lanes,
            "kill_zones": self.kill_zones,
            "active_zones": self.active_zones,
            "blue_jungler_champion": self.blue_jungler_champion,
            "red_jungler_champion": self.red_jungler_champion,
            "blue_mid_champion": self.blue_mid_champion,
            "red_mid_champion": self.red_mid_champion,
            "jungler_invade": self.jungler_invade,
            "invade_team": self.invade_team,
            "invade_champion": self.invade_champion,
            "invade_zones": self.invade_zones,
            "mid_roam": self.mid_roam,
            "roam_team": self.roam_team,
            "roam_champion": self.roam_champion,
            "roam_target_zones": self.roam_target_zones,
            "mid_jungle_pair": self.mid_jungle_pair,
            "pair_team": self.pair_team,
            "pair_zone": self.pair_zone,
            "before_dragon": self.before_dragon,
            "seconds_to_next_dragon": self.seconds_to_next_dragon,
        }
