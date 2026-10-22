from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import joblib

from .embedding import Embedder, EmbeddingConfig
from .index_store import IndexStore
from .map_zones import is_bot_side_zone, is_top_side_zone, normalize_token


class QueryEngine:
    def __init__(self, index_dir: Path, embedder: Embedder | None = None) -> None:
        self.store = IndexStore(index_dir)
        self.embeddings, self.metadata, self.info = self.store.load()
        provider = str(self.info.get("embedder_provider", "auto"))
        self.embedder = embedder or Embedder(
            EmbeddingConfig(model_name=str(self.info.get("embedding_model") or EmbeddingConfig().model_name)),
            provider=provider,
        )
        if self.info.get("use_pca") and self.store.pca_path.exists():
            self.embedder._pca = joblib.load(self.store.pca_path)

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        q = self.embedder.transform([query])[0]
        embeddings = self.embeddings
        if embeddings.shape[1] != q.shape[0]:
            # Older indexes can be PCA-reduced without a persisted PCA transformer.
            # Rebuild comparable vectors from metadata so semantic search remains available.
            texts = [str(row.get("text", "")) for row in self.metadata]
            embeddings = self.embedder.fit_transform(texts)
            q = self.embedder.transform([query])[0]

        sims = embeddings @ q
        idx = np.argsort(-sims)[:top_k]

        out = []
        for i in idx:
            row = dict(self.metadata[int(i)])
            row["score"] = float(sims[int(i)])
            out.append(row)
        return out

    def filter_search(
        self,
        min_kills: int | None = None,
        max_kills: int | None = None,
        requires_dragon: bool = False,
        requires_baron: bool = False,
        duration_s: int | None = None,
        sort_by: str = "gold_abs_swing",
        descending: bool = True,
        top_k: int = 5,
        **criteria: Any,
    ) -> list[dict[str, Any]]:
        rows = self.metadata

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

        def has_value(row: dict[str, Any], key: str, value: str) -> bool:
            wanted = normalize_token(value)
            values = as_list(row.get(key))
            return wanted in {normalize_token(str(v)) for v in values}

        def player_alias(row: dict[str, Any], value: str) -> str | None:
            wanted = normalize_token(value)
            if not wanted:
                return None
            aliases = [normalize_token(str(v)) for v in as_list(row.get("player_aliases_norm"))]
            for alias in aliases:
                if wanted == alias or wanted in alias or alias in wanted:
                    return alias
            return None

        def has_zone(row: dict[str, Any], value: str) -> bool:
            zones = set(str(v) for v in as_list(row.get("active_zones")))
            zones.update(str(v) for v in as_list(row.get("kill_zones")))
            zones.update(str(v) for v in as_list(row.get("invade_zones")))
            zones.update(str(v) for v in as_list(row.get("roam_target_zones")))
            if row.get("pair_zone"):
                zones.add(str(row.get("pair_zone")))
            if value == "bot_side":
                return any(is_bot_side_zone(z) for z in zones)
            if value == "top_side":
                return any(is_top_side_zone(z) for z in zones)
            if value == "jungle":
                return any("jungle" in z for z in zones)
            return value in zones

        def has_champion_pair(row: dict[str, Any], champion: str, opponent: str, lane: str | None) -> bool:
            c1 = normalize_token(champion)
            c2 = normalize_token(opponent)
            if not c1 or not c2:
                return True
            pairs = [str(p) for p in as_list(row.get("lane_matchup_pairs"))]
            if lane:
                return f"{lane}:{c1}:{c2}" in pairs or f"{lane}:{c2}:{c1}" in pairs
            return any(p.endswith(f":{c1}:{c2}") or p.endswith(f":{c2}:{c1}") for p in pairs)

        def keep(row: dict[str, Any]) -> bool:
            if min_kills is not None and int(row.get("kills_total", 0)) < min_kills:
                return False
            if max_kills is not None and int(row.get("kills_total", 0)) > max_kills:
                return False
            if requires_dragon and int(row.get("dragon_kills", 0)) <= 0:
                return False
            if requires_baron and int(row.get("baron_kills", 0)) <= 0:
                return False
            if duration_s is not None and int(row.get("duration_s", 0)) != duration_s:
                return False
            min_start_s = criteria.get("min_start_s")
            if min_start_s is not None and float(row.get("start_ms", 0)) < float(min_start_s) * 1000:
                return False
            max_start_s = criteria.get("max_start_s")
            if max_start_s is not None and float(row.get("start_ms", 0)) > float(max_start_s) * 1000:
                return False
            min_end_s = criteria.get("min_end_s")
            if min_end_s is not None and float(row.get("end_ms", 0)) < float(min_end_s) * 1000:
                return False
            max_end_s = criteria.get("max_end_s")
            if max_end_s is not None and float(row.get("end_ms", 0)) > float(max_end_s) * 1000:
                return False
            phase = criteria.get("phase")
            if phase and str(row.get("phase", "")) != str(phase):
                return False
            if criteria.get("requires_invade") and not bool(row.get("jungler_invade")):
                return False
            if criteria.get("requires_roam") and not bool(row.get("mid_roam")):
                return False
            if criteria.get("requires_mid_jungle_pair") and not bool(row.get("mid_jungle_pair")):
                return False
            if criteria.get("requires_before_dragon") and not bool(row.get("before_dragon")):
                return False
            team_side = criteria.get("team_side")
            player = criteria.get("player")
            matched_player_alias: str | None = None
            if player:
                matched_player_alias = player_alias(row, str(player))
                if matched_player_alias is None:
                    return False
            if team_side:
                team_side = str(team_side)
                team_fields = {
                    str(row.get("invade_team", "")),
                    str(row.get("roam_team", "")),
                    str(row.get("pair_team", "")),
                }
                if team_side not in team_fields:
                    return False
            if matched_player_alias is not None:
                player_roles = row.get("player_roles", {}) if isinstance(row.get("player_roles"), dict) else {}
                player_teams = row.get("player_teams", {}) if isinstance(row.get("player_teams"), dict) else {}
                player_role = str(player_roles.get(matched_player_alias, ""))
                player_team = str(player_teams.get(matched_player_alias, ""))
                if criteria.get("requires_invade") and player_role == "jungle" and str(row.get("invade_team", "")) != player_team:
                    return False
                if criteria.get("requires_roam") and player_role == "middle" and str(row.get("roam_team", "")) != player_team:
                    return False
                if criteria.get("requires_mid_jungle_pair") and player_role in {"middle", "jungle"} and str(row.get("pair_team", "")) != player_team:
                    return False
            champion = criteria.get("champion")
            opponent = criteria.get("opponent_champion")
            lane = criteria.get("lane")
            if champion and not has_value(row, "champions_norm", str(champion)):
                return False
            if opponent and not has_value(row, "champions_norm", str(opponent)):
                return False
            if champion and opponent and not has_champion_pair(row, str(champion), str(opponent), str(lane) if lane else None):
                return False
            kill_champion = criteria.get("kill_champion")
            if kill_champion and not (
                has_value(row, "kill_champions_norm", str(kill_champion))
                or has_value(row, "involved_champions_norm", str(kill_champion))
            ):
                return False
            involved_champion = criteria.get("involved_champion")
            if involved_champion and not has_value(row, "involved_champions_norm", str(involved_champion)):
                return False
            multikill_min = criteria.get("multikill_min")
            if multikill_min is not None and int(row.get("multikill_max", 0)) < int(multikill_min):
                return False
            multikill_exact = criteria.get("multikill_exact")
            multikill_champion = criteria.get("multikill_champion")
            multikill_events = [event for event in as_list(row.get("multikill_events")) if isinstance(event, dict)]
            if multikill_exact is not None and multikill_champion:
                wanted = normalize_token(str(multikill_champion))
                exact = int(multikill_exact)
                if not any(int(event.get("length", 0)) == exact and normalize_token(str(event.get("champion_norm", ""))) == wanted for event in multikill_events):
                    return False
            elif multikill_exact is not None:
                lengths = {int(event.get("length", 0)) for event in multikill_events}
                if int(multikill_exact) not in lengths:
                    return False
            elif multikill_champion and not has_value(row, "multikill_champions_norm", str(multikill_champion)):
                return False
            if criteria.get("requires_ace") and not bool(row.get("ace")):
                return False
            if criteria.get("requires_first_blood") and not bool(row.get("first_blood")):
                return False
            kill_lane = criteria.get("kill_lane")
            if kill_lane and str(kill_lane) not in {str(v) for v in as_list(row.get("kill_lanes"))}:
                return False
            zone = criteria.get("zone")
            if zone and not has_zone(row, str(zone)):
                return False
            invade_zone = criteria.get("invade_zone")
            if invade_zone:
                zones = {str(v) for v in as_list(row.get("invade_zones"))}
                if invade_zone == "bot_side" and not any(is_bot_side_zone(z) for z in zones):
                    return False
                if invade_zone == "top_side" and not any(is_top_side_zone(z) for z in zones):
                    return False
                if invade_zone == "jungle" and not any("jungle" in z for z in zones):
                    return False
                if invade_zone not in {"bot_side", "top_side", "jungle"} and str(invade_zone) not in zones:
                    return False
            roam_target = criteria.get("roam_target")
            if roam_target:
                zones = {str(v) for v in as_list(row.get("roam_target_zones"))}
                if roam_target == "bot_side" and not any(is_bot_side_zone(z) for z in zones):
                    return False
                if roam_target == "top_side" and not any(is_top_side_zone(z) for z in zones):
                    return False
                if roam_target == "jungle" and not any("jungle" in z for z in zones):
                    return False
                if roam_target not in {"bot_side", "top_side", "jungle"} and str(roam_target) not in zones:
                    return False
            return True

        filtered: list[dict[str, Any]] = []
        for row in rows:
            if not keep(row):
                continue
            item = dict(row)
            player = criteria.get("player")
            if player:
                matched_alias = player_alias(row, str(player))
                if matched_alias:
                    player_roles = row.get("player_roles", {}) if isinstance(row.get("player_roles"), dict) else {}
                    player_teams = row.get("player_teams", {}) if isinstance(row.get("player_teams"), dict) else {}
                    player_champions = row.get("player_champions", {}) if isinstance(row.get("player_champions"), dict) else {}
                    item["matched_player_alias"] = matched_alias
                    item["matched_player_role"] = str(player_roles.get(matched_alias, ""))
                    item["matched_player_team"] = str(player_teams.get(matched_alias, ""))
                    item["matched_player_champion"] = str(player_champions.get(matched_alias, ""))
            filtered.append(item)

        def sort_value(row: dict[str, Any]) -> float:
            value = row.get(sort_by, 0.0)
            if value is None:
                return float("-inf") if descending else float("inf")
            try:
                return float(value)
            except Exception:
                return 0.0

        filtered.sort(key=sort_value, reverse=descending)
        return filtered[:top_k]

    def top_macro_no_kill(self, window_seconds: int = 30, top_k: int = 5) -> list[dict[str, Any]]:
        return self.filter_search(
            min_kills=0,
            max_kills=0,
            duration_s=window_seconds,
            sort_by="gold_abs_swing",
            descending=True,
            top_k=top_k,
        )



def load_windows_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            rows.append(json.loads(line))
    return rows
