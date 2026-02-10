#!/usr/bin/env python3
"""
Stream live Kalshi market data to the terminal.

Usage:
    python3 testing/stream.py KXNCAAMBGAME-26JAN29SFPACHS-SFPA
    python3 testing/stream.py kxncaambgame-26jan29sfpachs
    python3 testing/stream.py https://kalshi.com/markets/kxncaambgame/.../kxncaambgame-26jan29sfpachs
"""

import asyncio
import base64
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import requests
import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ── Paths / URLs ────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, "config", "secrets.env")
REST_URL = "https://api.elections.kalshi.com/trade-api/v2"
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

# ── ANSI ────────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"
CLEAR_SCREEN = "\033[2J\033[H"
CLEAR_LINE = "\033[2K"


# ── Env / Auth ──────────────────────────────────────────────────────────────
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


ENV = load_env(ENV_PATH)
API_KEY_ID = ENV["KALSHI_API_KEY_ID"]
PRIVATE_KEY = serialization.load_pem_private_key(
    ENV["KALSHI_PRIVATE_KEY"].encode(), password=None
)


def sign(method, path):
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method}{path}"
    sig = PRIVATE_KEY.sign(
        msg.encode(),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.MAX_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": API_KEY_ID,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        "KALSHI-ACCESS-TIMESTAMP": ts,
    }


