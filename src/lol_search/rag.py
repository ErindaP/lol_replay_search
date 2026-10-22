from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .config import load_settings
from .map_zones import normalize_token


@dataclass
class ParsedQuery:
    semantic_text: str
    filters: dict[str, Any]


def _strip_accents(value: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", value) if not unicodedata.combining(ch))


def _time_to_seconds(raw_value: str, unit: str | None) -> int:
    value = int(raw_value)
    unit = (unit or "").lower()
    return value * 60 if unit.startswith("m") else value


PLAYER_ALIAS_STOPWORDS = {
    "early",
    "game",
    "mid",
    "late",
    "kill",
    "kills",
    "team",
    "fight",
    "teamfight",
    "teamfights",
    "invade",
    "invades",
    "jungle",
    "roam",
    "roaming",
    "triple",
    "double",
    "quadra",
    "penta",
    "dragon",
    "baron",
    "herald",
    "tower",
    "lane",
    "midlane",
    "toplane",
    "botlane",
    "earlygame",
    "midgame",
    "lategame",
}


class QueryInterpreter:
    """Rule-based parser with optional LLM upgrade path."""

    def __init__(self, openai_api_key: str | None = None) -> None:
        self.openai_api_key = openai_api_key
        self._champions: list[str] | None = None
        self._players: dict[str, str] | None = None

    def _known_champions(self) -> list[str]:
        if self._champions is not None:
            return self._champions
        names = {
            "Zoe",
            "Malzahar",
            "LeeSin",
            "JarvanIV",
            "Nocturne",
            "Graves",
            "Nidalee",
            "Elise",
            "LeBlanc",
            "Ahri",
            "Orianna",
            "Syndra",
        }
        raw_dir = load_settings().data_raw_dir
        if raw_dir.exists():
            for path in raw_dir.glob("*.match.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for participant in data.get("info", {}).get("participants", []):
                    champion = str(participant.get("championName") or "").strip()
                    if champion:
                        names.add(champion)
        self._champions = sorted(names, key=lambda x: len(x), reverse=True)
        return self._champions

    def _extract_champions(self, q: str) -> list[str]:
        q_plain = _strip_accents(q.lower())
        q_norm = normalize_token(q_plain)
        found_with_pos: list[tuple[int, str]] = []
        seen: set[str] = set()
        for champion in self._known_champions():
            c_norm = normalize_token(champion)
            if not c_norm or c_norm in seen:
                continue
            aliases = {champion.lower(), c_norm}
            split_alias = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", champion).lower()
            aliases.add(split_alias)
            pos = -1
            for alias in sorted(aliases, key=len, reverse=True):
                alias_plain = _strip_accents(alias)
                if len(normalize_token(alias_plain)) <= 3:
                    match = re.search(rf"(?<![a-z0-9]){re.escape(alias_plain)}(?![a-z0-9])", q_plain)
                    alias_pos = match.start() if match else -1
                elif " " in alias_plain:
                    match = re.search(rf"(?<![a-z0-9]){re.escape(alias_plain)}(?![a-z0-9])", q_plain)
                    alias_pos = match.start() if match else -1
                else:
                    alias_pos = q_norm.find(normalize_token(alias_plain))
                if alias_pos >= 0 and (pos < 0 or alias_pos < pos):
                    pos = alias_pos
            if pos >= 0:
                found_with_pos.append((pos, c_norm))
                seen.add(c_norm)
        found_with_pos.sort(key=lambda x: x[0])
        return [champion for _, champion in found_with_pos]

    def _known_players(self) -> dict[str, str]:
        if self._players is not None:
            return self._players
        out: dict[str, str] = {}
        raw_dir = load_settings().data_raw_dir
        if raw_dir.exists():
            for path in raw_dir.glob("*.match.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                for participant in data.get("info", {}).get("participants", []):
                    player_name = str(participant.get("riotIdGameName") or participant.get("summonerName") or "").strip()
                    if not player_name:
                        continue
                    aliases = {normalize_token(player_name)}
                    cleaned = "".join(ch if ch.isalnum() else " " for ch in player_name)
                    aliases.update(normalize_token(part) for part in cleaned.split() if len(normalize_token(part)) >= 4)
                    for alias in aliases:
                        if len(alias) >= 4 and alias not in PLAYER_ALIAS_STOPWORDS:
                            out[alias] = player_name
        self._players = out
        return self._players

    def _extract_players(self, q: str) -> list[str]:
        q_plain = _strip_accents(q.lower())
        q_norm = normalize_token(q_plain)
        found: list[tuple[int, str]] = []
        for alias in self._known_players():
            if alias in PLAYER_ALIAS_STOPWORDS:
                continue
            match = re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", q_plain)
            pos = match.start() if match else -1
            if pos < 0 and " " not in q_plain:
                pos = q_norm.find(alias)
            if pos >= 0:
                found.append((pos, alias))
        found.sort(key=lambda x: (x[0], -len(x[1])))
        out: list[str] = []
        for _, alias in found:
            if not any(alias in existing or existing in alias for existing in out):
                out.append(alias)
        return out

    def parse(self, user_query: str) -> ParsedQuery:
        q = _strip_accents(user_query.lower().strip())
        filters: dict[str, Any] = {}

        if "teamfight" in q or "team fight" in q:
            filters["min_kills"] = 4
        if "dragon" in q:
            filters["requires_dragon"] = True
        if "baron" in q:
            filters["requires_baron"] = True
        if "sans kill" in q or "without kill" in q or "no kill" in q:
            filters["max_kills"] = 0
            filters["min_kills"] = 0
        if "kill" in q or "kills" in q:
            filters.setdefault("min_kills", 1)
        if "double kill" in q or "doublekill" in q:
            filters["multikill_min"] = 2
            filters["multikill_exact"] = 2
            filters.setdefault("min_kills", 2)
        if "triple kill" in q or "triplekill" in q:
            filters["multikill_min"] = 3
            filters["multikill_exact"] = 3
            filters.setdefault("min_kills", 3)
        if "quadra kill" in q or "quadrakill" in q:
            filters["multikill_min"] = 4
            filters["multikill_exact"] = 4
            filters.setdefault("min_kills", 4)
        if "penta kill" in q or "pentakill" in q:
            filters["multikill_min"] = 5
            filters["multikill_exact"] = 5
            filters.setdefault("min_kills", 5)
        if "ace" in q:
            filters["requires_ace"] = True
        if "first blood" in q or "premier sang" in q:
            filters["requires_first_blood"] = True
        if "early" in q or "early game" in q or "laning phase" in q or "phase de lane" in q:
            filters["phase"] = "early"
        if "mid game" in q or "midgame" in q or "milieu de game" in q or "milieu de partie" in q:
            filters["phase"] = "mid"
        if "late game" in q or "lategame" in q or "fin de game" in q or "fin de partie" in q:
            filters["phase"] = "late"

        # Parse duration hints like "30 secondes" / "30 seconds" / "30s"
        m = re.search(r"(\d+)\s*(secondes|seconds|s)\b", q)
        if m:
            filters["duration_s"] = int(m.group(1))

        # Parse time-window hints in natural language.
        between = re.search(
            r"(?:entre|between)\s*(\d+)\s*(minutes?|mins?|m|secondes|seconds|secs?|s)?\s*(?:et|and|-|a|to)\s*(\d+)\s*(minutes?|mins?|m|secondes|seconds|secs?|s)?",
            q,
        )
        if between:
            unit1 = between.group(2) or between.group(4) or "minutes"
            unit2 = between.group(4) or unit1
            filters["min_start_s"] = _time_to_seconds(between.group(1), unit1)
            filters["max_start_s"] = _time_to_seconds(between.group(3), unit2)

        after = re.search(r"(?:apres|after|post)\s*(\d+)\s*(minutes?|mins?|m|secondes|seconds|secs?|s)", q)
        if after:
            filters["min_start_s"] = _time_to_seconds(after.group(1), after.group(2))

        before = re.search(r"(?:avant|before|pre)\s*(\d+)\s*(minutes?|mins?|m|secondes|seconds|secs?|s)", q)
        if before:
            filters["max_start_s"] = _time_to_seconds(before.group(1), before.group(2))

        around = re.search(r"(?:vers|around|a|at)\s*(\d+)\s*(minutes?|mins?|m|secondes|seconds|secs?|s)", q)
        if around and "min_start_s" not in filters and "max_start_s" not in filters:
            center = _time_to_seconds(around.group(1), around.group(2))
            filters["min_start_s"] = max(0, center - 45)
            filters["max_start_s"] = center + 45

        # Parse sort hints
        if "max gold" in q or "plus de gold" in q or "most gold" in q:
            filters["sort_by"] = "gold_abs_swing"
            filters["descending"] = True

        if "dive" in q:
            filters.setdefault("min_kills", 1)
            if "toplane" in q or "top lane" in q or "sur la top" in q:
                filters["kill_lane"] = "top"
            elif "botlane" in q or "bot lane" in q or "sur la bot" in q:
                filters["kill_lane"] = "bottom"
            elif "midlane" in q or "mid lane" in q or "sur la mid" in q:
                filters["kill_lane"] = "middle"

        if "invade" in q or "jungle ennemie" in q or "enemy jungle" in q or "counter jungle" in q:
            filters["requires_invade"] = True
            filters.setdefault("sort_by", "gold_abs_swing")
            filters.setdefault("descending", True)
        if "counter jungle" in q or "counterjungle" in q:
            filters["requires_invade"] = True
        if "roam" in q or "roaming" in q or "deplacement du mid" in q:
            filters["requires_roam"] = True
        if ("mid" in q and "jung" in q and ("ensemble" in q or "joint" in q or "together" in q)) or "mid jungle" in q:
            filters["requires_mid_jungle_pair"] = True
        if "avant dragon" in q or "before dragon" in q:
            filters["requires_before_dragon"] = True
            filters.pop("requires_dragon", None)
        if "blue side" in q or "blue-side" in q or "cote bleu" in q:
            filters["team_side"] = "blue"
        if "red side" in q or "red-side" in q or "cote rouge" in q:
            filters["team_side"] = "red"
        if "botlane" in q or "bot lane" in q or "botlane" in q:
            if filters.get("requires_roam"):
                filters["roam_target"] = "bot_side"
            elif filters.get("requires_invade"):
                filters["invade_zone"] = "bot_side"
            else:
                filters["zone"] = "bot_side"
                filters["kill_lane"] = "bottom"
        if "toplane" in q or "top lane" in q or "toplane" in q:
            if filters.get("requires_roam"):
                filters["roam_target"] = "top_side"
            elif filters.get("requires_invade"):
                filters["invade_zone"] = "top_side"
            else:
                filters["zone"] = "top_side"
                filters["kill_lane"] = "top"
        if "midlane" in q or "mid lane" in q or "midlane" in q or "sur la mid" in q:
            filters["lane"] = "middle"
            if "kill" in q:
                filters["kill_lane"] = "middle"
        if "jungle" in q and filters.get("requires_roam"):
            filters["roam_target"] = "jungle"
        if "bot jungle" in q or "jungle bot" in q:
            filters["invade_zone"] = "bot_side"
        if "top jungle" in q or "jungle top" in q:
            filters["invade_zone"] = "top_side"

        champions = self._extract_champions(user_query)
        if champions:
            filters["champion"] = champions[0]
            if "multikill_min" in filters:
                filters["multikill_champion"] = champions[0]
        if len(champions) >= 2:
            filters["opponent_champion"] = champions[1]
        if champions and ("kill" in q or "tue" in q or "mort" in q):
            filters["involved_champion"] = champions[0]

        players = self._extract_players(user_query)
        if players:
            filters["player"] = players[0]

        semantic_text = user_query
        return ParsedQuery(semantic_text=semantic_text, filters=filters)

    def parse_with_llm(self, user_query: str) -> ParsedQuery:
        """Optional extension: keep stable offline behavior if OpenAI SDK/key are unavailable."""
        if not self.openai_api_key:
            return self.parse(user_query)

        try:
            from openai import OpenAI

            client = OpenAI(api_key=self.openai_api_key)
            prompt = (
                "Convert this LoL replay search query into JSON with keys: "
                "semantic_text, filters. Supported filters include "
                "min_kills,max_kills,requires_dragon,requires_baron,duration_s,sort_by,descending,"
                "phase,min_start_s,max_start_s,min_end_s,max_end_s,champion,opponent_champion,lane,kill_lane,involved_champion,"
                "player,"
                "multikill_min,multikill_exact,multikill_champion,requires_ace,requires_first_blood,"
                "requires_invade,invade_zone,requires_roam,roam_target,requires_mid_jungle_pair,"
                "requires_before_dragon,team_side."
            )
            resp = client.responses.create(
                model="gpt-5-mini",
                input=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": user_query},
                ],
            )
            text = resp.output_text
            data = json.loads(text)
            return ParsedQuery(
                semantic_text=data.get("semantic_text", user_query),
                filters=data.get("filters", {}),
            )
        except Exception:
            return self.parse(user_query)
