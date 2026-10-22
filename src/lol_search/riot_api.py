from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


class RiotAPI:
    def __init__(self, api_key: str, platform: str, region: str, timeout: int = 20) -> None:
        if not api_key:
            raise ValueError("RIOT_API_KEY is missing. Add it to your .env file.")
        self.api_key = api_key
        self.platform = platform
        self.region = region
        self.timeout = timeout

    def _request(self, host: str, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"https://{host}.api.riotgames.com{path}"
        headers = {"X-Riot-Token": self.api_key}
        response = requests.get(url, headers=headers, params=params or {}, timeout=self.timeout)
        if response.status_code >= 400:
            if response.status_code == 401:
                key_preview = f"{self.api_key[:8]}...{self.api_key[-4:]}" if len(self.api_key) >= 12 else "<too_short>"
                raise RuntimeError(
                    "Riot API error 401 (Unknown apikey). "
                    "Verify RIOT_API_KEY is valid and not expired (dev keys expire quickly), "
                    "copied without extra spaces/quotes, and loaded from the current project .env. "
                    f"key_preview={key_preview} url={url} body={response.text}"
                )
            raise RuntimeError(f"Riot API error {response.status_code} on {url}: {response.text}")
        return response.json()

    def get_account_by_riot_id(self, game_name: str, tag_line: str) -> dict[str, Any]:
        game_name_q = quote(game_name, safe="")
        tag_line_q = quote(tag_line, safe="")
        return self._request(
            self.region,
            f"/riot/account/v1/accounts/by-riot-id/{game_name_q}/{tag_line_q}",
        )

    def get_match_ids(self, puuid: str, count: int = 20, start: int = 0) -> list[str]:
        data = self._request(
            self.region,
            f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
            params={"start": start, "count": count},
        )
        return list(data)

    def get_match(self, match_id: str) -> dict[str, Any]:
        return self._request(self.region, f"/lol/match/v5/matches/{match_id}")

    def get_timeline(self, match_id: str) -> dict[str, Any]:
        return self._request(self.region, f"/lol/match/v5/matches/{match_id}/timeline")



def save_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")



def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
