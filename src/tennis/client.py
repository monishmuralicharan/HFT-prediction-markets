"""
Tennis live data client for AllSportsAPI (via RapidAPI).

Simple synchronous client using urllib + certifi. Tracks API call count
to stay within the 100 calls/day free tier limit.
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.request
from datetime import datetime, timezone

import certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

DAILY_LIMIT = 100
WARN_THRESHOLD = 80


class TennisClient:
    BASE_URL = "https://allsportsapi2.p.rapidapi.com"

    def __init__(self, api_key: str):
        self.headers = {
            "x-rapidapi-key": api_key,
            "x-rapidapi-host": "allsportsapi2.p.rapidapi.com",
        }
        self.call_count = 0
        self.remaining: int | None = None  # from API rate-limit headers

    def _get(self, path: str) -> dict:
        """Make a GET request, increment call counter, return parsed JSON."""
        if self.remaining is not None and self.remaining <= 0:
            raise RuntimeError(
                "Daily API limit reached (0 remaining). "
                "Wait until tomorrow or upgrade your plan."
            )

        url = f"{self.BASE_URL}{path}"
        req = urllib.request.Request(url, method="GET")
        for k, v in self.headers.items():
            req.add_header(k, v)

        with urllib.request.urlopen(req, timeout=15, context=_SSL_CTX) as resp:
            data = json.loads(resp.read())
            # Read actual remaining calls from RapidAPI headers
            remaining_hdr = resp.headers.get("X-RateLimit-Requests-Remaining")
            if remaining_hdr is not None:
                self.remaining = int(remaining_hdr)

        self.call_count += 1

        return data

    def get_live_matches(self) -> list[dict]:
        """Fetch all currently live tennis matches."""
        data = self._get("/api/tennis/events/live")
        return data.get("events", [])

    def get_match_details(self, event_id: int) -> dict:
        """Fetch details for a specific match."""
        data = self._get(f"/api/tennis/event/{event_id}")
        return data.get("event", data)

    def get_point_by_point(self, event_id: int) -> dict:
        """Fetch point-by-point live data for a match."""
        return self._get(f"/api/tennis/event/{event_id}/point-by-point")

    def find_match_for_kalshi(self, kalshi_ticker: str) -> dict | None:
        """Find the AllSportsAPI event matching a Kalshi tennis ticker.

        Parses the ticker to extract category, date, and player codes,
        then matches against live events. Returns the matched event dict
        or None.
        """
        parsed = parse_kalshi_ticker(kalshi_ticker)
        if not parsed:
            return None

        events = self.get_live_matches()
        return match_event(parsed, events)


# ── Kalshi ticker parsing / matching ─────────────────────────────────────────

# Matches: KX{CATEGORY}MATCH-{YY}{MON}{DD}{CODE1}{CODE2}
# Category can be: WTA, ATP, ATPCHALLENGER, WTACHALLENGER, etc.
# Optional trailing market suffix: -{CODE}
_TICKER_RE = re.compile(
    r"^KX([A-Z]+)MATCH-(\d{2})(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)"
    r"(\d{2})([A-Z]{3})([A-Z]{3})(?:-[A-Z]{2,4})?$",
    re.IGNORECASE,
)

_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}


def parse_kalshi_ticker(ticker: str) -> dict | None:
    """Parse a Kalshi tennis ticker into its components.

    Returns dict with keys: category, date, code1, code2
    or None if the ticker doesn't match the expected format.

    Examples:
        KXWTAMATCH-26FEB10NAVKAL            → {category: WTA, ...}
        KXWTAMATCH-26FEB10NAVKAL-NAV        → same (trailing suffix stripped)
        kxatpmatch-26feb11kortia-tia        → {category: ATP, ...}
        kxatpchallengermatch-26feb10milsmi  → {category: ATPCHALLENGER, ...}
    """
    ticker = ticker.strip().upper()
    m = _TICKER_RE.match(ticker)
    if not m:
        return None

    category = m.group(1)  # WTA, ATP, ATPCHALLENGER, etc.
    year = 2000 + int(m.group(2))
    month = _MONTHS[m.group(3)]
    day = int(m.group(4))
    code1 = m.group(5)
    code2 = m.group(6)

    return {
        "category": category,
        "date": datetime(year, month, day, tzinfo=timezone.utc).date(),
        "code1": code1,
        "code2": code2,
    }


def match_event(parsed: dict, events: list[dict]) -> dict | None:
    """Find the AllSportsAPI event matching parsed Kalshi ticker data.

    Primary match: both player nameCode values match (order-independent).
    Tiebreak: category (WTA/ATP) and date from startTimestamp.
    """
    code_pair = {parsed["code1"], parsed["code2"]}
    # Normalize: ATPCHALLENGER → ATP, WTACHALLENGER → WTA, etc.
    raw_category = parsed["category"]  # e.g. "ATPCHALLENGER", "WTA"
    target_base = "WTA" if raw_category.startswith("WTA") else "ATP"
    target_date = parsed["date"]

    candidates = []

    for event in events:
        home = event.get("homeTeam", {})
        away = event.get("awayTeam", {})
        if not isinstance(home, dict) or not isinstance(away, dict):
            continue

        home_code = (home.get("nameCode") or "").upper()
        away_code = (away.get("nameCode") or "").upper()

        if {home_code, away_code} != code_pair:
            continue

        # Code match found — score by category + date agreement
        score = 0

        # Check category (AllSportsAPI uses "ATP" / "WTA" for all tiers)
        cat_name = ""
        tournament = event.get("tournament", {})
        if isinstance(tournament, dict):
            cat = tournament.get("category", {})
            if isinstance(cat, dict):
                cat_name = (cat.get("name") or "").upper()
        if cat_name == target_base:
            score += 2

        # Check date (AllSportsAPI uses startTimestamp, SofaScore uses timestamp)
        ts = event.get("startTimestamp") or event.get("timestamp")
        if ts:
            event_date = datetime.fromtimestamp(ts, tz=timezone.utc).date()
            if event_date == target_date:
                score += 1

        candidates.append((score, event))

    if not candidates:
        return None

    # Return best-scoring match
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]
