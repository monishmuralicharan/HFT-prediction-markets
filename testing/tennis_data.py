#!/usr/bin/env python3
"""
Tennis live data viewer — fetches real-time match data from AllSportsAPI.

Usage:
    python3 testing/tennis_data.py                    # list live matches (1 call)
    python3 testing/tennis_data.py --match 12345      # + point-by-point (2 calls)
    python3 testing/tennis_data.py --kalshi TICKER     # auto-match Kalshi ticker (2 calls)
    python3 testing/tennis_data.py --live TICKER       # live dashboard for a Kalshi match
    python3 testing/tennis_data.py --live TICKER -n 30 # poll every 30s (default)
    python3 testing/tennis_data.py --poll 60           # refresh all live matches every 60s
    python3 testing/tennis_data.py --raw               # dump raw JSON (schema discovery)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.tennis.client import TennisClient, parse_kalshi_ticker, match_event

# ── ANSI ─────────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

ENV_PATH = os.path.join(_ROOT, "config", "secrets.env")


def load_env(path):
    env = {}
    with open(path) as f:
        lines = f.read().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("#"):
            i += 1
            continue
        if "=" not in line:
            i += 1
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if value.startswith('"') and not value.endswith('"'):
            parts = [value[1:]]
            i += 1
            while i < len(lines):
                if lines[i].strip().endswith('"'):
                    parts.append(lines[i].strip()[:-1])
                    break
                parts.append(lines[i])
                i += 1
            value = "\n".join(parts)
        elif value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        env[key] = value
        i += 1
    return env


def extract_player_name(event: dict, key: str) -> str:
    """Extract player name from event dict, handling nested team/player structures."""
    player = event.get(key, {})
    if isinstance(player, dict):
        return player.get("name", player.get("shortName", "???"))
    return str(player) if player else "???"


def extract_score(event: dict) -> str:
    """Extract a compact score string from event data."""
    home_score = event.get("homeScore", {})
    away_score = event.get("awayScore", {})

    if isinstance(home_score, dict) and isinstance(away_score, dict):
        # Try period scores (sets)
        parts = []
        for period_key in ["period1", "period2", "period3", "period4", "period5"]:
            h = home_score.get(period_key)
            a = away_score.get(period_key)
            if h is not None and a is not None:
                parts.append(f"{h}-{a}")
        if parts:
            # Current game score
            current_h = home_score.get("point", "")
            current_a = away_score.get("point", "")
            game_score = ""
            if current_h or current_a:
                game_score = f"  ({current_h}-{current_a})"
            return "  ".join(parts) + game_score

        # Fallback to display/current
        h = home_score.get("display", home_score.get("current", ""))
        a = away_score.get("display", away_score.get("current", ""))
        if h or a:
            return f"{h}-{a}"

    return "—"


def extract_tournament(event: dict) -> str:
    """Extract tournament name from event data."""
    tournament = event.get("tournament", {})
    if isinstance(tournament, dict):
        return tournament.get("name", "")
    return ""


def extract_status(event: dict) -> str:
    """Extract match status description."""
    status = event.get("status", {})
    if isinstance(status, dict):
        return status.get("description", status.get("type", ""))
    return ""


def display_matches(events: list[dict]):
    """Print live matches in a formatted table."""
    if not events:
        print(f"\n  {YELLOW}No live tennis matches right now.{RESET}\n")
        return

    print(f"\n  {BOLD}{CYAN}{'━' * 80}")
    print(f"  LIVE TENNIS MATCHES  ({len(events)} found)")
    print(f"  {'━' * 80}{RESET}\n")

    print(
        f"  {DIM}{'ID':>10}  {'Player 1':>22} vs {'Player 2':<22}  "
        f"{'Score':<20}  {'Tournament'}{RESET}"
    )
    print(f"  {DIM}{'─' * 80}{RESET}")

    for event in events:
        event_id = event.get("id", "?")
        p1 = extract_player_name(event, "homeTeam")
        p2 = extract_player_name(event, "awayTeam")
        score = extract_score(event)
        tournament = extract_tournament(event)
        status = extract_status(event)

        # Truncate long names
        p1 = p1[:22]
        p2 = p2[:22]
        tournament = tournament[:30]

        status_color = GREEN if "progress" in status.lower() else YELLOW

        print(
            f"  {BOLD}{event_id:>10}{RESET}  {p1:>22} vs {p2:<22}  "
            f"{status_color}{score:<20}{RESET}  {DIM}{tournament}{RESET}"
        )

    print(f"\n  {DIM}Use --match <ID> to see point-by-point details{RESET}\n")


def display_point_by_point(data: dict, event_id: int):
    """Print point-by-point data for a match."""
    print(f"\n  {BOLD}{CYAN}{'━' * 60}")
    print(f"  POINT-BY-POINT — Match {event_id}")
    print(f"  {'━' * 60}{RESET}\n")

    # Print raw structure keys for schema discovery
    print(f"  {DIM}Top-level keys: {list(data.keys())}{RESET}\n")

    # Try to print points if available
    points = data.get("pointByPoint", data.get("points", []))
    if isinstance(points, list):
        for i, point in enumerate(points[-20:]):  # last 20 points
            print(f"  {DIM}{i:>3}{RESET}  {json.dumps(point, default=str)[:100]}")
    else:
        # Dump the structure for schema discovery
        print(json.dumps(data, indent=2, default=str)[:3000])

    print()


def extract_score_raw(event: dict) -> tuple:
    """Extract score as a comparable tuple for change detection."""
    hs = event.get("homeScore", {})
    aws = event.get("awayScore", {})
    if not isinstance(hs, dict) or not isinstance(aws, dict):
        return ()
    return (
        hs.get("period1"), aws.get("period1"),
        hs.get("period2"), aws.get("period2"),
        hs.get("period3"), aws.get("period3"),
        hs.get("period4"), aws.get("period4"),
        hs.get("period5"), aws.get("period5"),
        hs.get("point"), aws.get("point"),
        hs.get("current"), aws.get("current"),
    )


def extract_serving(event: dict) -> str:
    """Return which player is serving."""
    fts = event.get("firstToServe")
    if fts == 1:
        return extract_player_name(event, "homeTeam").split()[-1]
    elif fts == 2:
        return extract_player_name(event, "awayTeam").split()[-1]
    return "?"


def display_live_dashboard(event: dict, kalshi_ticker: str, client: TennisClient,
                           prev_score: tuple, started_at: float):
    """Print a compact live match dashboard (overwrites screen)."""
    p1 = extract_player_name(event, "homeTeam")
    p2 = extract_player_name(event, "awayTeam")
    score = extract_score(event)
    cur_score = extract_score_raw(event)
    tournament = extract_tournament(event)
    status = extract_status(event)
    serving = extract_serving(event)
    changed = cur_score != prev_score and prev_score != ()

    elapsed = int(time.time() - started_at)
    elapsed_str = f"{elapsed // 60}m{elapsed % 60:02d}s"
    remaining = client.remaining if client.remaining is not None else "?"
    now_str = time.strftime("%H:%M:%S")

    # Clear screen
    print("\033[2J\033[H", end="")

    print(f"  {BOLD}{CYAN}{'━' * 64}")
    print(f"  LIVE  {kalshi_ticker.upper()}")
    print(f"  {'━' * 64}{RESET}")
    print()
    print(f"  {DIM}{tournament}  |  {status}{RESET}")
    print()

    # Player names + set scores
    hs = event.get("homeScore", {})
    aws = event.get("awayScore", {})
    sets_h = []
    sets_a = []
    for pk in ["period1", "period2", "period3", "period4", "period5"]:
        h = hs.get(pk) if isinstance(hs, dict) else None
        a = aws.get(pk) if isinstance(aws, dict) else None
        if h is not None and a is not None:
            sets_h.append(str(h))
            sets_a.append(str(a))

    point_h = str(hs.get("point", "")) if isinstance(hs, dict) else ""
    point_a = str(aws.get("point", "")) if isinstance(aws, dict) else ""

    serve1 = "*" if event.get("firstToServe") == 1 else " "
    serve2 = "*" if event.get("firstToServe") == 2 else " "

    set_cols = "  ".join(f"S{i+1}" for i in range(len(sets_h)))
    print(f"  {DIM}{'':>22}  {set_cols:}  {'Game':>6}{RESET}")
    print(f"  {BOLD}{serve1} {p1:>20}{RESET}  {'  '.join(f'{s:>2}' for s in sets_h)}  {GREEN}{point_h:>6}{RESET}")
    print(f"  {BOLD}{serve2} {p2:>20}{RESET}  {'  '.join(f'{s:>2}' for s in sets_a)}  {RED}{point_a:>6}{RESET}")
    print()

    # Change indicator
    if changed:
        print(f"  {GREEN}{BOLD}  SCORE UPDATED{RESET}")
    else:
        print(f"  {DIM}  ...waiting for update{RESET}")
    print()

    print(f"  {DIM}{'─' * 64}{RESET}")
    print(f"  {DIM}Time: {now_str}  |  Watching: {elapsed_str}  |  "
          f"Calls: {client.call_count} (remaining: {remaining}){RESET}")
    print(f"  {DIM}Ctrl+C to stop{RESET}")

    return cur_score


def run_live_dashboard(kalshi_ticker: str, client: TennisClient, interval: int):
    """Poll a specific match and display a live-updating dashboard."""
    parsed = parse_kalshi_ticker(kalshi_ticker)
    if not parsed:
        print(f"  {RED}Invalid Kalshi ticker: {kalshi_ticker}{RESET}")
        sys.exit(1)

    print(f"  {DIM}Parsed: {parsed['category']}  {parsed['date']}  "
          f"{parsed['code1']} vs {parsed['code2']}{RESET}")
    print(f"  {DIM}Fetching live matches...{RESET}")

    events = client.get_live_matches()
    matched = match_event(parsed, events)
    if not matched:
        print(f"  {YELLOW}No live match found for {kalshi_ticker}{RESET}")
        print(f"  {DIM}The match may not be live yet, or has already finished.{RESET}")
        sys.exit(0)

    event_id = matched["id"]
    p1 = extract_player_name(matched, "homeTeam")
    p2 = extract_player_name(matched, "awayTeam")
    remaining = client.remaining if client.remaining is not None else "?"
    print(f"  {GREEN}Matched!{RESET}  {BOLD}{p1} vs {p2}{RESET}  (ID: {event_id})")
    print(f"  {DIM}Remaining API calls: {remaining}{RESET}")
    print(f"  {CYAN}Polling every {interval}s — Ctrl+C to stop{RESET}\n")
    time.sleep(1)

    prev_score = ()
    started_at = time.time()

    # First display from the data we already have
    prev_score = display_live_dashboard(matched, kalshi_ticker, client, prev_score, started_at)

    while True:
        time.sleep(interval)

        # Check if we're running low
        if client.remaining is not None and client.remaining <= 5:
            print(f"\n  {RED}{BOLD}Stopping — only {client.remaining} API calls remaining!{RESET}")
            break

        try:
            events = client.get_live_matches()
        except Exception as e:
            print(f"\n  {RED}Fetch error: {e}{RESET}")
            continue

        matched = match_event(parsed, events)
        if not matched:
            print(f"\n  {YELLOW}Match no longer live (finished or not found).{RESET}")
            break

        prev_score = display_live_dashboard(matched, kalshi_ticker, client, prev_score, started_at)


def main():
    parser = argparse.ArgumentParser(
        description="Tennis live data viewer (AllSportsAPI)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 testing/tennis_data.py                                      # list live matches
  python3 testing/tennis_data.py --match 12345                        # point-by-point by ID
  python3 testing/tennis_data.py --kalshi KXWTAMATCH-26FEB10NAVKAL    # auto-match Kalshi ticker
  python3 testing/tennis_data.py --live KXWTAMATCH-26FEB10NAVKAL      # live dashboard (30s poll)
  python3 testing/tennis_data.py --live KXWTAMATCH-26FEB10NAVKAL -n 15  # poll every 15s
  python3 testing/tennis_data.py --poll 60                            # refresh all matches every 60s
  python3 testing/tennis_data.py --raw                                # raw JSON output
        """,
    )
    parser.add_argument(
        "--match", type=int, help="Event ID to fetch point-by-point data for"
    )
    parser.add_argument(
        "--kalshi", type=str, metavar="TICKER",
        help="Kalshi ticker (e.g. KXWTAMATCH-26FEB10NAVKAL) — auto-matches to live event",
    )
    parser.add_argument(
        "--live", type=str, metavar="TICKER",
        help="Live dashboard for a Kalshi match (polls for real-time updates)",
    )
    parser.add_argument(
        "-n", "--interval", type=int, default=30, metavar="SEC",
        help="Poll interval for --live mode in seconds (default: 30)",
    )
    parser.add_argument(
        "--poll", type=int, metavar="SECONDS",
        help="Poll interval in seconds for all-matches view (default: no polling)",
    )
    parser.add_argument(
        "--raw", action="store_true", help="Print raw JSON (for schema discovery)"
    )
    args = parser.parse_args()

    # Load API key
    env = load_env(ENV_PATH)
    api_key = env.get("RAPIDAPI_KEY")
    if not api_key:
        print(f"  {RED}Error: RAPIDAPI_KEY not found in {ENV_PATH}{RESET}")
        sys.exit(1)

    client = TennisClient(api_key)

    try:
        if args.live:
            run_live_dashboard(args.live, client, args.interval)

        elif args.kalshi:
            # Parse Kalshi ticker and auto-match to AllSportsAPI event
            parsed = parse_kalshi_ticker(args.kalshi)
            if not parsed:
                print(f"  {RED}Invalid Kalshi ticker: {args.kalshi}{RESET}")
                print(f"  {DIM}Expected format: KXWTAMATCH-26FEB10NAVKAL or KXATPMATCH-26FEB11KORTIA{RESET}")
                sys.exit(1)

            print(f"  {DIM}Parsed: {parsed['category']}  {parsed['date']}  "
                  f"{parsed['code1']} vs {parsed['code2']}{RESET}")
            print(f"  {DIM}Fetching live matches...{RESET}")

            matched = client.find_match_for_kalshi(args.kalshi)
            if not matched:
                print(f"  {YELLOW}No live match found for {args.kalshi}{RESET}")
                print(f"  {DIM}The match may not be live yet, or has already finished.{RESET}")
                sys.exit(0)

            event_id = matched["id"]
            p1 = extract_player_name(matched, "homeTeam")
            p2 = extract_player_name(matched, "awayTeam")
            score = extract_score(matched)
            tournament = extract_tournament(matched)

            print(f"\n  {GREEN}Matched!{RESET}  {BOLD}{p1} vs {p2}{RESET}")
            print(f"  {DIM}Event ID: {event_id}  |  {tournament}  |  Score: {score}{RESET}")

            print(f"\n  {DIM}Fetching point-by-point...{RESET}")
            pbp = client.get_point_by_point(event_id)
            if args.raw:
                print(json.dumps(pbp, indent=2, default=str))
            else:
                display_point_by_point(pbp, event_id)

            print(f"  {DIM}API calls used: {client.call_count}{RESET}")

        elif args.match:
            # Fetch live matches + point-by-point for specific match
            print(f"  {DIM}Fetching live matches...{RESET}")
            events = client.get_live_matches()
            display_matches(events)

            print(f"  {DIM}Fetching point-by-point for match {args.match}...{RESET}")
            pbp = client.get_point_by_point(args.match)
            if args.raw:
                print(json.dumps(pbp, indent=2, default=str))
            else:
                display_point_by_point(pbp, args.match)

            print(f"  {DIM}API calls used: {client.call_count}{RESET}")

        elif args.poll:
            # Polling mode
            print(f"  {CYAN}Polling every {args.poll}s (Ctrl+C to stop){RESET}\n")
            while True:
                events = client.get_live_matches()
                # Clear screen for clean refresh
                print("\033[2J\033[H", end="")
                if args.raw:
                    print(json.dumps(events, indent=2, default=str))
                else:
                    display_matches(events)
                print(f"  {DIM}API calls used: {client.call_count}  |  "
                      f"Next refresh in {args.poll}s{RESET}")
                time.sleep(args.poll)

        else:
            # Single fetch
            print(f"  {DIM}Fetching live matches...{RESET}")
            events = client.get_live_matches()
            if args.raw:
                print(json.dumps(events, indent=2, default=str))
            else:
                display_matches(events)
            print(f"  {DIM}API calls used: {client.call_count}{RESET}")

    except KeyboardInterrupt:
        print(f"\n  {DIM}Stopped. Total API calls: {client.call_count}{RESET}\n")
    except Exception as e:
        print(f"  {RED}Error: {e}{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
