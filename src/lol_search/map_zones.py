from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MapZone:
    name: str
    category: str
    polygon: tuple[tuple[float, float], ...]


# Approximate Summoner's Rift zones in Riot timeline coordinates.
# They are intentionally coarse: timeline positions are low-frequency, so robust
# macro buckets are more useful than brittle pixel-perfect borders.
MAP_ZONES: tuple[MapZone, ...] = (
    MapZone("blue_base", "base", ((0, 0), (2500, 0), (2500, 2500), (0, 2500))),
    MapZone("red_base", "base", ((12500, 12500), (15000, 12500), (15000, 15000), (12500, 15000))),
    MapZone("dragon_pit", "objective", ((8800, 3600), (10800, 3600), (10800, 5600), (8800, 5600))),
    MapZone("baron_pit", "objective", ((4200, 9400), (6200, 9400), (6200, 11400), (4200, 11400))),
    MapZone("blue_top_jungle", "jungle", ((1800, 6200), (6500, 6200), (6500, 11800), (1800, 11800))),
    MapZone("blue_bot_jungle", "jungle", ((5200, 1600), (11200, 1600), (11200, 6500), (5200, 6500))),
    MapZone("red_top_jungle", "jungle", ((3800, 9000), (9800, 9000), (9800, 13600), (3800, 13600))),
    MapZone("red_bot_jungle", "jungle", ((9000, 3800), (13600, 3800), (13600, 9800), (9000, 9800))),
    MapZone("top_lane", "lane", ((0, 9000), (3800, 9000), (3800, 15000), (0, 15000))),
    MapZone("bot_lane", "lane", ((9000, 0), (15000, 0), (15000, 3800), (9000, 3800))),
)


def normalize_token(value: str | None) -> str:
    if not value:
        return ""
    return "".join(ch for ch in value.lower() if ch.isalnum())


def point_in_polygon(x: float, y: float, polygon: tuple[tuple[float, float], ...]) -> bool:
    inside = False
    j = len(polygon) - 1
    for i, (xi, yi) in enumerate(polygon):
        xj, yj = polygon[j]
        crosses = (yi > y) != (yj > y)
        if crosses:
            x_at_y = (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
            if x < x_at_y:
                inside = not inside
        j = i
    return inside


def zones_for_point(x: float, y: float) -> list[str]:
    zones: list[str] = []
    # Mid lane roughly follows the blue-base to red-base diagonal.
    if 2500 <= x <= 12500 and 2500 <= y <= 12500 and abs(x - y) <= 1200:
        zones.append("mid_lane")
    # River roughly follows the opposite diagonal.
    if 2500 <= x <= 12500 and 2500 <= y <= 12500 and abs((x + y) - 15000) <= 1400:
        zones.append("river")
    for zone in MAP_ZONES:
        if point_in_polygon(x, y, zone.polygon):
            zones.append(zone.name)
    return zones or ["unknown"]


def primary_zone(x: float, y: float) -> str:
    zones = zones_for_point(x, y)
    priority = (
        "dragon_pit",
        "baron_pit",
        "river",
        "mid_lane",
        "top_lane",
        "bot_lane",
        "blue_top_jungle",
        "blue_bot_jungle",
        "red_top_jungle",
        "red_bot_jungle",
        "blue_base",
        "red_base",
    )
    for name in priority:
        if name in zones:
            return name
    return zones[0]


def lane_for_zone(zone: str) -> str | None:
    if zone == "mid_lane":
        return "middle"
    if zone == "top_lane":
        return "top"
    if zone == "bot_lane":
        return "bottom"
    return None


def is_enemy_jungle_for_team(zone: str, team_side: str) -> bool:
    if team_side == "blue":
        return zone in {"red_top_jungle", "red_bot_jungle"}
    if team_side == "red":
        return zone in {"blue_top_jungle", "blue_bot_jungle"}
    return False


def is_jungle_zone(zone: str) -> bool:
    return zone in {"blue_top_jungle", "blue_bot_jungle", "red_top_jungle", "red_bot_jungle"}


def is_bot_side_zone(zone: str) -> bool:
    return zone in {"blue_bot_jungle", "red_bot_jungle", "bot_lane", "dragon_pit"}


def is_top_side_zone(zone: str) -> bool:
    return zone in {"blue_top_jungle", "red_top_jungle", "top_lane", "baron_pit"}


def zone_center(zone_name: str) -> tuple[float, float] | None:
    synthetic = {
        "mid_lane": (7500.0, 7500.0),
        "river": (7500.0, 7500.0),
        "bot_side": (10800.0, 4200.0),
        "top_side": (4200.0, 10800.0),
        "jungle": (7500.0, 7500.0),
    }
    if zone_name in synthetic:
        return synthetic[zone_name]
    for zone in MAP_ZONES:
        if zone.name != zone_name:
            continue
        xs = [p[0] for p in zone.polygon]
        ys = [p[1] for p in zone.polygon]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    return None
