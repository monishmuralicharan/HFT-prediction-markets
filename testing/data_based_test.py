#!/usr/bin/env python3
"""
Tennis score-driven paper trading test.

Combines live 365Scores tennis score polling (3x/sec) with Kalshi WebSocket
market data. Score changes drive BUY/SELL decisions on the YES side.

STRATEGY 1: "Game-Win Momentum"
───────────────────────────────
  Trades BOTH sides: home player market and away player market.
  Only GAME WINS trigger entries — points are ignored for trading.

  WHEN A PLAYER WINS A GAME (or set):
    - SELL all positions on the OTHER player's market
    - BUY YES on the winning player's market with 1/2 of total balance

  EXIT CONDITIONS (whichever comes first):
    - Take profit at +8%
    - Stop loss at -5%
    - Timeout after 20 seconds

  PRICE CAP:
    - Only BUY when ask < $0.98

STRATEGY 2: "Close Set / Service-Game Buy, Post-Game Sell"
──────────────────────────────────────────────────────────
  Focuses on service games when the match score is close.

  DEFINITIONS:
    set_close     := abs(home_sets_won - away_sets_won) <= 1
    games_close   := abs(home_games - away_games) <= 1
    tied_games    := home_games == away_games
    tied_and_serving(player) := tied_games AND server == player
    down_one_and_serving(player) := player_games == opponent_games - 1 AND server == player

  ENTRY (BUY server's YES):
    IF set_close AND games_close AND NOT tiebreak:
      - tied_and_serving → BUY server YES
      - down_one_and_serving → BUY server YES
    Only 1 position at a time. Enter at start of game (game score 0-0).

  EXIT:
    - SELL immediately when the current game completes (GAME_WON / SET_WON).
    - Stop loss at -5%.

  NO-TRADE FILTERS:
    - Tiebreaks (games == 6-6).
    - Games not close (diff > 1).
    - Server info missing.

DEFAULTS:
  Balance: $1000

Usage:
    python3 testing/data_based_test.py --ticker KXATPMATCH-26FEB11UGOCOM --strategy 1
    python3 testing/data_based_test.py --ticker KXATPMATCH-26FEB11UGOCOM --strategy 2
    python3 testing/data_based_test.py --ticker KXATPMATCH-26FEB11UGOCOM --game-id 12345
"""

from __future__ import annotations

# ── Suppress structlog JSON before any src/ import ────────────────────────────
import logging
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

logging.basicConfig(handlers=[logging.NullHandler()], level=logging.CRITICAL)

import structlog
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL),
)

import argparse
import asyncio
import base64
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
from uuid import UUID

import ssl
import urllib.request
from urllib.parse import urlencode

import certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

# ── src/ imports (after logging suppression) ───────────────────────────────────
from src.db.models import (
    Account,
    ExitReason,
    Market,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.tennis.scores365_client import (
    Scores365Client,
    extract_game_score,
    extract_set_scores,
    extract_serving,
)
from src.tennis.client import parse_kalshi_ticker

# ── Paths / URLs ──────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, "config", "secrets.env")
CONFIG_PATH = os.path.join(ROOT, "config", "config.yaml")
REST_URL = "https://api.elections.kalshi.com/trade-api/v2"
WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"

# ── ANSI ──────────────────────────────────────────────────────────────────────
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"

# ── Tennis score map ──────────────────────────────────────────────────────────
# 365Scores uses numeric scores: 0, 15, 30, 40, 50 (50 = AD)
SCORE_MAP = {0: "0", 15: "15", 30: "30", 40: "40", 50: "AD"}

# Max price at which we'll BUY new contracts (98c = $0.98)
MAX_BUY_PRICE = Decimal("0.98")


# ── Env / Auth ────────────────────────────────────────────────────────────────
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
    if params:
        url = f"{url}?{urlencode(params)}"
    headers = sign("GET", f"/trade-api/v2{path}")
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10, context=_SSL_CTX) as resp:
        return json.loads(resp.read())


# ── Orderbook state ───────────────────────────────────────────────────────────
class OrderbookState:
    def __init__(self):
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

    def get_best_bid_ask(self, ticker):
        """Return (best_yes_bid_cents, best_yes_ask_cents) from orderbook depth."""
        book = self.books[ticker]
        yes_bids = [p for p, s in book["yes"].items() if s > 0]
        no_prices = [p for p, s in book["no"].items() if s > 0]
        best_bid = max(yes_bids) if yes_bids else None
        best_ask = (100 - max(no_prices)) if no_prices else None
        return best_bid, best_ask


# ── Paper Execution Engine ─────────────────────────────────────────────────────
@dataclass
class PaperOrder:
    order: Order
    signal: Optional[object] = None  # entry metadata
    position_id: Optional[UUID] = None
    exit_reason: Optional[ExitReason] = None
    created_at: float = 0.0
    timeout: float = 60.0


class PaperExecutionEngine:
    """Simulates realistic order book fills — orders rest until price crosses."""

    def __init__(self, order_manager: OrderManager, position_tracker: PositionTracker):
        self.order_manager = order_manager
        self.position_tracker = position_tracker
        self.pending_orders: list[PaperOrder] = []

    async def submit_buy(
        self, market_id: str, price: Decimal, size: Decimal, metadata: Optional[dict] = None
    ) -> PaperOrder:
        """Submit a BUY LIMIT order."""
        order = Order(
            market_id=market_id,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=price,
            size=size,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc),
        )
        self.order_manager.add_order(order)
        po = PaperOrder(
            order=order,
            signal=metadata,
            created_at=time.time(),
            timeout=60,
        )
        self.pending_orders.append(po)
        return po

    async def submit_sell(
        self, position: Position, exit_price: Decimal, exit_reason: ExitReason
    ) -> PaperOrder:
        """Submit a SELL LIMIT exit order."""
        order = Order(
            market_id=position.market_id,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=exit_price,
            size=position.position_size,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc),
        )
        self.order_manager.add_order(order)
        po = PaperOrder(
            order=order,
            position_id=position.id,
            exit_reason=exit_reason,
            created_at=time.time(),
            timeout=300,
        )
        self.pending_orders.append(po)
        return po

    def check_fills(self, market: Market) -> list[dict]:
        """Check if any pending orders should fill given current market state."""
        fills = []
        still_pending = []
        now = time.time()

        for po in self.pending_orders:
            if po.order.market_id != market.id:
                still_pending.append(po)
                continue

            if now - po.created_at > po.timeout:
                po.order.status = OrderStatus.CANCELLED
                po.order.cancelled_at = datetime.now(timezone.utc)
                fills.append({"type": "timeout", "order": po})
                continue

            filled = False
            if po.order.side == OrderSide.BUY:
                if market.best_ask is not None and market.best_ask <= po.order.price:
                    filled = True
            elif po.order.side == OrderSide.SELL:
                if market.best_bid is not None and market.best_bid >= po.order.price:
                    filled = True

            if filled:
                po.order.status = OrderStatus.FILLED
                po.order.filled_size = po.order.size
                po.order.avg_fill_price = po.order.price
                po.order.filled_at = datetime.now(timezone.utc)

                if po.signal is not None and po.position_id is None:
                    fills.append({"type": "entry_fill", "order": po})
                else:
                    fills.append({"type": "exit_fill", "order": po})
            else:
                still_pending.append(po)

        self.pending_orders = still_pending
        return fills

    def has_pending_entry(self, market_id: str) -> bool:
        return any(
            po.order.market_id == market_id and po.signal is not None
            for po in self.pending_orders
        )

    def has_pending_exit(self, position_id: UUID) -> bool:
        return any(
            po.position_id == position_id
            for po in self.pending_orders
        )

    def cancel_exits_for_position(self, position_id: UUID):
        """Cancel all pending exit orders for a position."""
        new_pending = []
        for po in self.pending_orders:
            if po.position_id == position_id:
                po.order.status = OrderStatus.CANCELLED
                po.order.cancelled_at = datetime.now(timezone.utc)
            else:
                new_pending.append(po)
        self.pending_orders = new_pending


