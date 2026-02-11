"""
Tennis live data client for SportAPI7 (via RapidAPI).

Drop-in replacement for AllSportsAPI TennisClient with massively better rate limits.
Same schema, same field names, same event IDs — just a different RapidAPI host.

Uses http.client instead of urllib (same as SofaScore client) to avoid
Cloudflare-related issues.

Free tier: 50 requests/month (~1.6/day)

Endpoints (identical to AllSportsAPI):
  /api/v1/sport/tennis/events/live         — live matches
  /api/v1/event/{id}                       — event details
  /api/v1/event/{id}/point-by-point        — point-by-point data
"""
from __future__ import annotations

import http.client
import json

HOST = "sportapi7.p.rapidapi.com"


class SportAPI7Client:

    def __init__(self, api_key: str):
        self.headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": HOST,
        }
        self.call_count = 0
        self.remaining: int | None = None  # from API rate-limit headers

    def _get(self, path: str) -> dict:
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
                f"SportAPI7 error: {resp.status} {resp.reason} "
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
        """Fetch all currently live tennis matches."""
        data = self._get("/api/v1/sport/tennis/events/live")
        return data.get("events", [])

    def get_match_details(self, event_id: int) -> dict:
        """Fetch details for a specific match."""
        data = self._get(f"/api/v1/event/{event_id}")
        return data.get("event", data)

    def get_point_by_point(self, event_id: int) -> dict:
        """Fetch point-by-point live data for a match."""
        return self._get(f"/api/v1/event/{event_id}/point-by-point")

    def find_match_for_kalshi(self, kalshi_ticker: str) -> dict | None:
        """Find the SportAPI7 event matching a Kalshi tennis ticker.

        Reuses the same parsing/matching logic from the AllSportsAPI client
        since SportAPI7 uses an identical schema (same field names, same IDs).
        """
        from src.tennis.client import match_event, parse_kalshi_ticker

        parsed = parse_kalshi_ticker(kalshi_ticker)
        if not parsed:
            return None

        events = self.get_live_matches()
        return match_event(parsed, events)
