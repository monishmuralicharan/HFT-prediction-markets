#!/usr/bin/env python3
"""
Live test runner for the Kalshi HFT bot.

Connects to live WebSocket market data, runs the full StrategyEngine → RiskManager
→ Execution pipeline, and displays a real-time terminal dashboard.

Paper mode (default): simulates realistic order book fills — orders rest until
price crosses them, just like real limit orders.

Live mode (--live): places real orders via Kalshi API (with safety confirmation).

Usage:
    python3 testing/live_test.py KXNCAAMBGAME-26JAN29SFPACHS-SFPA
    python3 testing/live_test.py https://kalshi.com/markets/kxncaambgame/.../ticker
    python3 testing/live_test.py --event KXNCAAMBGAME
    python3 testing/live_test.py TICKER --live
    python3 testing/live_test.py TICKER --balance 500
    python3 testing/live_test.py TICKER --threshold 0.70
    python3 testing/live_test.py TICKER --take-profit 0.03 --stop-loss 0.02
"""

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
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID

import ssl
import urllib.request
from urllib.parse import urlencode

import certifi

_SSL_CTX = ssl.create_default_context(cafile=certifi.where())

import websockets
import yaml
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
from src.risk.manager import RiskManager
from src.strategy.engine import StrategyEngine
from src.strategy.signals import TradingSignal

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



# ── Env / Auth (self-contained, same as stream.py) ────────────────────────────
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


# ── Config loader ─────────────────────────────────────────────────────────────
def load_config():
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


# ── Ticker resolution (same as stream.py) ─────────────────────────────────────
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


def resolve_tickers(raw_args):
    tickers = []
    i = 0
    while i < len(raw_args):
        if raw_args[i] == "--event":
            i += 1
            if i >= len(raw_args):
                print("Error: --event requires an event ticker", file=sys.stderr)
                sys.exit(1)
            event_ticker = raw_args[i].upper()
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
            raw = raw_args[i].strip().rstrip("/")
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


# ── Orderbook state (same as stream.py) ───────────────────────────────────────
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

    def get_top(self, ticker, depth=5):
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

    def get_best_bid_ask(self, ticker):
        """Return (best_yes_bid_cents, best_yes_ask_cents) from orderbook depth."""
        book = self.books[ticker]
        yes_bids = [p for p, s in book["yes"].items() if s > 0]
        no_prices = [p for p, s in book["no"].items() if s > 0]
        best_bid = max(yes_bids) if yes_bids else None
        # Implied yes ask = 100 - best_no_bid
        best_ask = (100 - max(no_prices)) if no_prices else None
        return best_bid, best_ask


# ── Paper Execution Engine ─────────────────────────────────────────────────────
@dataclass
class PaperOrder:
    order: Order
    signal: Optional[TradingSignal]  # set for entry orders
    position_id: Optional[UUID]  # set for exit orders
    exit_reason: Optional[ExitReason]  # set for exit orders
    created_at: float
    timeout: float  # seconds