# ── Tennis Match State ─────────────────────────────────────────────────────────
@dataclass
class TennisMatchState:
    game_id: Optional[int] = None
    home_name: str = ""
    away_name: str = ""
    home_game_score: int = 0     # raw numeric: 0, 15, 30, 40, 50
    away_game_score: int = 0
    set_scores: list = field(default_factory=list)  # [(6,4), (3,2)]
    serving: int = 0             # 1=home, 2=away
    last_update: float = 0.0


class ScoreEvent(Enum):
    POINT_HOME = "POINT_HOME"
    POINT_AWAY = "POINT_AWAY"
    GAME_WON_HOME = "GAME_WON_HOME"
    GAME_WON_AWAY = "GAME_WON_AWAY"
    SET_WON_HOME = "SET_WON_HOME"
    SET_WON_AWAY = "SET_WON_AWAY"


def build_match_state(game: dict) -> TennisMatchState:
    """Extract TennisMatchState from a 365Scores game dict."""
    home = game.get("homeCompetitor", {})
    away = game.get("awayCompetitor", {})

    # Game scores: raw integers from the "Game" stage
    home_gs, away_gs = 0, 0
    for stage in game.get("stages", []):
        if stage.get("shortName") == "Game":
            h = stage.get("homeCompetitorScore", -1)
            a = stage.get("awayCompetitorScore", -1)
            if h >= 0:
                home_gs = int(h)
                away_gs = int(a)
            break

    set_scores = extract_set_scores(game)
    serving = extract_serving(game)

    return TennisMatchState(
        game_id=game.get("id"),
        home_name=(home.get("name") or home.get("symbolicName") or "Home"),
        away_name=(away.get("name") or away.get("symbolicName") or "Away"),
        home_game_score=home_gs,
        away_game_score=away_gs,
        set_scores=set_scores,
        serving=serving,
        last_update=time.time(),
    )


def detect_score_change(old: TennisMatchState, new: TennisMatchState) -> list[ScoreEvent]:
    """Compare two match states and return detected score events."""
    events = []

    # Check for set changes first (higher priority)
    old_sets = old.set_scores
    new_sets = new.set_scores

    # New set appeared
    if len(new_sets) > len(old_sets):
        if len(old_sets) > 0:
            # The set that was in progress is now complete
            finished_set = new_sets[len(old_sets) - 1]
            if finished_set[0] > finished_set[1]:
                events.append(ScoreEvent.SET_WON_HOME)
            else:
                events.append(ScoreEvent.SET_WON_AWAY)
        else:
            # First set just ended
            finished_set = new_sets[0] if new_sets else (0, 0)
            if finished_set[0] > finished_set[1]:
                events.append(ScoreEvent.SET_WON_HOME)
            else:
                events.append(ScoreEvent.SET_WON_AWAY)
        return events

    # Check if the current set's game count changed (game won)
    if old_sets and new_sets and len(old_sets) == len(new_sets):
        old_current = old_sets[-1]
        new_current = new_sets[-1]
        if new_current != old_current:
            # Game scores also reset to 0-0 when a game is won
            home_games_up = new_current[0] > old_current[0]
            away_games_up = new_current[1] > old_current[1]
            if home_games_up:
                events.append(ScoreEvent.GAME_WON_HOME)
            elif away_games_up:
                events.append(ScoreEvent.GAME_WON_AWAY)
            if events:
                return events

    # Check for point-level changes
    old_h, old_a = old.home_game_score, old.away_game_score
    new_h, new_a = new.home_game_score, new.away_game_score

    if old_h == new_h and old_a == new_a:
        return events  # no change

    # Determine who scored
    if new_h > old_h and new_a == old_a:
        # Home score went up
        events.append(ScoreEvent.POINT_HOME)
    elif new_a > old_a and new_h == old_h:
        # Away score went up
        events.append(ScoreEvent.POINT_AWAY)
    elif new_a < old_a and new_h == old_h:
        # Away lost AD (50→40 deuce), home scored
        events.append(ScoreEvent.POINT_HOME)
    elif new_h < old_h and new_a == old_a:
        # Home lost AD, away scored
        events.append(ScoreEvent.POINT_AWAY)
    elif new_h == 0 and new_a == 0 and (old_h != 0 or old_a != 0):
        # Game score reset to 0-0 — game was won, detect via set scores
        # (covered above, but as fallback)
        pass
    else:
        # Both changed — likely a game reset we missed
        if new_h == 0 and new_a == 0:
            pass  # game reset, handled by set check above
        elif new_h > old_h:
            events.append(ScoreEvent.POINT_HOME)
        elif new_a > old_a:
            events.append(ScoreEvent.POINT_AWAY)

    return events


def format_score(state: TennisMatchState) -> str:
    """Format match score for display."""
    sets_str = "  ".join(f"{h}-{a}" for h, a in state.set_scores)
    h_game = SCORE_MAP.get(state.home_game_score, str(state.home_game_score))
    a_game = SCORE_MAP.get(state.away_game_score, str(state.away_game_score))
    serve = "*" if state.serving == 1 else ""
    serve_a = "*" if state.serving == 2 else ""
    return f"{state.home_name}{serve} vs {state.away_name}{serve_a}  [{sets_str}]  Game: {h_game}-{a_game}"