def rest_get(path, params=None):
    url = f"{REST_URL}{path}"
    headers = sign("GET", f"/trade-api/v2{path}")
    r = requests.get(url, headers=headers, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


# ── Orderbook state ─────────────────────────────────────────────────────────
class OrderbookState:
    """Maintains a local orderbook from snapshots + deltas."""

    def __init__(self):
        # {ticker: {"yes": {price: size, ...}, "no": {price: size, ...}}}
        self.books = defaultdict(lambda: {"yes": {}, "no": {}})
        self.last_update = {}

    def apply_snapshot(self, ticker, data):
        book = {"yes": {}, "no": {}}
        for price, size in (data.get("yes") or []):
            book["yes"][price] = size
        for price, size in (data.get("no") or []):
            book["no"][price] = size
        self.books[ticker] = book
        self.last_update[ticker] = time.time()

    def apply_delta(self, ticker, data):
        """Apply a single-level delta: {price, delta, side}."""
        book = self.books[ticker]
        side = data.get("side")
        price = data.get("price")
        delta = data.get("delta", 0)

        if side and price is not None:
            current = book[side].get(price, 0)
            new_size = current + delta
            if new_size <= 0:
                book[side].pop(price, None)
            else:
                book[side][price] = new_size
            self.last_update[ticker] = time.time()

    def get_top(self, ticker, depth=5):
        """Return top N yes bid levels and top N no levels (for implied asks)."""
        book = self.books[ticker]
        yes_bids = sorted(
            [(p, s) for p, s in book["yes"].items() if s > 0],
            key=lambda x: -x[0],
        )[:depth]
        no_levels = sorted(
            [(p, s) for p, s in book["no"].items() if s > 0],
            key=lambda x: -x[0],
        )[:depth]
        return yes_bids, no_levels


# ── Market state ────────────────────────────────────────────────────────────
class MarketState:
    def __init__(self):
        self.info = {}        # {ticker: {title, yes_bid, ...}}
        self.tickers = {}     # {ticker: {yes_bid, yes_ask, volume, ...}}
        self.orderbook = OrderbookState()
        self.trades = []      # recent trades (max 30)
        self.trade_count = 0
        self.msg_count = 0
        self.connected_at = None

    def short(self, ticker):
        return ticker.split("-")[-1] if ticker else "???"


# ── Rendering ───────────────────────────────────────────────────────────────
def render(state):
    """Redraw the full terminal display."""
    lines = []
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")

    lines.append(f"{BOLD}{CYAN}{'━' * 72}")
    lines.append(f"  KALSHI LIVE STREAM   {DIM}{now} UTC   {state.msg_count} msgs   {state.trade_count} trades{RESET}")
    lines.append(f"{CYAN}{'━' * 72}{RESET}")

    # Orderbook for each ticker
    for ticker in sorted(state.info.keys()):
        info = state.info[ticker]
        short = state.short(ticker)
        tick = state.tickers.get(ticker, {})

        title = info.get("title", info.get("subtitle", ticker))
        vol = tick.get("volume", info.get("volume", 0))
        oi = tick.get("open_interest", info.get("open_interest", ""))
        last_price = tick.get("price", info.get("last_price"))

        # Use authoritative bid/ask from ticker channel (updates every ~1s)
        yes_bid = tick.get("yes_bid", info.get("yes_bid"))
        yes_ask = tick.get("yes_ask", info.get("yes_ask"))
        bid_str = f"{yes_bid}c" if yes_bid else " - "
        ask_str = f"{yes_ask}c" if yes_ask else " - "
        spread = (yes_ask or 0) - (yes_bid or 0)
        last_str = f"{last_price}c" if last_price else "-"

        lines.append("")
        lines.append(f"  {BOLD}{WHITE}{short}{RESET}  {DIM}{title}{RESET}")
        lines.append(
            f"  Bid {GREEN}{BOLD}{bid_str}{RESET}  "
            f"Ask {RED}{BOLD}{ask_str}{RESET}  "
            f"Last {WHITE}{BOLD}{last_str}{RESET}  "
            f"Spread {YELLOW}{spread}c{RESET}  "
            f"{DIM}vol={vol:,}  oi={oi}{RESET}"
        )

        # Top of book from local orderbook state
        yes_bids, no_levels = state.orderbook.get_top(ticker, depth=5)

        # Convert no levels to implied yes ask prices for display
        implied_asks = sorted(
            [(100 - p, s) for p, s in no_levels if s > 0],
            key=lambda x: x[0],
        )[:5]

        lines.append(f"  {DIM}{'─' * 34}  {'─' * 34}{RESET}")
        lines.append(
            f"  {GREEN}{'BID (YES)':^34}{RESET}"
            f"  {RED}{'ASK (implied)':^34}{RESET}"
        )

        max_rows = max(len(yes_bids), len(implied_asks), 1)
        for i in range(min(max_rows, 5)):
            # Bid side
            if i < len(yes_bids):
                bp, bs = yes_bids[i]
                bid_cell = f"  {GREEN}{bs:>8,} @ {bp:>2}c (${bp/100:.2f}){RESET}"
            else:
                bid_cell = f"  {'':>30}"

            # Ask side
            if i < len(implied_asks):
                ap, as_ = implied_asks[i]
                ask_cell = f"  {RED}{as_:>8,} @ {ap:>2}c (${ap/100:.2f}){RESET}"
            else:
                ask_cell = ""

            lines.append(f"{bid_cell:42s}{ask_cell}")

    # Recent trades
    lines.append(f"\n{CYAN}{'─' * 72}{RESET}")
    lines.append(f"  {BOLD}RECENT TRADES{RESET}")
    lines.append(f"{CYAN}{'─' * 72}{RESET}")

    if state.trades:
        for t in state.trades[-15:]:
            ts = t["time"]
            short = state.short(t["ticker"])
            side = t["side"]
            count = t["count"]
            price = t["price"]
            notional = count * price / 100

            if side == "yes":
                color = GREEN
                arrow = "BUY "
            else:
                color = RED
                arrow = "SELL"

            lines.append(
                f"  {DIM}{ts}{RESET}  "
                f"{color}{arrow}{RESET}  "
                f"{BOLD}{short:>5}{RESET}  "
                f"{count:>5,} contracts @ "
                f"{color}{price}c{RESET}  "
                f"{DIM}${notional:>8,.2f}{RESET}"
            )
    else:
        lines.append(f"  {DIM}Waiting for trades...{RESET}")

    lines.append(f"\n  {DIM}Ctrl+C to stop{RESET}")

    # Print everything
    output = CLEAR_SCREEN + "\n".join(lines)
    sys.stdout.write(output + "\n")
    sys.stdout.flush()


# ── WebSocket stream ────────────────────────────────────────────────────────
async def stream(tickers):
    state = MarketState()

    # Fetch initial market info via REST
    for t in tickers:
        try:
            data = rest_get(f"/markets/{t}")
            state.info[t] = data.get("market", data)
        except Exception:
            state.info[t] = {"title": t}

    channels = ["ticker", "orderbook_delta", "trade"]
    last_render = 0
    render_interval = 0.3  # redraw at most ~3x/sec

    while True:
        headers = sign("GET", "/trade-api/ws/v2")
        try:
            async with websockets.connect(WS_URL, additional_headers=headers) as ws:
                state.connected_at = time.time()

                # Subscribe
                sub = {
                    "id": 1,
                    "cmd": "subscribe",
                    "params": {"channels": channels, "market_tickers": tickers},
                }
                await ws.send(json.dumps(sub))

                # Initial render
                render(state)

                async for raw in ws:
                    msg = json.loads(raw)
                    state.msg_count += 1

                    # Skip ack messages
                    if "id" in msg and "result" in msg:
                        continue

                    msg_type = msg.get("type", "")
                    payload = msg.get("msg", msg)
                    ticker = payload.get("market_ticker", "")

                    if msg_type == "orderbook_snapshot":
                        state.orderbook.apply_snapshot(ticker, payload)

                    elif msg_type == "orderbook_delta":
                        state.orderbook.apply_delta(ticker, payload)

                    elif msg_type == "ticker":
                        state.tickers[ticker] = payload

                    elif msg_type == "trade":
                        state.trade_count += 1
                        state.trades.append({
                            "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                            "ticker": ticker,
                            "side": payload.get("taker_side", "?"),
                            "count": payload.get("count", 0),
                            "price": payload.get("yes_price", 0),
                        })
                        # Keep only last 50
                        if len(state.trades) > 50:
                            state.trades = state.trades[-50:]

                    # Throttled re-render
                    now = time.time()
                    if now - last_render >= render_interval:
                        render(state)
                        last_render = now

        except websockets.ConnectionClosed:
            sys.stdout.write(f"\n  {RED}Disconnected. Reconnecting in 2s...{RESET}\n")
            sys.stdout.flush()
            await asyncio.sleep(2)
        except Exception as e:
            sys.stdout.write(f"\n  {RED}Error: {e}. Reconnecting in 5s...{RESET}\n")
            sys.stdout.flush()
            await asyncio.sleep(5)


# ── CLI ─────────────────────────────────────────────────────────────────────
def expand_event(event_ticker):
    data = rest_get("/markets", params={"event_ticker": event_ticker, "limit": 50})
    markets = data.get("markets", [])
    found = [m["ticker"] for m in markets if m.get("status") in ("active", "open")]
    if found:
        for m in markets:
            if m["ticker"] in found:
                yes_bid = m.get("yes_bid", 0) or 0
                yes_ask = m.get("yes_ask", 0) or 0
                title = m.get("title", m.get("subtitle", ""))
                print(f"    {BOLD}{m['ticker']}{RESET}  {DIM}{title}{RESET}")
                print(f"      Yes: {GREEN}{yes_bid}c{RESET}/{RED}{yes_ask}c{RESET}  Vol: {m.get('volume', 0):,}")
    return found


def resolve_tickers(args):
    tickers = []
    i = 0
    while i < len(args):
        if args[i] == "--event":
            i += 1
            if i >= len(args):
                print("Error: --event requires an event ticker", file=sys.stderr)
                sys.exit(1)
            event_ticker = args[i].upper()
            print(f"  Looking up event {event_ticker}...")
            try:
                found = expand_event(event_ticker)
                if not found:
                    print(f"  {RED}No active markets found{RESET}")
                    sys.exit(1)
                tickers.extend(found)
            except Exception as e:
                print(f"  {RED}Error: {e}{RESET}", file=sys.stderr)
                sys.exit(1)
        else:
            raw = args[i].strip().rstrip("/")
            if "kalshi.com" in raw:
                raw = raw.split("/")[-1]
            ticker = raw.upper()
            try:
                rest_get(f"/markets/{ticker}")
                tickers.append(ticker)
            except Exception:
                print(f"  {YELLOW}{ticker} not found as market, trying as event...{RESET}")
                try:
                    found = expand_event(ticker)
                    if found:
                        tickers.extend(found)
                    else:
                        print(f"  {RED}No markets found for {ticker}{RESET}")
                        sys.exit(1)
                except Exception as e2:
                    print(f"  {RED}Could not resolve {ticker}: {e2}{RESET}")
                    sys.exit(1)
        i += 1
    return tickers


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    tickers = resolve_tickers(sys.argv[1:])
    print(f"\n  {GREEN}Connecting to {len(tickers)} market(s)...{RESET}\n")

    try:
        asyncio.get_event_loop().run_until_complete(stream(tickers))
    except KeyboardInterrupt:
        # Clear screen artifacts and exit cleanly
        sys.stdout.write(f"\n\n  {BOLD}Stream stopped.{RESET}\n\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
