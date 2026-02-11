"""
Tennis live data client for SofaScore6 API (via RapidAPI).

Simple synchronous client using http.client. Tracks API call count
to stay within rate limits. Drop-in alternative to AllSportsAPI TennisClient.

Uses http.client instead of urllib because SofaScore's Cloudflare
blocks urllib's default User-Agent (error 1010).

Free tier: 500 requests/month (~16/day)
Pro tier:  $5/mo — 30,000 req/mo (~1,000/day), 60 req/min

Available endpoints (verified):
  /api/sofascore/v1/match/live?sport_slug=tennis     — live matches
  /api/sofascore/v1/match/statistics?match_id={id}   — serve stats, aces, etc.
  /api/sofascore/v1/match/odds?match_id={id}         — betting odds
  /api/sofascore/v1/match/votes?match_id={id}        — fan votes

NOT available on this API (404):
  - Event detail by ID
  - Incidents / point-by-point
  - Scheduled events
"""
from __future__ import annotations

import http.client
import json

# Free tier: 500/month ≈ 16/day
MONTHLY_LIMIT = 500
WARN_THRESHOLD = 400

HOST = "sofascore6.p.rapidapi.com"


class SofaScoreClient:

    def __init__(self, api_key: str):
        self.headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": HOST,
        }
        self.call_count = 0
        self.remaining: int | None = None  # from API rate-limit headers

    def _get(self, path: str):
        """Make a GET request, increment call counter, return parsed JSON."""
        if self.remaining is not None and self.remaining <= 0:
            raise RuntimeError(
                "API rate limit reached (0 remaining). "
                "Wait or upgrade your plan."
            )

        conn = http.client.HTTPSConnection(HOST, timeout=15)
        conn.request("GET", path, headers=self.headers)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()

        if resp.status != 200:
            raise RuntimeError(
                f"SofaScore6 API error: {resp.status} {resp.reason} "
                f"on {path} — {body.decode()[:200]}"
            )

        data = json.loads(body)

        # Read actual remaining calls from RapidAPI headers
        remaining_hdr = resp.getheader("X-RateLimit-Requests-Remaining")
        if remaining_hdr is not None:
            self.remaining = int(remaining_hdr)

        self.call_count += 1
        return data

    def get_live_matches(self) -> list[dict]:
        """Fetch all currently live tennis matches.

        Returns a flat list of event dicts. Schema per event:
          id, slug, timestamp, status{type, description, ...},
          homeTeam{id, name, shortName, nameCode, ranking, ...},
          awayTeam{...},
          homeScore{current, display, period1..5, point},
          awayScore{...},
          tournament{id, name, category{name: "ATP"/"WTA"}},
          season, round
        """
        data = self._get("/api/sofascore/v1/match/live?sport_slug=tennis")
        # Response is a flat list, not {"events": [...]}
        if isinstance(data, list):
            return data
        return data.get("events", data.get("data", []))

    def get_statistics(self, match_id: int) -> list[dict]:
        """Fetch match statistics (aces, double faults, serve %, etc.).

        Returns a list of period stats, each with groups of stat items.
        Periods: "ALL", "1", "2", etc.
        """
        return self._get(
            f"/api/sofascore/v1/match/statistics?match_id={match_id}"
        )

    def get_odds(self, match_id: int) -> list[dict]:
        """Fetch betting odds for a match."""
        return self._get(
            f"/api/sofascore/v1/match/odds?match_id={match_id}"
        )

    def get_votes(self, match_id: int) -> dict:
        """Fetch fan vote data for a match."""
        return self._get(
            f"/api/sofascore/v1/match/votes?match_id={match_id}"
        )

    def find_match_for_kalshi(self, kalshi_ticker: str) -> dict | None:
        """Find the SofaScore event matching a Kalshi tennis ticker.

        Reuses the same parsing/matching logic from the AllSportsAPI client
        since SofaScore uses the same homeTeam/awayTeam schema with nameCode.

        Note: SofaScore uses 'timestamp' instead of 'startTimestamp',
        but match_event() handles both.
        """
        from src.tennis.client import match_event, parse_kalshi_ticker

        parsed = parse_kalshi_ticker(kalshi_ticker)
        if not parsed:
            return None

        events = self.get_live_matches()
        return match_event(parsed, events)