# ── WS-to-Market bridge ──────────────────────────────────────────────────────
@dataclass
class TradingState:
    account: Account
    markets: dict  # {ticker: Market}
    market_info: dict  # {ticker: REST market info}
    tickers_data: dict  # {ticker: raw WS ticker payload}
    orderbook: OrderbookState
    msg_count: int = 0
    trade_count: int = 0
    connected_at: Optional[float] = None
    mode: str = "paper"


def update_market_from_ws(state: TradingState, ticker: str) -> Optional[Market]:
    """Build/update a Market model from WS ticker + orderbook data (cents → dollars)."""
    tick = state.tickers_data.get(ticker, {})
    info = state.market_info.get(ticker, {})

    yes_bid_c = tick.get("yes_bid")
    yes_ask_c = tick.get("yes_ask")
    last_price_c = tick.get("price") or tick.get("yes_price")

    ob_bid_c, ob_ask_c = state.orderbook.get_best_bid_ask(ticker)
    if yes_bid_c is None:
        yes_bid_c = ob_bid_c
    if yes_ask_c is None:
        yes_ask_c = ob_ask_c

    best_bid = Decimal(str(yes_bid_c)) / Decimal("100") if yes_bid_c is not None else None
    best_ask = Decimal(str(yes_ask_c)) / Decimal("100") if yes_ask_c is not None else None
    last_price = Decimal(str(last_price_c)) / Decimal("100") if last_price_c is not None else None

    if best_bid is None and best_ask is None and last_price is None:
        return None

    title = info.get("title", info.get("subtitle", ticker))
    vol = tick.get("volume", info.get("volume", 0))

    close_time = info.get("close_time") or info.get("expiration_time")
    if close_time:
        try:
            end_date = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
        except Exception:
            end_date = datetime.now(timezone.utc)
    else:
        end_date = datetime.now(timezone.utc)

    market = Market(
        id=ticker,
        question=title,
        outcomes=["Yes", "No"],
        end_date=end_date,
        active=True,
        volume_24h=Decimal(str(vol)),
        liquidity=Decimal(str(info.get("liquidity", 0))),
        event_ticker=info.get("event_ticker"),
        series_ticker=info.get("series_ticker"),
        best_bid=best_bid,
        best_ask=best_ask,
        last_price=last_price if last_price is not None else (best_bid or best_ask),
    )
    market.calculate_spread()
    market.calculate_probability()

    state.markets[ticker] = market
    return market


# ── Fill handling (adapted — no auto TP on entry fill) ────────────────────────
def ticker_short(ticker):
    return ticker.split("-")[-1] if ticker else "???"


def fmt_dur(seconds):
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m}m"


async def handle_fills_tennis(
    fills: list[dict],
    state: TradingState,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
):
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

    for fill in fills:
        if fill["type"] == "entry_fill":
            po = fill["order"]
            metadata = po.signal or {}
            fill_ticker = po.order.market_id
            market = state.markets.get(fill_ticker)
            if not market:
                continue

            position = Position(
                market_id=fill_ticker,
                market_question=market.question,
                outcome=market.outcomes[0] if market.outcomes else fill_ticker,
                entry_time=datetime.now(timezone.utc),
                entry_price=po.order.avg_fill_price,
                entry_probability=market.probability if market.probability is not None else po.order.price,
                position_size=po.order.size,
                stop_loss_price=po.order.price * (1 - SL_PCT),  # 5% stop loss
                take_profit_price=Decimal("0.99"),
                entry_order_id=po.order.id,
            )
            tracker.add_position(position)
            state.account.lock_funds(position.position_size)

            reason = metadata.get("reason", "entry")
            short = ticker_short(fill_ticker)
            acct = state.account
            print(
                f"  {DIM}{now_str}{RESET}  {GREEN}OPEN{RESET}  {BOLD}{short}{RESET}  "
                f"({reason})  @${float(po.order.avg_fill_price):.2f}  "
                f"size=${float(position.position_size):.2f}  "
                f"bal=${float(acct.total_balance):.2f}  pnl=${float(acct.daily_pnl):+.2f}"
            )
            print()
            state.trade_count += 1

        elif fill["type"] == "exit_fill":
            po = fill["order"]
            position = tracker.get_position(po.position_id)
            if not position:
                continue

            execution.cancel_exits_for_position(po.position_id)
            exit_reason = po.exit_reason or ExitReason.MANUAL
            tracker.close_position(po.position_id, po.order.avg_fill_price, exit_reason)
            state.account.unlock_funds(position.position_size)
            if position.realized_pnl is not None:
                state.account.record_trade(position.realized_pnl)
                state.account.total_balance += position.realized_pnl
                state.account.available_balance += position.realized_pnl

            short = ticker_short(position.market_id)
            pnl = position.realized_pnl if position.realized_pnl is not None else Decimal("0")
            pnl_color = GREEN if pnl >= 0 else RED
            acct = state.account
            print(
                f"  {DIM}{now_str}{RESET}  {RED}CLOSE{RESET}  {BOLD}{short}{RESET}  "
                f"@${float(po.order.avg_fill_price):.2f}  {exit_reason.value}  "
                f"trade_pnl={pnl_color}${float(pnl):+.2f}{RESET}  "
                f"bal=${float(acct.total_balance):.2f}  pnl=${float(acct.daily_pnl):+.2f}"
            )
            print()
            state.trade_count += 1

        elif fill["type"] == "timeout":
            po = fill["order"]
            short = ticker_short(po.order.market_id)
            print(
                f"  {DIM}{now_str}{RESET}  {YELLOW}TIMEOUT{RESET}  {BOLD}{short}{RESET}  "
                f"@${float(po.order.price):.2f}  cancelled"
            )
            print()


# ── Stop loss check (adapted: -3%, no timeout) ───────────────────────────────
SL_PCT = Decimal("0.05")    # 5% stop loss
TP_PCT = Decimal("0.08")    # 8% take profit
TIMEOUT_SECS = 20           # 20 second timeout