class PaperExecutionEngine:
    """Simulates realistic order book fills — orders rest until price crosses."""

    def __init__(self, order_manager: OrderManager, position_tracker: PositionTracker):
        self.order_manager = order_manager
        self.position_tracker = position_tracker
        self.pending_orders: list[PaperOrder] = []

    async def execute_signal(self, signal: TradingSignal) -> Optional[Position]:
        """Submit a BUY LIMIT entry order. Returns None; position created on fill."""
        entry_order = Order(
            market_id=signal.market.id,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=signal.entry_price,
            size=signal.position_size,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc),
        )
        self.order_manager.add_order(entry_order)
        self.pending_orders.append(PaperOrder(
            order=entry_order,
            signal=signal,
            position_id=None,
            exit_reason=None,
            created_at=time.time(),
            timeout=60,
        ))
        return None

    async def close_position(
        self, position: Position, exit_price: Decimal, exit_reason: ExitReason
    ) -> bool:
        """Submit a SELL LIMIT exit order — waits for fill like entry."""
        exit_order = Order(
            market_id=position.market_id,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=exit_price,
            size=position.position_size,
            status=OrderStatus.SUBMITTED,
            submitted_at=datetime.now(timezone.utc),
        )
        self.order_manager.add_order(exit_order)
        self.pending_orders.append(PaperOrder(
            order=exit_order,
            signal=None,
            position_id=position.id,
            exit_reason=exit_reason,
            created_at=time.time(),
            timeout=300,
        ))
        return True

    def check_fills(self, market: Market) -> list[dict]:
        """Check if any pending orders should fill given current market state."""
        fills = []
        still_pending = []
        now = time.time()

        for po in self.pending_orders:
            if po.order.market_id != market.id:
                still_pending.append(po)
                continue

            # Check timeout
            if now - po.created_at > po.timeout:
                po.order.status = OrderStatus.CANCELLED
                po.order.cancelled_at = datetime.now(timezone.utc)
                fills.append({"type": "timeout", "order": po})
                continue

            filled = False
            if po.order.side == OrderSide.BUY:
                # BUY fills when ask <= our price
                if market.best_ask is not None and market.best_ask <= po.order.price:
                    filled = True
            elif po.order.side == OrderSide.SELL:
                # SELL fills when bid >= our price
                if market.best_bid is not None and market.best_bid >= po.order.price:
                    filled = True

            if filled:
                po.order.status = OrderStatus.FILLED
                po.order.filled_size = po.order.size
                po.order.avg_fill_price = po.order.price
                po.order.filled_at = datetime.now(timezone.utc)

                if po.signal and not po.position_id:
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

    def cancel_other_exit(self, position_id: UUID, keep_order_id: str):
        """Cancel the other exit order for a position (SL if TP filled, or vice versa)."""
        new_pending = []
        for po in self.pending_orders:
            if po.position_id == position_id and po.order.id != keep_order_id:
                po.order.status = OrderStatus.CANCELLED
                po.order.cancelled_at = datetime.now(timezone.utc)
            else:
                new_pending.append(po)
        self.pending_orders = new_pending


# ── Live Test State ────────────────────────────────────────────────────────────
@dataclass
class LiveTestState:
    account: Account
    markets: dict  # {ticker: Market}
    market_info: dict  # {ticker: REST market info}
    tickers_data: dict  # {ticker: raw WS ticker payload}
    orderbook: OrderbookState
    msg_count: int = 0
    trade_count: int = 0
    connected_at: Optional[float] = None
    mode: str = "paper"


# ── WS-to-Market bridge ──────────────────────────────────────────────────────
def update_market_from_ws(state: LiveTestState, ticker: str) -> Optional[Market]:
    """Build/update a Market model from WS ticker + orderbook data (cents → dollars)."""
    tick = state.tickers_data.get(ticker, {})
    info = state.market_info.get(ticker, {})

    # Get prices from ticker channel (cents)
    yes_bid_c = tick.get("yes_bid")
    yes_ask_c = tick.get("yes_ask")
    last_price_c = tick.get("price") or tick.get("yes_price")

    # Fall back to orderbook-derived best bid/ask
    ob_bid_c, ob_ask_c = state.orderbook.get_best_bid_ask(ticker)
    if yes_bid_c is None:
        yes_bid_c = ob_bid_c
    if yes_ask_c is None:
        yes_ask_c = ob_ask_c

    # Convert cents → dollars (Decimal)
    best_bid = Decimal(str(yes_bid_c)) / Decimal("100") if yes_bid_c is not None else None
    best_ask = Decimal(str(yes_ask_c)) / Decimal("100") if yes_ask_c is not None else None
    last_price = Decimal(str(last_price_c)) / Decimal("100") if last_price_c is not None else None

    # Need at least some price data
    if best_bid is None and best_ask is None and last_price is None:
        return None

    title = info.get("title", info.get("subtitle", ticker))
    vol = tick.get("volume", info.get("volume", 0))

    # Parse end date
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


