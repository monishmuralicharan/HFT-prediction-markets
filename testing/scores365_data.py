#!/usr/bin/env python3
"""
Tennis live data viewer — fetches real-time match data from 365Scores.

FREE, no API key, no rate limits. Provides point-level game scores,
serving indicator, set scores, and player symbolic names.

Usage:
    python3 testing/scores365_data.py                    # list live matches
    python3 testing/scores365_data.py --match 4669057    # match detail by ID
    python3 testing/scores365_data.py --kalshi TICKER     # auto-match Kalshi ticker
    python3 testing/scores365_data.py --live TICKER       # live dashboard for a Kalshi match
    python3 testing/scores365_data.py --live TICKER -n 5  # poll every 5s (default: 5)
    python3 testing/scores365_data.py --poll 10           # refresh all live matches every 10s
    python3 testing/scores365_data.py --raw               # dump raw JSON (schema discovery)
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

from src.tennis.client import parse_kalshi_ticker
from src.tennis.scores365_client import (
    Scores365Client,
    extract_game_score,
    extract_serving,
    extract_set_scores,
    match_365_event,
)

# ── ANSI ─────────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


# ── Display helpers ──────────────────────────────────────────────────────────


def player_name(game: dict, key: str) -> str:
    comp = game.get(key, {})
    if isinstance(comp, dict):
        return comp.get("name", comp.get("symbolicName", "???"))
    return "???"


def player_code(game: dict, key: str) -> str:
    comp = game.get(key, {})
    return (comp.get("symbolicName") or "?") if isinstance(comp, dict) else "?"


def player_ranking(game: dict, key: str) -> str:
    comp = game.get(key, {})
    if isinstance(comp, dict):
        rankings = comp.get("rankings", [])
        if rankings and isinstance(rankings[0], dict):
            return f"#{rankings[0].get('position', '')}"
    return ""


def format_score(game: dict) -> str:
    """Format compact score: set scores + current game score."""
    sets = extract_set_scores(game)
    gh, ga = extract_game_score(game)

    parts = [f"{h}-{a}" for h, a in sets]
    score = "  ".join(parts) if parts else "—"

    if gh or ga:
        score += f"  ({gh}-{ga})"
    return score


def format_sets_compact(game: dict) -> str:
    """Format set scores as [6-4 3-6 5-3]."""
    sets = extract_set_scores(game)
    if not sets:
        return "[-]"
    return "[" + " ".join(f"{h}-{a}" for h, a in sets) + "]"


def format_game_score(game: dict) -> str:
    """Format current game score like '*40-15' with serve indicator."""
    gh, ga = extract_game_score(game)
    if not gh and not ga:
        return ""
    serve = extract_serving(game)
    prefix = "*" if serve == 1 else " " if serve == 2 else ""
    return f"{prefix}{gh}-{ga}"


def serving_name(game: dict) -> str:
    serve = extract_serving(game)
    if serve == 1:
        return player_name(game, "homeCompetitor").split()[-1]
    elif serve == 2:
        return player_name(game, "awayCompetitor").split()[-1]
    return "?"


def competition_name(game: dict) -> str:
    return game.get("competitionDisplayName", "")


def status_text(game: dict) -> str:
    return game.get("statusText", "")


# ── Display functions ────────────────────────────────────────────────────────


def display_matches(games: list[dict]):
    if not games:
        print(f"\n  {YELLOW}No live tennis matches right now.{RESET}\n")
        return

    print(f"\n  {BOLD}{CYAN}{'━' * 90}")
    print(f"  LIVE TENNIS MATCHES — 365Scores  ({len(games)} found)")
    print(f"  {'━' * 90}{RESET}\n")

    print(
        f"  {DIM}{'ID':>10}  {'Player 1':>22} {'Rk':>4} vs "
        f"{'Rk':<4} {'Player 2':<22}  {'Score':<24}  {'Tournament'}{RESET}"
    )
    print(f"  {DIM}{'─' * 90}{RESET}")

    for game in games:
        gid = game.get("id", "?")
        p1 = player_name(game, "homeCompetitor")[:22]
        p2 = player_name(game, "awayCompetitor")[:22]
        r1 = player_ranking(game, "homeCompetitor")
        r2 = player_ranking(game, "awayCompetitor")
        score = format_score(game)
        tournament = competition_name(game)[:30]
        status = status_text(game)

        serve = extract_serving(game)
        serve_marker_1 = "●" if serve == 1 else " "
        serve_marker_2 = "●" if serve == 2 else " "

        status_color = GREEN if game.get("statusGroup") == 3 else YELLOW

        print(
            f"  {BOLD}{gid:>10}{RESET}  {p1:>22} {DIM}{r1:>4}{RESET}"
            f" {GREEN}{serve_marker_1}{RESET} vs "
            f"{GREEN}{serve_marker_2}{RESET} {DIM}{r2:<4}{RESET} {p2:<22}  "
            f"{status_color}{score:<24}{RESET}  {DIM}{tournament}{RESET}"
        )

    print(f"\n  {DIM}Use --match <ID> for detail  |  ● = serving{RESET}\n")


def display_match_detail(game: dict):
    gid = game.get("id", "?")
    p1 = player_name(game, "homeCompetitor")
    p2 = player_name(game, "awayCompetitor")

    print(f"\n  {BOLD}{CYAN}{'━' * 60}")
    print(f"  MATCH DETAIL — {p1} vs {p2}  (365Scores)")
    print(f"  {'━' * 60}{RESET}\n")

    print(f"  {DIM}ID:{RESET}         {gid}")
    print(f"  {DIM}Tournament:{RESET} {competition_name(game)}")
    print(f"  {DIM}Round:{RESET}      {game.get('stageName', '?')}")
    print(f"  {DIM}Status:{RESET}     {status_text(game)}")
    print(f"  {DIM}Venue:{RESET}      {game.get('venue', {}).get('name', '?')}")

    r1 = player_ranking(game, "homeCompetitor")
    r2 = player_ranking(game, "awayCompetitor")
    print(f"\n  {BOLD}{p1}{RESET} {DIM}{r1}{RESET}  vs  {BOLD}{p2}{RESET} {DIM}{r2}{RESET}")

    # Set scores
    print(f"\n  {DIM}{'Set':<6} {'Home':>6} {'Away':>6}  {'Status'}{RESET}")
    print(f"  {DIM}{'─' * 30}{RESET}")
    for stage in game.get("stages", []):
        sn = stage.get("shortName", "")
        if sn in ("Game", "Sets"):
            continue
        h = stage.get("homeCompetitorScore", -1)
        a = stage.get("awayCompetitorScore", -1)
        if h < 0:
            continue
        ended = "done" if stage.get("isEnded") else "live" if stage.get("isLive") else ""
        ended_str = f"  {GREEN}{ended}{RESET}" if ended == "live" else f"  {DIM}{ended}{RESET}"
        print(f"  {sn:<6} {int(h):>6} {int(a):>6}{ended_str}")

    # Game score
    gh, ga = extract_game_score(game)
    if gh or ga:
        print(f"\n  {DIM}Game score:{RESET} {GREEN}{BOLD}{gh}-{ga}{RESET}")

    # Serving
    print(f"  {DIM}Serving:{RESET}    {serving_name(game)}")
    print()


# ── Score tracking for polling ───────────────────────────────────────────────


def extract_state(game: dict) -> tuple:
    """Extract match state as a comparable tuple for change detection."""
    sets = extract_set_scores(game)
    gh, ga = extract_game_score(game)
    serve = extract_serving(game)
    return (tuple(sets), gh, ga, serve)


def log_poll(game: dict, client: Scores365Client, prev_state: tuple,
             poll_num: int) -> tuple:
    """Print a single log line for a poll result. Returns new state tuple."""
    cur_state = extract_state(game)
    changed = cur_state != prev_state and prev_state != ()

    now_str = time.strftime("%H:%M:%S")
    p1 = player_name(game, "homeCompetitor")
    p2 = player_name(game, "awayCompetitor")
    status = status_text(game)
    sets = format_sets_compact(game)
    game_score = format_game_score(game)
    serve = serving_name(game)

    if changed:
        tag = f"{GREEN}{BOLD}UPDATE{RESET}"
    else:
        tag = f"{DIM}poll{RESET}  "

    print(
        f"  {DIM}{now_str}{RESET}  {tag}  "
        f"{BOLD}{p1}{RESET} vs {BOLD}{p2}{RESET}  "
        f"{CYAN}{sets}{RESET}  {GREEN}{game_score:>7}{RESET}  "
        f"{DIM}{status}  serving={serve}  "
        f"#{poll_num}  calls={client.call_count}{RESET}"
    )

    if changed and prev_state != ():
        # Show what changed
        prev_sets, prev_gh, prev_ga, prev_serve_code = prev_state
        cur_sets, cur_gh, cur_ga, cur_serve_code = cur_state
        diffs = []
        if prev_sets != cur_sets:
            diffs.append(f"sets: {list(prev_sets)}->{list(cur_sets)}")
        if prev_gh != cur_gh or prev_ga != cur_ga:
            diffs.append(f"game: {prev_gh}-{prev_ga}->{cur_gh}-{cur_ga}")
        if prev_serve_code != cur_serve_code:
            diffs.append("serve changed")
        if diffs:
            print(f"           {DIM}^ {', '.join(diffs)}{RESET}")

    return cur_state


def run_live_poll(kalshi_ticker: str, client: Scores365Client, interval: int):
    """Poll a specific match and print sequential log lines."""
    parsed = parse_kalshi_ticker(kalshi_ticker)
    if not parsed:
        print(f"  {RED}Invalid Kalshi ticker: {kalshi_ticker}{RESET}")
        sys.exit(1)

    print(f"  {DIM}Parsed: {parsed['category']}  {parsed['date']}  "
          f"{parsed['code1']} vs {parsed['code2']}{RESET}")
    print(f"  {DIM}Fetching live matches from 365Scores...{RESET}")

    games = client.get_live_matches()
    matched = match_365_event(parsed, games)
    if not matched:
        print(f"  {YELLOW}No live match found for {kalshi_ticker}{RESET}")
        print(f"  {DIM}The match may not be live yet, or has already finished.{RESET}")
        sys.exit(0)

    gid = matched["id"]
    p1 = player_name(matched, "homeCompetitor")
    p2 = player_name(matched, "awayCompetitor")
    tournament = competition_name(matched)

    print(f"  {GREEN}Matched!{RESET}  {BOLD}{p1} vs {p2}{RESET}  "
          f"{DIM}({tournament}, ID: {gid}){RESET}")
    print(f"  {DIM}Polling every {interval}s  |  FREE — no rate limits{RESET}")
    print(f"  {DIM}Ctrl+C to stop{RESET}")
    print()

    prev_state = ()
    poll_num = 1
    effective_interval = max(interval, 1)

    # First log from the data we already have
    prev_state = log_poll(matched, client, prev_state, poll_num)

    while True:
        time.sleep(effective_interval)
        poll_num += 1

        try:
            games = client.get_live_matches()
        except Exception as e:
            print(f"  {DIM}{time.strftime('%H:%M:%S')}{RESET}  {RED}error: {e}{RESET}")
            continue

        matched = match_365_event(parsed, games)
        if not matched:
            print(f"  {DIM}{time.strftime('%H:%M:%S')}{RESET}  "
                  f"{YELLOW}Match ended or no longer live.{RESET}")
            break

        prev_state = log_poll(matched, client, prev_state, poll_num)


def main():
    parser = argparse.ArgumentParser(
        description="Tennis live data viewer (365Scores — FREE, no API key)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 testing/scores365_data.py                                      # list live matches
  python3 testing/scores365_data.py --match 4669057                      # match detail by ID
  python3 testing/scores365_data.py --kalshi KXATPMATCH-26FEB11FRIIGIR   # auto-match Kalshi ticker
  python3 testing/scores365_data.py --live KXATPMATCH-26FEB11FRIGIR      # live dashboard (5s poll)
  python3 testing/scores365_data.py --live KXATPMATCH-26FEB11FRIGIR -n 3 # poll every 3s
  python3 testing/scores365_data.py --poll 10                            # refresh all matches every 10s
  python3 testing/scores365_data.py --raw                                # raw JSON output
        """,
    )
    parser.add_argument(
        "--match", type=int, help="Game ID to fetch details for"
    )
    parser.add_argument(
        "--kalshi", type=str, metavar="TICKER",
        help="Kalshi ticker (e.g. KXATPMATCH-26FEB11FRIGIR) — auto-matches to live event",
    )
    parser.add_argument(
        "--live", type=str, metavar="TICKER",
        help="Live dashboard for a Kalshi match (polls for real-time updates)",
    )
    parser.add_argument(
        "-n", "--interval", type=int, default=5, metavar="SEC",
        help="Poll interval for --live mode in seconds (default: 5)",
    )
    parser.add_argument(
        "--poll", type=int, metavar="SECONDS",
        help="Poll interval in seconds for all-matches view (default: no polling)",
    )
    parser.add_argument(
        "--raw", action="store_true", help="Print raw JSON (for schema discovery)"
    )
    args = parser.parse_args()

    client = Scores365Client()

    try:
        if args.live:
            run_live_poll(args.live, client, args.interval)

        elif args.kalshi:
            parsed = parse_kalshi_ticker(args.kalshi)
            if not parsed:
                print(f"  {RED}Invalid Kalshi ticker: {args.kalshi}{RESET}")
                print(f"  {DIM}Expected format: KXWTAMATCH-26FEB10NAVKAL or KXATPMATCH-26FEB11FRIGIR{RESET}")
                sys.exit(1)

            print(f"  {DIM}Parsed: {parsed['category']}  {parsed['date']}  "
                  f"{parsed['code1']} vs {parsed['code2']}{RESET}")
            print(f"  {DIM}Fetching live matches from 365Scores...{RESET}")

            matched = client.find_match_for_kalshi(args.kalshi)
            if not matched:
                print(f"  {YELLOW}No live match found for {args.kalshi}{RESET}")
                print(f"  {DIM}The match may not be live yet, or has already finished.{RESET}")
                sys.exit(0)

            gid = matched["id"]
            p1 = player_name(matched, "homeCompetitor")
            p2 = player_name(matched, "awayCompetitor")
            score = format_score(matched)
            tournament = competition_name(matched)

            print(f"\n  {GREEN}Matched!{RESET}  {BOLD}{p1} vs {p2}{RESET}")
            print(f"  {DIM}Game ID: {gid}  |  {tournament}  |  Score: {score}{RESET}")

            if args.raw:
                detail = client.get_match_details(gid)
                print(json.dumps(detail, indent=2, default=str))
            else:
                display_match_detail(matched)

            print(f"  {DIM}API calls used: {client.call_count}  |  Cost: $0{RESET}")

        elif args.match:
            print(f"  {DIM}Fetching match {args.match} from 365Scores...{RESET}")
            detail = client.get_match_details(args.match)
            if args.raw:
                print(json.dumps(detail, indent=2, default=str))
            else:
                display_match_detail(detail)
            print(f"  {DIM}API calls used: {client.call_count}  |  Cost: $0{RESET}")

        elif args.poll:
            print(f"  {CYAN}Polling every {args.poll}s (Ctrl+C to stop){RESET}\n")
            while True:
                games = client.get_live_matches()
                print("\033[2J\033[H", end="")
                if args.raw:
                    print(json.dumps(games, indent=2, default=str))
                else:
                    display_matches(games)
                print(f"  {DIM}API calls: {client.call_count}  |  "
                      f"Next refresh in {args.poll}s  |  Cost: $0{RESET}")
                time.sleep(args.poll)

        else:
            print(f"  {DIM}Fetching live matches from 365Scores...{RESET}")
            games = client.get_live_matches()
            if args.raw:
                print(json.dumps(games, indent=2, default=str))
            else:
                display_matches(games)
            print(f"  {DIM}API calls: {client.call_count}  |  Cost: $0{RESET}")

    except KeyboardInterrupt:
        print(f"\n  {DIM}Stopped. Total API calls: {client.call_count}  |  Cost: $0{RESET}\n")
    except Exception as e:
        print(f"  {RED}Error: {e}{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()