async def check_exits_tennis(
    state: TradingState,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
    strategy: int = 1,
):
    """Check all open positions for stop loss (-5%), take profit (+8%), or timeout (20s, S1 only)."""
    now = datetime.now(timezone.utc)
    for position in tracker.get_open_positions():
        market = state.markets.get(position.market_id)
        if not market:
            continue

        last = market.last_price
        if last is None or position.entry_price == 0:
            continue

        if execution.has_pending_exit(position.id):
            continue

        pnl_pct = (last - position.entry_price) / position.entry_price
        hold_secs = (now - position.entry_time).total_seconds()

        exit_reason = None
        reason_tag = ""

        if pnl_pct <= -SL_PCT:
            exit_reason = ExitReason.STOP_LOSS
            reason_tag = f"{float(pnl_pct * 100):.1f}%"
        elif pnl_pct >= TP_PCT:
            exit_reason = ExitReason.TAKE_PROFIT
            reason_tag = f"+{float(pnl_pct * 100):.1f}%"
        elif strategy == 1 and hold_secs >= TIMEOUT_SECS:
            exit_reason = ExitReason.TIMEOUT
            reason_tag = f"{int(hold_secs)}s"

        if not exit_reason:
            continue

        exit_price = market.best_bid or last
        execution.cancel_exits_for_position(position.id)
        await execution.submit_sell(position, exit_price, exit_reason)
        short = ticker_short(position.market_id)
        now_str = now.strftime("%H:%M:%S")

        color = GREEN if exit_reason == ExitReason.TAKE_PROFIT else YELLOW
        print(
            f"  {DIM}{now_str}{RESET}  {color}{exit_reason.value}{RESET}  {BOLD}{short}{RESET}  "
            f"({reason_tag})  selling @${float(exit_price):.2f}"
        )
        print()


# ── Score event → trading signal processing ───────────────────────────────────

def _can_buy(market: Market) -> bool:
    """Check if the current ask price is below the 98c cap."""
    price = market.best_ask or market.last_price
    return price is not None and price < MAX_BUY_PRICE


async def _sell_all_positions(
    ticker: str,
    state: TradingState,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
    reason_label: str,
    now_str: str,
):
    """Sell all open positions for a given ticker."""
    market = state.markets.get(ticker)
    if not market:
        return
    for pos in tracker.get_open_positions():
        if pos.market_id != ticker:
            continue
        if execution.has_pending_exit(pos.id):
            continue
        exit_price = market.best_bid or market.last_price or pos.entry_price
        execution.cancel_exits_for_position(pos.id)
        await execution.submit_sell(pos, exit_price, ExitReason.MANUAL)
        short = ticker_short(ticker)
        print(
            f"  {DIM}{now_str}{RESET}  {RED}SELL{RESET}  {BOLD}{short}{RESET}  "
            f"{reason_label}  @${float(exit_price):.2f}"
        )


async def _buy_yes(
    ticker: str,
    state: TradingState,
    execution: PaperExecutionEngine,
    bet_size: Decimal,
    reason: str,
    now_str: str,
    is_add: bool = False,
):
    """Buy YES contracts on a ticker. Returns True if order submitted."""
    market = state.markets.get(ticker)
    if not market or not _can_buy(market):
        return False
    buy_price = market.best_ask or market.last_price
    if buy_price is None:
        return False
    num = int(bet_size / buy_price)
    size = Decimal(str(num)) * buy_price
    if num < 1 or size > state.account.available_balance:
        return False
    await execution.submit_buy(ticker, buy_price, size, metadata={"reason": reason})
    short = ticker_short(ticker)
    action = "ADD" if is_add else "BUY"
    prefix = "+" if is_add else ""
    print(
        f"  {DIM}{now_str}{RESET}  {GREEN}{action}{RESET}  {BOLD}{short}{RESET}  "
        f"{prefix}{num} contracts @${float(buy_price):.2f}  ({reason})"
    )
    return True


async def process_score_events(
    events: list[ScoreEvent],
    match_state: TennisMatchState,
    state: TradingState,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
    home_ticker: str,
    away_ticker: Optional[str],
):
    """Convert score events into BUY/SELL orders.

    Only GAME WINS (and set wins) trigger trades.
    Points are logged but don't trigger any trading.
    On game win: sell the other side, buy the winner's YES with 1/2 balance.
    """
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

    for event in events:
        # Determine which side to buy and which to sell
        if event in (ScoreEvent.POINT_HOME, ScoreEvent.GAME_WON_HOME, ScoreEvent.SET_WON_HOME):
            buy_ticker = home_ticker
            sell_ticker = away_ticker
            player_name = match_state.home_name
            is_home = True
        else:
            buy_ticker = away_ticker
            sell_ticker = home_ticker
            player_name = match_state.away_name
            is_home = False

        color = GREEN if is_home else RED
        is_point = event in (ScoreEvent.POINT_HOME, ScoreEvent.POINT_AWAY)
        is_game = event in (ScoreEvent.GAME_WON_HOME, ScoreEvent.GAME_WON_AWAY)
        is_set = event in (ScoreEvent.SET_WON_HOME, ScoreEvent.SET_WON_AWAY)

        # ── Log the score event ──────────────────────────────────────
        if is_point:
            print(
                f"  {DIM}{now_str}{RESET}  {color}POINT {player_name}{RESET}  "
                f"{format_score(match_state)}"
            )
            print()
            continue  # points don't trigger trades

        elif is_game:
            print(
                f"  {DIM}{now_str}{RESET}  {color}GAME WON {player_name}{RESET}  "
                f"{format_score(match_state)}"
            )
        elif is_set:
            print(
                f"  {DIM}{now_str}{RESET}  {color}{BOLD}SET WON {player_name}{RESET}  "
                f"{format_score(match_state)}"
            )

        # ── SELL the other side's positions ───────────────────────────
        if sell_ticker:
            await _sell_all_positions(
                sell_ticker, state, execution, tracker,
                f"{player_name} won game", now_str,
            )

        # ── BUY the winner's YES with 1/2 of total balance ───────────
        if not buy_ticker:
            print()
            continue

        has_buy_position = any(
            pos.market_id == buy_ticker for pos in tracker.get_open_positions()
        )
        has_pending = execution.has_pending_entry(buy_ticker)

        if not has_buy_position and not has_pending:
            half_balance = state.account.available_balance / Decimal("2")
            reason = "game_win" if is_game else "set_win"
            await _buy_yes(
                buy_ticker, state, execution, half_balance,
                reason, now_str, is_add=False,
            )

        print()  # blank line after each score event block


# ── Strategy 2: Close Set / Service-Game Buy, Post-Game Sell ─────────────────

