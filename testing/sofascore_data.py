#!/usr/bin/env python3
"""
Tennis live data viewer — fetches real-time match data from SofaScore6 API.

Drop-in alternative to tennis_data.py using SofaScore6 instead of AllSportsAPI.
Same CLI interface, same display logic, different data source.

Key differences from AllSportsAPI:
  - No point-by-point / incidents endpoint
  - Has match statistics (aces, serve %, break points, etc.)
  - Has odds endpoint
  - Response is a flat list (not wrapped in {"events": [...]})
  - Uses 'timestamp' instead of 'startTimestamp'
  - No 'firstToServe' field (serving indicator unavailable)

Usage:
    python3 testing/sofascore_data.py                    # list live matches (1 call)
    python3 testing/sofascore_data.py --match 12345      # + statistics (2 calls)
    python3 testing/sofascore_data.py --kalshi TICKER     # auto-match Kalshi ticker (2 calls)
    python3 testing/sofascore_data.py --live TICKER       # live dashboard for a Kalshi match
    python3 testing/sofascore_data.py --live TICKER -n 30 # poll every 30s (default)
    python3 testing/sofascore_data.py --poll 60           # refresh all live matches every 60s
    python3 testing/sofascore_data.py --raw               # dump raw JSON (schema discovery)
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

from src.tennis.client import parse_kalshi_ticker, match_event
from src.tennis.sofascore_client import SofaScoreClient

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


def extract_ranking(event: dict, key: str) -> str:
    """Extract player ATP/WTA ranking."""
    player = event.get(key, {})
    if isinstance(player, dict):
        rank = player.get("ranking")
        return f"#{rank}" if rank else ""
    return ""


def display_matches(events: list[dict]):
    """Print live matches in a formatted table."""
    if not events:
        print(f"\n  {YELLOW}No live tennis matches right now.{RESET}\n")
        return

    print(f"\n  {BOLD}{CYAN}{'━' * 90}")
    print(f"  LIVE TENNIS MATCHES — SofaScore6  ({len(events)} found)")
    print(f"  {'━' * 90}{RESET}\n")

    print(
        f"  {DIM}{'ID':>10}  {'Player 1':>22} {'Rk':>4} vs "
        f"{'Rk':<4} {'Player 2':<22}  {'Score':<20}  {'Tournament'}{RESET}"
    )
    print(f"  {DIM}{'─' * 90}{RESET}")

    for event in events:
        event_id = event.get("id", "?")
        p1 = extract_player_name(event, "homeTeam")
        p2 = extract_player_name(event, "awayTeam")
        r1 = extract_ranking(event, "homeTeam")
        r2 = extract_ranking(event, "awayTeam")
        score = extract_score(event)
        tournament = extract_tournament(event)
        status = extract_status(event)

        # Truncate long names
        p1 = p1[:22]
        p2 = p2[:22]
        tournament = tournament[:30]

        status_color = GREEN if "progress" in status.lower() else YELLOW

        print(
            f"  {BOLD}{event_id:>10}{RESET}  {p1:>22} {DIM}{r1:>4}{RESET} vs "
            f"{DIM}{r2:<4}{RESET} {p2:<22}  "
            f"{status_color}{score:<20}{RESET}  {DIM}{tournament}{RESET}"
        )

    print(f"\n  {DIM}Use --match <ID> to see match statistics{RESET}\n")


def display_statistics(stats: list[dict], event_id: int):
    """Print match statistics."""
    print(f"\n  {BOLD}{CYAN}{'━' * 60}")
    print(f"  MATCH STATISTICS — Match {event_id}  (SofaScore6)")
    print(f"  {'━' * 60}{RESET}\n")

    if not stats:
        print(f"  {YELLOW}No statistics available yet.{RESET}\n")
        return

    for period_stats in stats:
        period = period_stats.get("period", "?")
        print(f"  {BOLD}Period: {period}{RESET}")
        print(f"  {DIM}{'─' * 50}{RESET}")

        for group in period_stats.get("groups", []):
            group_name = group.get("groupName", "")
            print(f"\n  {CYAN}{group_name}{RESET}")

            for item in group.get("statisticsItems", []):
                name = item.get("name", "?")
                home = item.get("home", "")
                away = item.get("away", "")
                print(f"    {home:>20}  {DIM}{name:^20}{RESET}  {away:<20}")

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


def format_sets_compact(event: dict) -> str:
    """Format set scores as a compact string like [6-4 3-6 5-3]."""
    hs = event.get("homeScore", {})
    aws = event.get("awayScore", {})
    if not isinstance(hs, dict) or not isinstance(aws, dict):
        return "[-]"
    parts = []
    for pk in ["period1", "period2", "period3", "period4", "period5"]:
        h = hs.get(pk)
        a = aws.get(pk)
        if h is not None and a is not None:
            parts.append(f"{h}-{a}")
    return "[" + " ".join(parts) + "]" if parts else "[-]"


def format_game_score(event: dict) -> str:
    """Format current game score like '40-15'."""
    hs = event.get("homeScore", {})
    aws = event.get("awayScore", {})
    pt_h = hs.get("point", "") if isinstance(hs, dict) else ""
    pt_a = aws.get("point", "") if isinstance(aws, dict) else ""
    if not pt_h and not pt_a:
        return ""
    return f"{pt_h}-{pt_a}"


def log_poll(event: dict, client: SofaScoreClient, prev_score: tuple,
             poll_num: int) -> tuple:
    """Print a single log line for a poll result. Returns new_score_tuple."""
    cur_score = extract_score_raw(event)
    changed = cur_score != prev_score and prev_score != ()

    now_str = time.strftime("%H:%M:%S")
    remaining = client.remaining if client.remaining is not None else "?"

    p1 = extract_player_name(event, "homeTeam")
    p2 = extract_player_name(event, "awayTeam")
    status = extract_status(event)
    sets = format_sets_compact(event)
    game = format_game_score(event)

    if changed:
        tag = f"{GREEN}{BOLD}UPDATE{RESET}"
    else:
        tag = f"{DIM}poll{RESET}  "

    print(
        f"  {DIM}{now_str}{RESET}  {tag}  "
        f"{BOLD}{p1}{RESET} vs {BOLD}{p2}{RESET}  "
        f"{CYAN}{sets}{RESET}  {GREEN}{game:>7}{RESET}  "
        f"{DIM}{status}  "
        f"#{poll_num} rem={remaining}{RESET}"
    )

    return cur_score


def run_live_poll(kalshi_ticker: str, client: SofaScoreClient, interval: int):
    """Poll a specific match and print sequential log lines."""
    parsed = parse_kalshi_ticker(kalshi_ticker)
    if not parsed:
        print(f"  {RED}Invalid Kalshi ticker: {kalshi_ticker}{RESET}")
        sys.exit(1)

    print(f"  {DIM}Parsed: {parsed['category']}  {parsed['date']}  "
          f"{parsed['code1']} vs {parsed['code2']}{RESET}")
    print(f"  {DIM}Fetching live matches from SofaScore6...{RESET}")

    events = client.get_live_matches()
    matched = match_event(parsed, events)
    if not matched:
        print(f"  {YELLOW}No live match found for {kalshi_ticker}{RESET}")
        print(f"  {DIM}The match may not be live yet, or has already finished.{RESET}")
        sys.exit(0)

    event_id = matched["id"]
    p1 = extract_player_name(matched, "homeTeam")
    p2 = extract_player_name(matched, "awayTeam")
    tournament = extract_tournament(matched)
    remaining = client.remaining if client.remaining is not None else "?"

    print(f"  {GREEN}Matched!{RESET}  {BOLD}{p1} vs {p2}{RESET}  "
          f"{DIM}({tournament}, ID: {event_id}){RESET}")
    print(f"  {DIM}Polling every {interval}s  |  API calls remaining: {remaining}{RESET}")
    print(f"  {DIM}Ctrl+C to stop{RESET}")
    print()

    prev_score = ()
    poll_num = 1
    effective_interval = max(interval, 1)

    # First log from the data we already have
    prev_score = log_poll(matched, client, prev_score, poll_num)

    while True:
        time.sleep(effective_interval)
        poll_num += 1

        if client.remaining is not None and client.remaining <= 5:
            print(f"  {RED}{BOLD}Stopping — only {client.remaining} API calls remaining!{RESET}")
            break

        try:
            events = client.get_live_matches()
        except Exception as e:
            print(f"  {DIM}{time.strftime('%H:%M:%S')}{RESET}  {RED}error: {e}{RESET}")
            continue

        matched = match_event(parsed, events)
        if not matched:
            print(f"  {DIM}{time.strftime('%H:%M:%S')}{RESET}  "
                  f"{YELLOW}Match ended or no longer live.{RESET}")
            break

        prev_score = log_poll(matched, client, prev_score, poll_num)


def main():
    parser = argparse.ArgumentParser(
        description="Tennis live data viewer (SofaScore6 API)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 testing/sofascore_data.py                                      # list live matches
  python3 testing/sofascore_data.py --match 12345                        # statistics by ID
  python3 testing/sofascore_data.py --kalshi KXWTAMATCH-26FEB10NAVKAL    # auto-match Kalshi ticker
  python3 testing/sofascore_data.py --live KXWTAMATCH-26FEB10NAVKAL      # live dashboard (30s poll)
  python3 testing/sofascore_data.py --live KXWTAMATCH-26FEB10NAVKAL -n 15  # poll every 15s
  python3 testing/sofascore_data.py --poll 60                            # refresh all matches every 60s
  python3 testing/sofascore_data.py --raw                                # raw JSON output
        """,
    )
    parser.add_argument(
        "--match", type=int, help="Event ID to fetch statistics for"
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

    client = SofaScoreClient(api_key)

    try:
        if args.live:
            run_live_poll(args.live, client, args.interval)

        elif args.kalshi:
            parsed = parse_kalshi_ticker(args.kalshi)
            if not parsed:
                print(f"  {RED}Invalid Kalshi ticker: {args.kalshi}{RESET}")
                print(f"  {DIM}Expected format: KXWTAMATCH-26FEB10NAVKAL or KXATPMATCH-26FEB11KORTIA{RESET}")
                sys.exit(1)

            print(f"  {DIM}Parsed: {parsed['category']}  {parsed['date']}  "
                  f"{parsed['code1']} vs {parsed['code2']}{RESET}")
            print(f"  {DIM}Fetching live matches from SofaScore6...{RESET}")

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

            print(f"\n  {DIM}Fetching statistics...{RESET}")
            stats = client.get_statistics(event_id)
            if args.raw:
                print(json.dumps(stats, indent=2, default=str))
            else:
                display_statistics(stats, event_id)

            print(f"  {DIM}API calls used: {client.call_count}{RESET}")

        elif args.match:
            print(f"  {DIM}Fetching live matches from SofaScore6...{RESET}")
            events = client.get_live_matches()
            display_matches(events)

            print(f"  {DIM}Fetching statistics for match {args.match}...{RESET}")
            stats = client.get_statistics(args.match)
            if args.raw:
                print(json.dumps(stats, indent=2, default=str))
            else:
                display_statistics(stats, args.match)

            print(f"  {DIM}API calls used: {client.call_count}{RESET}")

        elif args.poll:
            print(f"  {CYAN}Polling every {args.poll}s (Ctrl+C to stop){RESET}\n")
            while True:
                events = client.get_live_matches()
                print("\033[2J\033[H", end="")
                if args.raw:
                    print(json.dumps(events, indent=2, default=str))
                else:
                    display_matches(events)
                print(f"  {DIM}API calls used: {client.call_count}  |  "
                      f"Next refresh in {args.poll}s{RESET}")
                time.sleep(args.poll)

        else:
            print(f"  {DIM}Fetching live matches from SofaScore6...{RESET}")
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