# ── Fill handling ─────────────────────────────────────────────────────────────
async def handle_fills(
    fills: list[dict],
    state: LiveTestState,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
):
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

    for fill in fills:
        if fill["type"] == "entry_fill":
            po = fill["order"]
            signal = po.signal
            position = Position(
                market_id=signal.market.id,
                market_question=signal.market.question,
                outcome=signal.market.outcomes[0] if signal.market.outcomes else signal.market.id,
                entry_time=datetime.now(timezone.utc),
                entry_price=po.order.avg_fill_price,
                entry_probability=signal.market.probability if signal.market.probability is not None else signal.entry_price,
                position_size=signal.position_size,
                stop_loss_price=signal.stop_loss_price,
                take_profit_price=signal.take_profit_price,
                entry_order_id=po.order.id,
            )
            tracker.add_position(position)
            state.account.lock_funds(position.position_size)

            # Submit only TP as pending SELL order (SL is time-based, handled in main loop)
            await execution.close_position(position, signal.take_profit_price, ExitReason.TAKE_PROFIT)

            short = ticker_short(signal.market.id)
            acct = state.account
            print(
                f"  {DIM}{now_str}{RESET}  {GREEN}OPEN{RESET}  {BOLD}{short}{RESET}  "
                f"@${float(po.order.avg_fill_price):.2f}  size=${float(signal.position_size):.2f}  "
                f"TP=${float(signal.take_profit_price):.2f}  SL=-5%/60s  "
                f"bal=${float(acct.total_balance):.2f}  pnl=${float(acct.daily_pnl):+.2f}"
            )
            state.trade_count += 1

        elif fill["type"] == "exit_fill":
            po = fill["order"]
            position = tracker.get_position(po.position_id)
            if not position:
                continue

            # Cancel the other exit order
            execution.cancel_other_exit(po.position_id, po.order.id)

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
            state.trade_count += 1

        elif fill["type"] == "timeout":
            po = fill["order"]
            ticker_id = po.order.market_id
            short = ticker_short(ticker_id)
            print(
                f"  {DIM}{now_str}{RESET}  {YELLOW}TIMEOUT{RESET}  {BOLD}{short}{RESET}  "
                f"@${float(po.order.price):.2f}  cancelled"
            )


# ── Signal generation ─────────────────────────────────────────────────────────
async def maybe_generate_signal(
    market: Market,
    state: LiveTestState,
    strategy: StrategyEngine,
    risk: RiskManager,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
):
    if tracker.has_position_for_market(market.id):
        return
    if execution.has_pending_entry(market.id):
        return

    # Only buy lines at 85%+ probability
    prob = market.probability if market.probability is not None else market.last_price
    if prob is None or prob < Decimal("0.85"):
        return

    signal = strategy.evaluate_market(market, state.account)
    now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
    short = ticker_short(market.id)

    if not signal:
        return

    is_valid, error = risk.validate_signal(signal, state.account, tracker.get_open_count())

    if is_valid:
        await execution.execute_signal(signal)
        print(
            f"  {DIM}{now_str}{RESET}  {GREEN}SIGNAL{RESET}  {BOLD}{short}{RESET}  "
            f"{signal.strength.value}  conf={float(signal.confidence):.0f}  "
            f"@${float(signal.entry_price):.2f}  {GREEN}PENDING{RESET}"
        )
    else:
        print(
            f"  {DIM}{now_str}{RESET}  {RED}SIGNAL{RESET}  {BOLD}{short}{RESET}  "
            f"{signal.strength.value}  conf={float(signal.confidence):.0f}  "
            f"@${float(signal.entry_price):.2f}  {RED}REJECTED{RESET}  {error or ''}"
        )


# ── Strategy-driven exits (timeout/market-close) ─────────────────────────────
async def check_strategy_exits(
    state: LiveTestState,
    strategy: StrategyEngine,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
):
    for position in tracker.get_open_positions():
        market = state.markets.get(position.market_id)
        if not market:
            continue
        current_price = market.last_price or market.best_bid or market.best_ask
        if current_price is None:
            continue

        should_exit, reason_str = strategy.check_exit(position, current_price)
        if should_exit and reason_str in ("TIMEOUT", "MARKET_CLOSED"):
            exit_price = market.best_bid or current_price
            await execution.close_position(position, exit_price, ExitReason(reason_str))


SL_TIMEOUT_SECONDS = 60
SL_LOSS_PCT = Decimal("0.05")