async def process_score_events_s2(
    events: list[ScoreEvent],
    match_state: TennisMatchState,
    state: TradingState,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
    home_ticker: str,
    away_ticker: Optional[str],
):
    """Strategy 2: Buy server's YES in close sets, sell when game completes.

    Entry: When sets close (diff<=1), games close (diff<=1), not tiebreak,
           and server is tied or down one → BUY server's YES with 1/2 balance.
    Exit:  Immediately on game/set completion.
    """
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

    # Compute match state
    set_scores = match_state.set_scores
    home_sets_won = sum(1 for h, a in set_scores if h > a)
    away_sets_won = sum(1 for h, a in set_scores if a > h)
    set_close = abs(home_sets_won - away_sets_won) <= 1

    # Current set game count
    if set_scores:
        home_games, away_games = set_scores[-1]
    else:
        home_games, away_games = 0, 0

    games_close = abs(home_games - away_games) <= 1
    tied_games = home_games == away_games
    tiebreak = home_games >= 6 and away_games >= 6
    serving = match_state.serving  # 1=home, 2=away

    for event in events:
        is_point = event in (ScoreEvent.POINT_HOME, ScoreEvent.POINT_AWAY)
        is_game = event in (ScoreEvent.GAME_WON_HOME, ScoreEvent.GAME_WON_AWAY)
        is_set = event in (ScoreEvent.SET_WON_HOME, ScoreEvent.SET_WON_AWAY)
        is_home_event = event in (ScoreEvent.POINT_HOME, ScoreEvent.GAME_WON_HOME, ScoreEvent.SET_WON_HOME)
        player_name = match_state.home_name if is_home_event else match_state.away_name
        color = GREEN if is_home_event else RED

        # ── Log the score event ──────────────────────────────────────
        if is_point:
            print(
                f"  {DIM}{now_str}{RESET}  {color}POINT {player_name}{RESET}  "
                f"{format_score(match_state)}"
            )
        elif is_game:
            print(
                f"  {DIM}{now_str}{RESET}  {color}GAME WON {player_name}{RESET}  "
                f"{format_score(match_state)}"
            )
        elif is_set:
            print(
                f"  {DIM}{now_str}{RESET}  {color}{BOLD}SET WON {player_name}{RESET}  "
                f"{format_score(match_state)}"
            )

        # ── EXIT: sell on game/set completion ─────────────────────────
        if is_game or is_set:
            open_positions = tracker.get_open_positions()
            for pos in open_positions:
                sell_market = state.markets.get(pos.market_id)
                if not sell_market:
                    continue
                if execution.has_pending_exit(pos.id):
                    continue
                exit_price = sell_market.best_bid or sell_market.last_price or pos.entry_price
                execution.cancel_exits_for_position(pos.id)
                await execution.submit_sell(pos, exit_price, ExitReason.MANUAL)
                short = ticker_short(pos.market_id)
                print(
                    f"  {DIM}{now_str}{RESET}  {RED}SELL{RESET}  {BOLD}{short}{RESET}  "
                    f"game completed  @${float(exit_price):.2f}"
                )

            # Recompute state after game/set win for entry check
            home_sets_won = sum(1 for h, a in set_scores if h > a)
            away_sets_won = sum(1 for h, a in set_scores if a > h)
            set_close = abs(home_sets_won - away_sets_won) <= 1
            if set_scores:
                home_games, away_games = set_scores[-1]
            else:
                home_games, away_games = 0, 0
            games_close = abs(home_games - away_games) <= 1
            tied_games = home_games == away_games
            tiebreak = home_games >= 6 and away_games >= 6

        # ── ENTRY: check conditions for new game ─────────────────────
        # Only enter at start of a new game (after game completion or on first event)
        if is_game or is_set:
            # Already have a position? Skip.
            if tracker.get_open_positions() or execution.has_pending_entry(home_ticker) or (
                away_ticker and execution.has_pending_entry(away_ticker)
            ):
                print()
                continue

            # No server info
            if serving not in (1, 2):
                print(
                    f"  {DIM}{now_str}{RESET}  {DIM}S2: no server info — skipping{RESET}"
                )
                print()
                continue

            # Tiebreak filter
            if tiebreak:
                print(
                    f"  {DIM}{now_str}{RESET}  {DIM}S2: tiebreak ({home_games}-{away_games}) — skipping{RESET}"
                )
                print()
                continue

            # Must be close
            if not set_close or not games_close:
                reason = []
                if not set_close:
                    reason.append(f"sets {home_sets_won}-{away_sets_won}")
                if not games_close:
                    reason.append(f"games {home_games}-{away_games}")
                print(
                    f"  {DIM}{now_str}{RESET}  {DIM}S2: not close ({', '.join(reason)}) — skipping{RESET}"
                )
                print()
                continue

            # Determine entry reason
            server_ticker = home_ticker if serving == 1 else away_ticker
            server_name = match_state.home_name if serving == 1 else match_state.away_name
            server_games = home_games if serving == 1 else away_games
            opp_games = away_games if serving == 1 else home_games

            entry_reason = None
            if tied_games:
                entry_reason = "tied_and_serving"
            elif server_games == opp_games - 1:
                entry_reason = "down_one_and_serving"

            if entry_reason and server_ticker:
                bet_amount = state.account.available_balance * Decimal("0.10")
                print(
                    f"  {DIM}{now_str}{RESET}  {CYAN}S2 ENTRY{RESET}  "
                    f"{BOLD}{server_name}{RESET} serving  "
                    f"sets={home_sets_won}-{away_sets_won}  "
                    f"games={home_games}-{away_games}  "
                    f"reason={entry_reason}"
                )
                await _buy_yes(
                    server_ticker, state, execution, bet_amount,
                    entry_reason, now_str, is_add=False,
                )
            else:
                if not entry_reason:
                    print(
                        f"  {DIM}{now_str}{RESET}  {DIM}S2: server not tied/down_one "
                        f"(games={home_games}-{away_games} serve={'H' if serving==1 else 'A'}) — skipping{RESET}"
                    )

        print()  # blank line after each event block


# ── 365Scores polling task ────────────────────────────────────────────────────
async def poll_365scores(
    client: Scores365Client,
    game_id: int,
    match_state: TennisMatchState,
    signal_queue: asyncio.Queue,
    interval: float = 0.333,
):
    """Poll 365Scores 3x/sec. Only enqueue events when score changes."""
    loop = asyncio.get_running_loop()
    old_state = TennisMatchState(
        game_id=match_state.game_id,
        home_name=match_state.home_name,
        away_name=match_state.away_name,
        home_game_score=match_state.home_game_score,
        away_game_score=match_state.away_game_score,
        set_scores=list(match_state.set_scores),
        serving=match_state.serving,
        last_update=match_state.last_update,
    )

    while True:
        try:
            game = await loop.run_in_executor(None, client.get_match_details, game_id)
            new_state = build_match_state(game)
            events = detect_score_change(old_state, new_state)
            if events:
                # Update the shared match_state in-place
                match_state.home_game_score = new_state.home_game_score
                match_state.away_game_score = new_state.away_game_score
                match_state.set_scores = new_state.set_scores
                match_state.serving = new_state.serving
                match_state.last_update = new_state.last_update

                for event in events:
                    await signal_queue.put((event, new_state))

            old_state = new_state
        except Exception as e:
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"  {DIM}{now_str}{RESET}  {RED}365Scores error: {e}{RESET}")

        await asyncio.sleep(interval)


