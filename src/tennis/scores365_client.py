"""
Tennis live data client for 365Scores (webws.365scores.com).

FREE, no API key required, no observed rate limits.
Provides point-level game scores, serving indicator, set scores, and
player symbolic names — everything needed for Kalshi ticker matching.

Key schema differences from AllSportsAPI/SofaScore:
  - homeCompetitor/awayCompetitor instead of homeTeam/awayTeam
  - symbolicName instead of nameCode
  - inPossession instead of firstToServe
  - Scores in stages[] array (Game, S1, S2, ..., Sets)
  - statusGroup: 2=scheduled, 3=in-progress, 4=ended
"""
from __future__ import annotations

import http.client
import json
from datetime import datetime, timezone

HOST = "webws.365scores.com"
LIVE_PATH = "/web/games/current/?appTypeId=5&langId=1&timezoneName=America/New_York&userCountryId=14&sports=3"


class Scores365Client:

    def __init__(self):
        self.call_count = 0

    def _get(self, path: str) -> dict:
        """Make a GET request, increment call counter, return parsed JSON."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.6778.205 Safari/537.36"
            ),
            "Accept": "application/json",
        }
        conn = http.client.HTTPSConnection(HOST, timeout=15)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()

        if resp.status != 200:
            raise RuntimeError(
                f"365Scores error: {resp.status} {resp.reason} "
                f"on {path} — {body.decode()[:200]}"
            )

        self.call_count += 1
        return json.loads(body)

    def get_live_matches(self) -> list[dict]:
        """Fetch all currently live tennis matches (statusGroup == 3)."""
        data = self._get(LIVE_PATH)
        games = data.get("games", [])
        return [g for g in games if g.get("statusGroup") == 3]

    def get_all_matches(self) -> list[dict]:
        """Fetch all current tennis matches (live + scheduled + ended)."""
        data = self._get(LIVE_PATH)
        return data.get("games", [])

    def get_match_details(self, game_id: int) -> dict:
        """Fetch details for a specific match by game ID."""
        data = self._get(f"/web/games/?games={game_id}")
        games = data.get("games", [])
        if games:
            return games[0]
        return data

    def find_match_for_kalshi(self, kalshi_ticker: str) -> dict | None:
        """Find the 365Scores game matching a Kalshi tennis ticker.

        Uses parse_kalshi_ticker() for parsing, then matches against
        365Scores' symbolicName field (equivalent to AllSportsAPI's nameCode).
        """
        from src.tennis.client import parse_kalshi_ticker

        parsed = parse_kalshi_ticker(kalshi_ticker)
        if not parsed:
            return None

        events = self.get_live_matches()
        return match_365_event(parsed, events)


# ── Matching logic (adapted for 365Scores schema) ──────────────────────────


def match_365_event(parsed: dict, games: list[dict]) -> dict | None:
    """Find the 365Scores game matching parsed Kalshi ticker data.

    Like match_event() but adapted for 365Scores schema:
      - symbolicName instead of nameCode
      - homeCompetitor/awayCompetitor instead of homeTeam/awayTeam
      - startTime (ISO string) instead of startTimestamp (unix int)
      - competition country name for ATP/WTA matching
    """
    code_pair = {parsed["code1"], parsed["code2"]}
    raw_category = parsed["category"]
    target_base = "WTA" if raw_category.startswith("WTA") else "ATP"
    target_date = parsed["date"]

    candidates = []

    for game in games:
        home = game.get("homeCompetitor", {})
        away = game.get("awayCompetitor", {})
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue

        home_code = (home.get("symbolicName") or "").upper()
        away_code = (away.get("symbolicName") or "").upper()

        if {home_code, away_code} != code_pair:
            continue

        # Code match found — score by category + date agreement
        score = 0

        # Check category via competition's country name (365Scores uses "ATP"/"WTA")
        comp_name = (game.get("competitionDisplayName") or "").upper()
        # Also check countries list if available
        if target_base in comp_name:
            score += 2

        # Check date from startTime (ISO format)
        start_time = game.get("startTime")
        if start_time:
            try:
                event_date = datetime.fromisoformat(start_time).date()
                if event_date == target_date:
                    score += 1
            except (ValueError, TypeError):
                pass

        candidates.append((score, game))

    if not candidates:
        return None

    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


# ── Helper functions for extracting data from 365Scores schema ──────────────


def extract_game_score(game: dict) -> tuple[str, str]:
    """Extract current game point score (e.g., '30', '15') from stages."""
    for stage in game.get("stages", []):
        if stage.get("shortName") == "Game":
            h = stage.get("homeCompetitorScore", -1)
            a = stage.get("awayCompetitorScore", -1)
            if h >= 0:
                return str(int(h)), str(int(a))
    return "", ""


def extract_set_scores(game: dict) -> list[tuple[int, int]]:
    """Extract set scores as list of (home, away) tuples."""
    sets = []
    for stage in game.get("stages", []):
        sn = stage.get("shortName", "")
        if sn.startswith("S") and sn != "Sets":
            h = stage.get("homeCompetitorScore", -1)
            a = stage.get("awayCompetitorScore", -1)
            if h >= 0 and a >= 0:
                sets.append((int(h), int(a)))
    return sets


def extract_serving(game: dict) -> int:
    """Return 1 if home is serving, 2 if away, 0 if unknown."""
    home = game.get("homeCompetitor", {})
    away = game.get("awayCompetitor", {})
    if home.get("inPossession"):
        return 1
    if away.get("inPossession"):
        return 2
    return 0