async def check_stop_loss(
    state: LiveTestState,
    execution: PaperExecutionEngine,
    tracker: PositionTracker,
):
    """Close any position that is down -5% or held longer than 30s."""
    now = datetime.now(timezone.utc)
    for position in tracker.get_open_positions():
        market = state.markets.get(position.market_id)
        if not market:
            continue
        exit_price = market.best_bid or market.last_price or position.entry_price
        if exit_price is None:
            continue

        hold_secs = (now - position.entry_time).total_seconds()
        pnl_pct = (exit_price - position.entry_price) / position.entry_price if position.entry_price else Decimal("0")
        triggered_by_loss = pnl_pct <= -SL_LOSS_PCT
        triggered_by_time = hold_secs >= SL_TIMEOUT_SECONDS

        if not triggered_by_loss and not triggered_by_time:
            continue

        # Cancel any pending TP order for this position before submitting SL
        execution.cancel_other_exit(position.id, "")

        reason = f"-{float(pnl_pct * 100):.1f}%" if triggered_by_loss else f"{int(hold_secs)}s"
        await execution.close_position(position, exit_price, ExitReason.STOP_LOSS)
        short = ticker_short(position.market_id)
        print(
            f"  {DIM}{now.strftime('%H:%M:%S')}{RESET}  {YELLOW}STOP LOSS{RESET}  {BOLD}{short}{RESET}  "
            f"({reason})  selling @${float(exit_price):.2f}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────────
def ticker_short(ticker):
    return ticker.split("-")[-1] if ticker else "???"


def fmt_dur(seconds):
    """Format seconds as Xm or Xh Ym."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        return f"{int(seconds // 60)}m"
    else:
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        return f"{h}h{m}m"


# ── Session summary ───────────────────────────────────────────────────────────
def print_session_summary(
    state: LiveTestState,
    tracker: PositionTracker,
    execution: PaperExecutionEngine,
    started_at: float,
):
    duration = time.time() - started_at
    acct = state.account

    # Cancel any remaining pending orders for clean accounting
    open_positions = tracker.get_open_positions()
    closed_positions = list(tracker.closed_positions.values())

    # Calculate unrealized P&L on still-open positions
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

    # Collect per-trade details for the trade table
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

    # Best / worst trade
    pnls = [t["pnl"] for t in trade_details]
    best_pnl = max(pnls) if pnls else Decimal("0")
    worst_pnl = min(pnls) if pnls else Decimal("0")

    # Print
    out = []
    out.append(f"\n{BOLD}{CYAN}{'━' * 72}")
    out.append(f"  SESSION SUMMARY")
    out.append(f"{'━' * 72}{RESET}")

    mode_str = f"{YELLOW}PAPER{RESET}" if state.mode == "paper" else f"{RED}LIVE{RESET}"
    out.append(f"\n  Mode: {mode_str}   Duration: {fmt_dur(duration)}   Messages: {state.msg_count:,}")
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

    # Trade log table
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

    # Open positions still held
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
async def run(tickers, args):
    cfg = load_config()
    strat_cfg = cfg.get("strategy", {})
    risk_cfg = cfg.get("risk", {})
    pos_cfg = cfg.get("positions", {})

    # Override from CLI args
    entry_threshold = Decimal(str(args.threshold or strat_cfg.get("entry_threshold", 0.85)))
    take_profit_pct = Decimal(str(args.take_profit or strat_cfg.get("take_profit_pct", 0.03)))
    stop_loss_pct = Decimal(str(args.stop_loss or strat_cfg.get("stop_loss_pct", 0.01)))
    max_hold = args.max_hold or strat_cfg.get("max_hold_time_hours", 2)
    max_pos_size_pct = Decimal(str(risk_cfg.get("max_position_size_pct", 0.10)))
    min_pos_size = Decimal(str(pos_cfg.get("min_position_size", 50)))
    max_pos_size = Decimal(str(pos_cfg.get("max_position_size", 1000)))
    balance = Decimal(str(args.balance))

    # Wire components
    strategy = StrategyEngine(
        entry_threshold=entry_threshold,
        take_profit_pct=take_profit_pct,
        stop_loss_pct=stop_loss_pct,
        max_hold_time_hours=max_hold,
        max_position_size_pct=max_pos_size_pct,
        min_position_size=min_pos_size,
        max_position_size=max_pos_size,
    )
    risk_manager = RiskManager(
        max_position_size_pct=max_pos_size_pct,
        max_total_exposure_pct=Decimal(str(risk_cfg.get("max_total_exposure_pct", 0.30))),
        max_concurrent_positions=pos_cfg.get("max_concurrent", 10),
        max_daily_loss_pct=Decimal(str(risk_cfg.get("max_daily_loss_pct", 0.05))),
        max_consecutive_losses=risk_cfg.get("max_consecutive_losses", 5),
        api_error_threshold=Decimal(str(risk_cfg.get("api_error_threshold", 0.10))),
        max_disconnect_seconds=risk_cfg.get("max_disconnect_seconds", 15),
    )
    order_manager = OrderManager()
    position_tracker = PositionTracker()

    account = Account(
        address="live-test",
        total_balance=balance,
        available_balance=balance,
        starting_balance=balance,
        daily_starting_balance=balance,
    )

    # Paper vs live execution engine
    if args.mode == "live":
        from src.api.auth import KalshiAuth
        from src.api.kalshi import KalshiClient

        auth = KalshiAuth(key_id=API_KEY_ID, private_key=ENV["KALSHI_PRIVATE_KEY"])
        api_client = KalshiClient(
            base_url=REST_URL,
            auth=auth,
        )
        from src.execution.engine import ExecutionEngine
        execution = ExecutionEngine(api_client, order_manager, position_tracker)
        is_paper = False
    else:
        execution = PaperExecutionEngine(order_manager, position_tracker)
        is_paper = True

    state = LiveTestState(
        account=account,
        markets={},
        market_info={},
        tickers_data={},
        orderbook=OrderbookState(),
        mode=args.mode,
    )

    # Fetch initial market info via REST
    for t in tickers:
        try:
            data = rest_get(f"/markets/{t}")
            state.market_info[t] = data.get("market", data)
        except Exception:
            state.market_info[t] = {"title": t}

    channels = ["ticker", "orderbook_delta"]
    last_exit_check = 0
    exit_check_interval = 3.0
    last_sl_check = 0
    started_at = time.time()
    last_quote_log = 0
    quote_interval = 1.0

    mode_str = f"{YELLOW}PAPER{RESET}" if is_paper else f"{RED}LIVE{RESET}"
    print(f"\n  {BOLD}{CYAN}━━━ Kalshi Live Test ━━━{RESET}  {mode_str}  bal=${float(balance):.2f}  tickers={len(tickers)}")
    print(f"  {DIM}Ctrl+C to stop{RESET}\n")

    try:
        while True:
            headers = sign("GET", "/trade-api/ws/v2")
            try:
                async with websockets.connect(WS_URL, additional_headers=headers, ssl=_SSL_CTX) as ws:
                    state.connected_at = time.time()
                    print(f"  {DIM}{datetime.now(timezone.utc).strftime('%H:%M:%S')}{RESET}  {GREEN}connected{RESET}")

                    sub = {
                        "id": 1,
                        "cmd": "subscribe",
                        "params": {"channels": channels, "market_tickers": tickers},
                    }
                    await ws.send(json.dumps(sub))

                    async for raw in ws:
                        msg = json.loads(raw)
                        state.msg_count += 1

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
                            state.tickers_data[ticker] = payload

                        # Update Market model for this ticker
                        if ticker:
                            market = update_market_from_ws(state, ticker)

                            if market and is_paper:
                                fills = execution.check_fills(market)
                                if fills:
                                    await handle_fills(fills, state, execution, position_tracker)

                            if market:
                                await maybe_generate_signal(
                                    market, state, strategy, risk_manager, execution, position_tracker,
                                )

                        # Periodic exit checks (strategy timeouts + 10s stop loss)
                        now = time.time()
                        if is_paper and now - last_exit_check >= exit_check_interval:
                            await check_strategy_exits(state, strategy, execution, position_tracker)
                            last_exit_check = now
                        if is_paper and now - last_sl_check >= 1.0:
                            await check_stop_loss(state, execution, position_tracker)
                            last_sl_check = now

                        # Periodic bid/ask log
                        now = time.time()
                        if now - last_quote_log >= quote_interval:
                            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                            parts = []
                            for t in sorted(state.market_info.keys()):
                                tick = state.tickers_data.get(t, {})
                                bid = tick.get("yes_bid")
                                ask = tick.get("yes_ask")
                                bid_s = f"{bid}c" if bid is not None else "-"
                                ask_s = f"{ask}c" if ask is not None else "-"
                                parts.append(f"{ticker_short(t)} {GREEN}{bid_s}{RESET}/{RED}{ask_s}{RESET}")
                            if parts:
                                print(f"  {DIM}{now_str}{RESET}  {' | '.join(parts)}")
                            last_quote_log = now

            except websockets.ConnectionClosed:
                print(f"  {RED}Disconnected. Reconnecting in 2s...{RESET}")
                await asyncio.sleep(2)
            except KeyboardInterrupt:
                raise
            except Exception as e:
                print(f"  {RED}Error: {e}. Reconnecting in 5s...{RESET}")
                await asyncio.sleep(5)

    except (KeyboardInterrupt, asyncio.CancelledError):
        print(f"\n  {DIM}Stopping...{RESET}\n")
        print_session_summary(state, position_tracker, execution, started_at)


# ── CLI ───────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Live test runner for the Kalshi HFT bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 testing/live_test.py KXNCAAMBGAME-26JAN29SFPACHS-SFPA
  python3 testing/live_test.py --event KXNCAAMBGAME
  python3 testing/live_test.py TICKER --live
  python3 testing/live_test.py TICKER --balance 500 --threshold 0.70
        """,
    )
    parser.add_argument(
        "tickers", nargs="*", help="Market ticker(s), event ticker(s), or Kalshi URLs"
    )
    parser.add_argument("--event", help="Expand an event ticker into sub-markets")
    parser.add_argument(
        "--mode", choices=["paper", "live"], default="paper",
        help="Trading mode (default: paper)",
    )
    parser.add_argument("--live", action="store_true", help="Shorthand for --mode live")
    parser.add_argument(
        "--balance", type=float, default=1000,
        help="Starting balance for paper mode (default: 1000)",
    )
    parser.add_argument("--threshold", type=float, help="Entry threshold override")
    parser.add_argument("--take-profit", type=float, dest="take_profit", help="Take profit %% override")
    parser.add_argument("--stop-loss", type=float, dest="stop_loss", help="Stop loss %% override")
    parser.add_argument("--max-hold", type=int, dest="max_hold", help="Max hold hours override")

    args = parser.parse_args()

    if args.live:
        args.mode = "live"

    # Collect tickers
    raw_ticker_args = list(args.tickers or [])
    if args.event:
        raw_ticker_args = ["--event", args.event] + raw_ticker_args

    if not raw_ticker_args:
        parser.print_help()
        sys.exit(1)

    tickers = resolve_tickers(raw_ticker_args)
    if not tickers:
        print(f"  {RED}No valid tickers found{RESET}")
        sys.exit(1)

    print(f"\n  {GREEN}Connecting to {len(tickers)} market(s)...{RESET}")

    # Live mode safety gate
    if args.mode == "live":
        print(f"\n  {RED}{BOLD}WARNING: LIVE MODE — real orders will be placed!{RESET}")
        print(f"  {RED}Tickers: {', '.join(tickers)}{RESET}")
        confirm = input(f"\n  {RED}Type 'yes' to confirm: {RESET}")
        if confirm.strip().lower() != "yes":
            print(f"  {YELLOW}Aborted.{RESET}")
            sys.exit(0)

    print(f"  {DIM}Mode: {args.mode.upper()}  Balance: ${args.balance:.2f}{RESET}\n")

    try:
        asyncio.run(run(tickers, args))
    except KeyboardInterrupt:
        pass  # summary already printed inside run()


if __name__ == "__main__":
    main()