# ── Session summary ───────────────────────────────────────────────────────────
def print_session_summary(
    state: TradingState,
    tracker: PositionTracker,
    execution: PaperExecutionEngine,
    started_at: float,
):
    duration = time.time() - started_at
    acct = state.account

    open_positions = tracker.get_open_positions()
    closed_positions = list(tracker.closed_positions.values())

    unrealized = Decimal("0")
    for pos in open_positions:
        market = state.markets.get(pos.market_id)
        if market:
            price = market.last_price or market.best_bid or pos.entry_price
            unrealized += pos.calculate_unrealized_pnl(price)

    realized = acct.realized_pnl
    total_pnl = realized + unrealized
    total_trades = acct.daily_trades
    wins = acct.daily_wins
    losses = acct.daily_losses
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0.0

    trade_details = []
    for pos in closed_positions:
        trade_details.append({
            "ticker": ticker_short(pos.market_id),
            "entry": pos.entry_price,
            "exit": pos.exit_price,
            "size": pos.position_size,
            "pnl": pos.realized_pnl or Decimal("0"),
            "reason": pos.exit_reason.value if pos.exit_reason else "?",
            "hold": (pos.exit_time - pos.entry_time).total_seconds() if pos.exit_time else 0,
        })

    pnls = [t["pnl"] for t in trade_details]
    best_pnl = max(pnls) if pnls else Decimal("0")
    worst_pnl = min(pnls) if pnls else Decimal("0")

    out = []
    out.append(f"\n{BOLD}{CYAN}{'━' * 72}")
    out.append(f"  SESSION SUMMARY")
    out.append(f"{'━' * 72}{RESET}")

    out.append(f"\n  Mode: {YELLOW}PAPER{RESET}   Duration: {fmt_dur(duration)}   Messages: {state.msg_count:,}")
    out.append(f"  Markets: {', '.join(ticker_short(t) for t in state.market_info.keys())}")

    out.append(f"\n{CYAN}{'─' * 72}{RESET}")
    out.append(f"  {BOLD}PERFORMANCE{RESET}")
    out.append(f"{CYAN}{'─' * 72}{RESET}")

    pnl_color = GREEN if total_pnl >= 0 else RED
    real_color = GREEN if realized >= 0 else RED
    unreal_color = GREEN if unrealized >= 0 else RED

    starting = float(acct.starting_balance)
    ending = float(acct.total_balance + unrealized)
    ret_pct = ((ending - starting) / starting * 100) if starting > 0 else 0.0

    out.append(f"  Starting balance:  ${starting:>10,.2f}")
    out.append(f"  Ending balance:    ${ending:>10,.2f}")
    out.append(f"  Return:            {pnl_color}{ret_pct:>+10.2f}%{RESET}")
    out.append(f"")
    out.append(f"  Realized P&L:      {real_color}${float(realized):>+10.2f}{RESET}")
    out.append(f"  Unrealized P&L:    {unreal_color}${float(unrealized):>+10.2f}{RESET}")
    out.append(f"  Total P&L:         {pnl_color}${float(total_pnl):>+10.2f}{RESET}")

    out.append(f"\n{CYAN}{'─' * 72}{RESET}")
    out.append(f"  {BOLD}STATISTICS{RESET}")
    out.append(f"{CYAN}{'─' * 72}{RESET}")

    out.append(f"  Total trades:      {total_trades}")
    out.append(f"  Wins / Losses:     {GREEN}{wins}{RESET} / {RED}{losses}{RESET}")
    out.append(f"  Win rate:          {win_rate:.1f}%")
    if pnls:
        out.append(f"  Best trade:        {GREEN}${float(best_pnl):+.2f}{RESET}")
        out.append(f"  Worst trade:       {RED}${float(worst_pnl):+.2f}{RESET}")
    out.append(f"  Messages received: {state.msg_count:,}")
    out.append(f"  Pending orders:    {len(execution.pending_orders)}")
    out.append(f"  Open positions:    {len(open_positions)}")

    if trade_details:
        out.append(f"\n{CYAN}{'─' * 72}{RESET}")
        out.append(f"  {BOLD}CLOSED TRADES{RESET}")
        out.append(f"{CYAN}{'─' * 72}{RESET}")
        out.append(
            f"  {DIM}{'Ticker':>8}  {'Entry':>8}  {'Exit':>8}  "
            f"{'Size':>9}  {'P&L':>9}  {'Reason':>12}  {'Hold':>6}{RESET}"
        )
        for t in trade_details:
            t_color = GREEN if t["pnl"] >= 0 else RED
            out.append(
                f"  {BOLD}{t['ticker']:>8}{RESET}  "
                f"${float(t['entry']):>7.2f}  ${float(t['exit']):>7.2f}  "
                f"${float(t['size']):>8.2f}  "
                f"{t_color}${float(t['pnl']):>+8.2f}{RESET}  "
                f"{t['reason']:>12}  {fmt_dur(t['hold']):>6}"
            )

    if open_positions:
        out.append(f"\n{CYAN}{'─' * 72}{RESET}")
        out.append(f"  {BOLD}STILL OPEN (unrealized){RESET}")
        out.append(f"{CYAN}{'─' * 72}{RESET}")
        out.append(
            f"  {DIM}{'Ticker':>8}  {'Entry':>8}  {'Current':>8}  "
            f"{'Size':>9}  {'P&L':>9}  {'Hold':>6}{RESET}"
        )
        for pos in open_positions:
            market = state.markets.get(pos.market_id)
            current = market.last_price if market and market.last_price else pos.entry_price
            pnl = pos.calculate_unrealized_pnl(current)
            hold_secs = (datetime.now(timezone.utc) - pos.entry_time).total_seconds()
            p_color = GREEN if pnl >= 0 else RED
            out.append(
                f"  {BOLD}{ticker_short(pos.market_id):>8}{RESET}  "
                f"${float(pos.entry_price):>7.2f}  ${float(current):>7.2f}  "
                f"${float(pos.position_size):>8.2f}  "
                f"{p_color}${float(pnl):>+8.2f}{RESET}  "
                f"{fmt_dur(hold_secs):>6}"
            )

    out.append(f"\n{CYAN}{'━' * 72}{RESET}\n")

    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


