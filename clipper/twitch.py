"""Twitch Helix API: findet die meistgesehenen Clips der letzten Stunden."""
from __future__ import annotations

import datetime as dt
import os

import requests

API = "https://api.twitch.tv/helix"


class Twitch:
    def __init__(self, client_id: str | None = None, client_secret: str | None = None):
        self.client_id = client_id or os.environ["TWITCH_CLIENT_ID"]
        secret = client_secret or os.environ["TWITCH_CLIENT_SECRET"]
        r = requests.post(
            "https://id.twitch.tv/oauth2/token",
            data={"client_id": self.client_id, "client_secret": secret,
                  "grant_type": "client_credentials"},
            timeout=30,
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]

    def _get(self, path: str, params) -> dict:
        r = requests.get(
            f"{API}/{path}", params=params, timeout=30,
            headers={"Client-ID": self.client_id, "Authorization": f"Bearer {self.token}"},
        )
        r.raise_for_status()
        return r.json()

    def game_ids(self, names: list[str]) -> dict[str, str]:
        if not names:
            return {}
        data = self._get("games", [("name", n) for n in names])["data"]
        return {g["id"]: g["name"] for g in data}

    def user_ids(self, logins: list[str]) -> dict[str, str]:
        out = {}
        for i in range(0, len(logins), 100):
            chunk = logins[i:i + 100]
            for u in self._get("users", [("login", l) for l in chunk])["data"]:
                out[u["id"]] = u["login"]
        return out

    def top_clips(self, *, game_id: str | None = None, broadcaster_id: str | None = None,
                  hours: int = 24, pages: int = 3) -> list[dict]:
        start = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours))
        params = {"first": 100, "started_at": start.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if game_id:
            params["game_id"] = game_id
        if broadcaster_id:
            params["broadcaster_id"] = broadcaster_id
        clips, cursor = [], None
        for _ in range(pages):
            if cursor:
                params["after"] = cursor
            resp = self._get("clips", params)
            clips += resp["data"]
            cursor = resp.get("pagination", {}).get("cursor")
            if not cursor:
                break
        return clips

    def live_streams(self, language: str, first: int = 20) -> list[dict]:
        """Die aktuell meistgesehenen Live-Streams einer Sprache."""
        return self._get("streams", {"language": language, "first": first,
                                     "type": "live"})["data"]

    def game_names(self, ids: list[str]) -> dict[str, str]:
        out = {}
        ids = [i for i in dict.fromkeys(ids) if i]
        for i in range(0, len(ids), 100):
            for g in self._get("games", [("id", x) for x in ids[i:i + 100]])["data"]:
                out[g["id"]] = g["name"]
        return out