# ── Main loop ─────────────────────────────────────────────────────────────────
async def run(ticker: str, args):
    balance = Decimal(str(args.balance))
    strategy = args.strategy

    # Select strategy function
    if strategy == 2:
        strategy_fn = process_score_events_s2
        strategy_label = "S2: Close Set / Service-Game"
    else:
        strategy_fn = process_score_events
        strategy_label = "S1: Game-Win Momentum"

    order_manager = OrderManager()
    position_tracker = PositionTracker()
    execution = PaperExecutionEngine(order_manager, position_tracker)

    account = Account(
        address="tennis-paper",
        total_balance=balance,
        available_balance=balance,
        starting_balance=balance,
        daily_starting_balance=balance,
    )

    state = TradingState(
        account=account,
        markets={},
        market_info={},
        tickers_data={},
        orderbook=OrderbookState(),
        mode="paper",
    )

    # Parse ticker to get player codes (works with or without -SHA suffix)
    parsed = parse_kalshi_ticker(ticker)
    if not parsed:
        print(f"  {RED}Could not parse ticker: {ticker}{RESET}")
        sys.exit(1)

    home_code = parsed["code1"]
    away_code = parsed["code2"]

    # Resolve both home and away market tickers
    home_ticker = None
    away_ticker = None

    try:
        # Check if the input is already a market ticker (has player suffix)
        data = rest_get(f"/markets/{ticker}")
        mkt = data.get("market", data)
        # It's a direct market ticker — figure out which side it is
        if ticker.endswith(f"-{home_code}"):
            home_ticker = ticker
            state.market_info[ticker] = mkt
            # Derive away ticker
            away_ticker = ticker.rsplit("-", 1)[0] + f"-{away_code}"
        elif ticker.endswith(f"-{away_code}"):
            away_ticker = ticker
            state.market_info[ticker] = mkt
            home_ticker = ticker.rsplit("-", 1)[0] + f"-{home_code}"
        else:
            home_ticker = ticker
            state.market_info[ticker] = mkt
    except Exception:
        # Ticker not found as market — try as event ticker
        pass

    if not home_ticker:
        try:
            event_data = rest_get("/markets", {"event_ticker": ticker, "limit": 10})
            event_markets = event_data.get("markets", [])
            for m in event_markets:
                if m["ticker"].endswith(f"-{home_code}") and m.get("status") in ("active", "open"):
                    home_ticker = m["ticker"]
                    state.market_info[home_ticker] = m
                elif m["ticker"].endswith(f"-{away_code}") and m.get("status") in ("active", "open"):
                    away_ticker = m["ticker"]
                    state.market_info[away_ticker] = m
        except Exception as e2:
            print(f"  {RED}Failed to resolve ticker {ticker}: {e2}{RESET}")
            sys.exit(1)

    if not home_ticker:
        print(f"  {RED}No active market found for home player {home_code}{RESET}")
        sys.exit(1)

    # Fetch away market info if we only derived it
    if away_ticker and away_ticker not in state.market_info:
        try:
            data = rest_get(f"/markets/{away_ticker}")
            state.market_info[away_ticker] = data.get("market", data)
        except Exception:
            print(f"  {YELLOW}Could not fetch away market {away_ticker} — away trading disabled{RESET}")
            away_ticker = None

    # Fetch home market info if we only derived it
    if home_ticker and home_ticker not in state.market_info:
        try:
            data = rest_get(f"/markets/{home_ticker}")
            state.market_info[home_ticker] = data.get("market", data)
        except Exception:
            pass

    all_tickers = [home_ticker]
    if away_ticker:
        all_tickers.append(away_ticker)

    home_info = state.market_info.get(home_ticker, {})
    away_info = state.market_info.get(away_ticker, {}) if away_ticker else {}

    print(f"\n  {BOLD}{CYAN}━━━ Tennis Score-Driven Paper Trading ━━━{RESET}")
    print(f"  {MAGENTA}Strategy: {strategy_label}{RESET}")
    print(f"  {DIM}Players: {parsed['code1']} vs {parsed['code2']}{RESET}")
    print(
        f"  {GREEN}HOME{RESET} {home_ticker}  "
        f"yes={home_info.get('yes_bid', '?')}c/{home_info.get('yes_ask', '?')}c"
    )
    if away_ticker:
        print(
            f"  {RED}AWAY{RESET} {away_ticker}  "
            f"yes={away_info.get('yes_bid', '?')}c/{away_info.get('yes_ask', '?')}c"
        )
    print(f"  {DIM}Balance: ${float(balance):.2f}  Trade size: 1/2 balance{RESET}")

    # Find matching 365Scores game
    client = Scores365Client()
    game_id = args.game_id

    if game_id:
        print(f"  {DIM}Using provided game ID: {game_id}{RESET}")
        game = client.get_match_details(game_id)
    else:
        print(f"  {DIM}Searching for matching 365Scores game...{RESET}")
        game = client.find_match_for_kalshi(ticker)

    if not game:
        print(f"  {RED}No matching 365Scores game found!{RESET}")
        print(f"  {DIM}Try providing --game-id manually{RESET}")

        # List available live matches
        try:
            live = client.get_live_matches()
            if live:
                print(f"\n  {BOLD}Live tennis matches:{RESET}")
                for g in live[:10]:
                    home = g.get("homeCompetitor", {})
                    away = g.get("awayCompetitor", {})
                    h_name = home.get("name", "?")
                    a_name = away.get("name", "?")
                    h_code = home.get("symbolicName", "?")
                    a_code = away.get("symbolicName", "?")
                    gid = g.get("id", "?")
                    game_h, game_a = extract_game_score(g)
                    sets = extract_set_scores(g)
                    sets_str = "  ".join(f"{h}-{a}" for h, a in sets) if sets else ""
                    print(
                        f"    {DIM}ID={gid}{RESET}  {h_name} ({h_code}) vs {a_name} ({a_code})  "
                        f"[{sets_str}]  Game: {game_h}-{game_a}"
                    )
        except Exception:
            pass
        sys.exit(1)

    game_id = game.get("id")
    match_state = build_match_state(game)
    print(f"  {GREEN}Found match: {match_state.home_name} vs {match_state.away_name}  (ID={game_id}){RESET}")
    print(f"  {DIM}Score: {format_score(match_state)}{RESET}")
    print(f"  {DIM}Polling 365Scores 3x/sec. Ctrl+C to stop.{RESET}\n")

    signal_queue: asyncio.Queue = asyncio.Queue()
    started_at = time.time()
    last_sl_check = 0
    last_quote_log = 0

    # Start 365Scores polling as a background task
    poll_task = asyncio.create_task(
        poll_365scores(client, game_id, match_state, signal_queue, interval=0.333)
    )

    ws_recv_task = None
    queue_wait_task = None

    try:
        while True:
            headers = sign("GET", "/trade-api/ws/v2")
            try:
                async with websockets.connect(WS_URL, extra_headers=headers, ssl=_SSL_CTX) as ws:
                    state.connected_at = time.time()
                    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                    print(f"  {DIM}{now_str}{RESET}  {GREEN}Kalshi WS connected{RESET}")

                    sub = {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {
                            "channels": ["ticker", "orderbook_delta"],
                            "market_tickers": all_tickers,
                        },
                    }
                    await ws.send(json.dumps(sub))

                    ws_recv_task = None
                    queue_wait_task = None

                    while True:
                        # Create pending tasks for both WS recv and queue
                        if ws_recv_task is None:
                            ws_recv_task = asyncio.ensure_future(ws.recv())
                        if queue_wait_task is None:
                            queue_wait_task = asyncio.ensure_future(signal_queue.get())

                        # Wait for EITHER a WS message OR a score event, with 1s timeout
                        done, _ = await asyncio.wait(
                            {ws_recv_task, queue_wait_task},
                            timeout=1.0,
                            return_when=asyncio.FIRST_COMPLETED,
                        )

                        # Handle WS message if one arrived
                        if ws_recv_task in done:
                            raw = ws_recv_task.result()
                            ws_recv_task = None  # will recreate next iteration

                            msg = json.loads(raw)
                            state.msg_count += 1

                            if not ("id" in msg and "result" in msg):
                                msg_type = msg.get("type", "")
                                payload = msg.get("msg", msg)
                                msg_ticker = payload.get("market_ticker", "")

                                if msg_type == "orderbook_snapshot":
                                    state.orderbook.apply_snapshot(msg_ticker, payload)
                                elif msg_type == "orderbook_delta":
                                    state.orderbook.apply_delta(msg_ticker, payload)
                                elif msg_type == "ticker":
                                    state.tickers_data[msg_ticker] = payload

                                if msg_ticker:
                                    market = update_market_from_ws(state, msg_ticker)
                                    if market:
                                        fills = execution.check_fills(market)
                                        if fills:
                                            await handle_fills_tennis(
                                                fills, state, execution,
                                                position_tracker,
                                            )

                        # Handle score event if one arrived
                        if queue_wait_task in done:
                            event, new_match_state = queue_wait_task.result()
                            queue_wait_task = None  # will recreate next iteration

                            await strategy_fn(
                                [event], new_match_state, state, execution,
                                position_tracker, home_ticker, away_ticker,
                            )

                        # Also drain any additional queued events
                        while not signal_queue.empty():
                            try:
                                event, new_match_state = signal_queue.get_nowait()
                                await strategy_fn(
                                    [event], new_match_state, state, execution,
                                    position_tracker, home_ticker, away_ticker,
                                )
                            except asyncio.QueueEmpty:
                                break

                        # Periodic quote + score log (every 1s)
                        now = time.time()
                        if now - last_quote_log >= 1.0:
                            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

                            # Calculate balance including unrealized P&L
                            unrealized = Decimal("0")
                            for pos in position_tracker.get_open_positions():
                                mkt = state.markets.get(pos.market_id)
                                if mkt and mkt.last_price is not None:
                                    unrealized += pos.calculate_unrealized_pnl(mkt.last_price)
                            total_bal = state.account.total_balance + unrealized
                            pnl = state.account.realized_pnl + unrealized
                            pnl_color = GREEN if pnl >= 0 else RED

                            # Market prices: bid/ask/last for each ticker
                            parts = []
                            for qt in all_tickers:
                                tick = state.tickers_data.get(qt, {})
                                bid = tick.get("yes_bid")
                                ask = tick.get("yes_ask")
                                last_p = tick.get("price") or tick.get("yes_price")
                                bid_s = f"{bid}c" if bid is not None else "-"
                                ask_s = f"{ask}c" if ask is not None else "-"
                                last_s = f"{last_p}c" if last_p is not None else "-"
                                short = ticker_short(qt)
                                parts.append(
                                    f"{BOLD}{short}{RESET} "
                                    f"{GREEN}{bid_s}{RESET}/{RED}{ask_s}{RESET}/{last_s}"
                                )
                            pos_count = len(position_tracker.get_open_positions())
                            pos_tag = f"  pos={pos_count}" if pos_count > 0 else ""
                            print(
                                f"  {DIM}{now_str}{RESET}  {' | '.join(parts)}  "
                                f"bal=${float(total_bal):.2f}  "
                                f"pnl={pnl_color}${float(pnl):+.2f}{RESET}{pos_tag}"
                            )

                            # Score line
                            sets_str = " ".join(f"{h}-{a}" for h, a in match_state.set_scores)
                            h_game = SCORE_MAP.get(match_state.home_game_score, str(match_state.home_game_score))
                            a_game = SCORE_MAP.get(match_state.away_game_score, str(match_state.away_game_score))
                            serve_h = "*" if match_state.serving == 1 else ""
                            serve_a = "*" if match_state.serving == 2 else ""
                            print(
                                f"  {DIM}{now_str}{RESET}  "
                                f"{BOLD}{match_state.home_name}{RESET}{serve_h} vs "
                                f"{BOLD}{match_state.away_name}{RESET}{serve_a}  "
                                f"{CYAN}[{sets_str}]{RESET}  "
                                f"{GREEN}{h_game}-{a_game}{RESET}"
                            )
                            print()
                            last_quote_log = now

                        # Periodic stop loss check (runs even with no messages)
                        if now - last_sl_check >= 1.0:
                            await check_exits_tennis(state, execution, position_tracker, strategy)
                            last_sl_check = now

            except websockets.ConnectionClosed:
                for t in (ws_recv_task, queue_wait_task):
                    if t and not t.done():
                        t.cancel()
                print(f"  {RED}Disconnected. Reconnecting in 2s...{RESET}")
                await asyncio.sleep(2)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                for t in (ws_recv_task, queue_wait_task):
                    if t and not t.done():
                        t.cancel()
                print(f"  {RED}WS error: {e}. Reconnecting in 5s...{RESET}")
                await asyncio.sleep(5)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"\n  {DIM}Stopping...{RESET}\n")
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        print_session_summary(state, position_tracker, execution, started_at)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Tennis score-driven paper trading (365Scores + Kalshi WS)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 testing/data_based_test.py --ticker KXATPMATCH-26FEB11UGOCOM
  python3 testing/data_based_test.py --ticker KXATPMATCH-26FEB11UGOCOM --balance 1000
  python3 testing/data_based_test.py --ticker KXATPMATCH-26FEB11UGOCOM --game-id 12345
        """,
    )
    parser.add_argument(
        "--ticker", required=True, help="Kalshi tennis market ticker"
    )
    parser.add_argument(
        "--strategy", type=int, default=1, choices=[1, 2],
        help="Strategy: 1=Game-Win Momentum, 2=Close Set Service-Game (default: 1)",
    )
    parser.add_argument(
        "--balance", type=float, default=1000,
        help="Starting paper balance (default: 1000)",
    )
    parser.add_argument(
        "--game-id", type=int, dest="game_id", default=None,
        help="365Scores game ID (skip auto-match)",
    )

    args = parser.parse_args()
    args.ticker = args.ticker.strip().upper()

    try:
        asyncio.run(run(args.ticker, args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
