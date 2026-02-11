"""Market monitoring and opportunity detection for Kalshi"""
from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from decimal import Decimal
from typing import Any, Callable, Optional

from src.api.auth import KalshiAuth
from src.api.kalshi import KalshiClient
from src.db.models import Market
from src.market.filters import MarketFilter
from src.market.websocket import WebSocketClient
from src.utils.logging import get_logger

CENTS = Decimal("100")


class OrderbookState:
    """Maintains a local orderbook from snapshots + deltas (in cents)."""

    def __init__(self):
        # {ticker: {"yes": {price_cents: size, ...}, "no": {price_cents: size, ...}}}
        self.books: dict[str, dict[str, dict[int, int]]] = defaultdict(
            lambda: {"yes": {}, "no": {}}
        )
        self.last_update: dict[str, float] = {}

    def apply_snapshot(self, ticker: str, data: dict[str, Any]) -> None:
        """Apply a full orderbook snapshot: {yes: [[price, size], ...], no: [...]}."""
        book: dict[str, dict[int, int]] = {"yes": {}, "no": {}}
        for price, size in data.get("yes") or []:
            book["yes"][price] = size
        for price, size in data.get("no") or []:
            book["no"][price] = size
        self.books[ticker] = book
        self.last_update[ticker] = time.time()

    def apply_delta(self, ticker: str, data: dict[str, Any]) -> None:
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

    def get_best_bid_ask(self, ticker: str) -> tuple[Optional[int], Optional[int], int]:
        """
        Derive best yes-bid, best yes-ask (implied from no side), and
        liquidity at best bid (contract count) — all in cents.

        Returns:
            (best_yes_bid_cents, best_yes_ask_cents, liquidity_at_best_bid)
        """
        book = self.books.get(ticker)
        if not book:
            return None, None, 0

        # Best yes bid = highest price on the yes side
        yes_levels = [(p, s) for p, s in book["yes"].items() if s > 0]
        best_bid = None
        bid_liquidity = 0
        if yes_levels:
            best_bid_level = max(yes_levels, key=lambda x: x[0])
            best_bid = best_bid_level[0]
            bid_liquidity = best_bid_level[1]

        # Best yes ask = implied from no side: 100 - highest_no_price
        no_levels = [(p, s) for p, s in book["no"].items() if s > 0]
        best_ask = None
        if no_levels:
            highest_no = max(no_levels, key=lambda x: x[0])
            best_ask = 100 - highest_no[0]

        return best_bid, best_ask, bid_liquidity


class MarketMonitor:
    """Monitor markets via Kalshi WebSocket and detect opportunities"""

    def __init__(
        self,
        websocket_url: str,
        market_filter: MarketFilter,
        auth: KalshiAuth,
        on_opportunity: Optional[Callable[[Market], None]] = None,
    ):
        """
        Initialize market monitor

        Args:
            websocket_url: WebSocket URL
            market_filter: Market filter instance
            auth: KalshiAuth instance for WebSocket authentication
            on_opportunity: Callback for market opportunities
        """
        self.websocket_url = websocket_url
        self.market_filter = market_filter
        self.auth = auth
        self.on_opportunity = on_opportunity
        self.logger = get_logger(__name__)

        # WebSocket client with auth headers
        self.ws_client = WebSocketClient(
            url=websocket_url,
            extra_headers=lambda: auth.get_ws_auth_headers(),
        )
        self.ws_client.on_message = self._handle_message
        self.ws_client.on_connect = self._on_connect
        self.ws_client.on_disconnect = self._on_disconnect

        # Market state tracking
        self.markets: dict[str, Market] = {}

        # Local orderbook state (cents)
        self.orderbook = OrderbookState()

        # Tracked tickers for subscriptions
        self._tracked_tickers: list[str] = []

        # Running state
        self.running = False

    async def load_initial_markets(self, api_client: KalshiClient) -> None:
        """
        Load initial markets via REST API before starting WebSocket.
        Populates market state and discovers tickers for subscription.

        Args:
            api_client: KalshiClient instance
        """
        self.logger.info("Loading initial markets from REST API")
        try:
            market_list = await api_client.get_markets(active=True)

            for market_data in market_list:
                try:
                    market = Market(
                        id=market_data.id,
                        question=market_data.question,
                        outcomes=market_data.outcomes,
                        end_date=market_data.end_date_iso,
                        active=market_data.active,
                        volume_24h=market_data.volume,
                        liquidity=market_data.liquidity,
                        event_ticker=market_data.event_ticker,
                        series_ticker=market_data.series_ticker,
                    )
                    self.markets[market.id] = market
                    self._tracked_tickers.append(market.id)
                except Exception as e:
                    self.logger.warning(
                        "Failed to parse market",
                        error=str(e),
                        market_id=market_data.id,
                    )

            self.logger.info(
                "Loaded initial markets",
                count=len(self._tracked_tickers),
            )
        except Exception as e:
            self.logger.error(
                "Failed to load initial markets",
                error=str(e),
                exc_info=True,
            )

    async def start(self) -> None:
        """Start the market monitor"""
        self.logger.info("Starting market monitor")
        self.running = True

        # Connect to WebSocket
        await self.ws_client.connect()

    async def stop(self) -> None:
        """Stop the market monitor"""
        self.logger.info("Stopping market monitor")
        self.running = False

        # Close WebSocket
        await self.ws_client.close()

    def _on_connect(self) -> None:
        """Handle WebSocket connection"""
        self.logger.info("Market monitor connected")

        # Subscribe to relevant Kalshi channels
        asyncio.create_task(self._subscribe_channels())

    def _on_disconnect(self) -> None:
        """Handle WebSocket disconnection"""
        self.logger.warning("Market monitor disconnected")

    async def _subscribe_channels(self) -> None:
        """Subscribe to Kalshi WebSocket channels"""
        if self._tracked_tickers:
            # Subscribe to market data channels with tracked tickers
            await self.ws_client.subscribe(
                channels=["orderbook_delta", "ticker", "trade"],
                market_tickers=self._tracked_tickers,
            )

        # Subscribe to user-specific channels (no tickers needed)
        await self.ws_client.subscribe(
            channels=["fill", "order_update"],
        )

        self.logger.info("Subscribed to Kalshi channels")

    def _handle_message(self, data: dict[str, Any]) -> None:
        """
        Handle WebSocket message.

        Kalshi WS messages use the ``type`` field as the message discriminator
        (e.g. ``orderbook_snapshot``, ``orderbook_delta``, ``ticker``, ``trade``).
        We fall back to ``channel`` for forward-compatibility.
        """
        try:
            msg_type = data.get("type", data.get("channel", ""))

            # Skip ack / subscription confirmations
            if "id" in data and "result" in data:
                return

            if msg_type == "ticker":
                self._handle_ticker_update(data)
            elif msg_type == "orderbook_snapshot":
                self._handle_orderbook_snapshot(data)
            elif msg_type == "orderbook_delta":
                self._handle_orderbook_delta(data)
            elif msg_type == "trade":
                self._handle_trade(data)
            elif msg_type == "fill":
                self._handle_fill(data)
            elif msg_type in ("order_update", "user_order"):
                self._handle_user_order(data)
            elif msg_type == "subscribed":
                self.logger.debug("Subscription confirmed")
            elif msg_type == "error":
                self.logger.warning("WebSocket error message", data=data)
            else:
                self.logger.debug("Unhandled message", type=msg_type)

        except Exception as e:
            self.logger.error(
                "Error handling WebSocket message",
                error=str(e),
                exc_info=True,
            )

    def _handle_ticker_update(self, data: dict[str, Any]) -> None:
        """Handle ticker update — parse yes_price/yes_bid/yes_ask (cents → dollars)"""
        msg = data.get("msg", {})
        ticker = msg.get("market_ticker")

        if not ticker:
            return

        try:
            if ticker not in self.markets:
                return

            market = self.markets[ticker]

            # Convert cents to dollars
            yes_price = msg.get("yes_price")
            yes_bid = msg.get("yes_bid")
            yes_ask = msg.get("yes_ask")

            if yes_price is not None:
                market.last_price = Decimal(str(yes_price)) / Decimal("100")
            if yes_bid is not None:
                market.best_bid = Decimal(str(yes_bid)) / Decimal("100")
            if yes_ask is not None:
                market.best_ask = Decimal(str(yes_ask)) / Decimal("100")

            volume = msg.get("volume")
            if volume is not None:
                market.volume_24h = Decimal(str(volume))

            # Recalculate derived fields
            market.calculate_spread()
            market.calculate_probability()

            # Check for opportunity
            self._check_opportunity(market)

        except Exception as e:
            self.logger.warning(
                "Failed to parse ticker update",
                error=str(e),
                ticker=ticker,
            )

    def _handle_orderbook_snapshot(self, data: dict[str, Any]) -> None:
        """Handle full orderbook snapshot (sent once on subscribe)."""
        msg = data.get("msg", data)
        ticker = msg.get("market_ticker")

        if not ticker:
            return

        try:
            self.orderbook.apply_snapshot(ticker, msg)
            self._sync_book_to_market(ticker)
        except Exception as e:
            self.logger.warning(
                "Failed to apply orderbook snapshot",
                error=str(e),
                ticker=ticker,
            )

    def _handle_orderbook_delta(self, data: dict[str, Any]) -> None:
        """Handle incremental orderbook delta: {price, delta, side}."""
        msg = data.get("msg", data)
        ticker = msg.get("market_ticker")

        if not ticker:
            return

        try:
            self.orderbook.apply_delta(ticker, msg)
            self._sync_book_to_market(ticker)
        except Exception as e:
            self.logger.warning(
                "Failed to apply orderbook delta",
                error=str(e),
                ticker=ticker,
            )

    def _sync_book_to_market(self, ticker: str) -> None:
        """Derive best bid/ask from local orderbook and update the Market model."""
        if ticker not in self.markets:
            return

        market = self.markets[ticker]
        best_bid, best_ask, bid_liquidity = self.orderbook.get_best_bid_ask(ticker)

        if best_bid is not None:
            market.best_bid = Decimal(str(best_bid)) / CENTS
        if best_ask is not None:
            market.best_ask = Decimal(str(best_ask)) / CENTS
        if bid_liquidity:
            market.liquidity = Decimal(str(bid_liquidity))

        market.calculate_spread()
        market.calculate_probability()
        self._check_opportunity(market)

    def _handle_trade(self, data: dict[str, Any]) -> None:
        """Handle trade message (for volume tracking)"""
        msg = data.get("msg", {})
        ticker = msg.get("market_ticker")
        if not ticker or ticker not in self.markets:
            return

        self.logger.debug("Trade executed", ticker=ticker)

    def _handle_fill(self, data: dict[str, Any]) -> None:
        """Handle fill notification for own orders"""
        msg = data.get("msg", {})
        self.logger.info(
            "Order fill received",
            order_id=msg.get("order_id"),
            ticker=msg.get("market_ticker"),
            count=msg.get("count"),
            yes_price=msg.get("yes_price"),
        )

    def _handle_user_order(self, data: dict[str, Any]) -> None:
        """Handle order status change for own orders"""
        msg = data.get("msg", {})
        self.logger.info(
            "Order status update",
            order_id=msg.get("order_id"),
            status=msg.get("status"),
            ticker=msg.get("market_ticker"),
        )

    def _check_opportunity(self, market: Market) -> None:
        """
        Check if market is an opportunity

        Args:
            market: Market to check
        """
        passes, reason = self.market_filter.filter(market)

        if passes:
            score = self.market_filter.calculate_opportunity_score(market)

            self.logger.info(
                "Market opportunity detected",
                market=market.question,
                probability=float(market.probability or 0),
                liquidity=float(market.liquidity),
                spread=float(market.spread or 0),
                score=float(score or 0),
            )

            if self.on_opportunity:
                self.on_opportunity(market)
        else:
            self.logger.debug(
                "Market filtered out",
                market=market.question,
                reason=reason,
            )

    def get_market(self, market_id: str) -> Optional[Market]:
        """Get a market by ID"""
        return self.markets.get(market_id)

    def get_all_markets(self) -> list[Market]:
        """Get all tracked markets"""
        return list(self.markets.values())

    def get_active_markets(self) -> list[Market]:
        """Get all active markets"""
        return [m for m in self.markets.values() if m.active]

    def disconnect_duration(self) -> float:
        """Get WebSocket disconnect duration in seconds"""
        return self.ws_client.disconnect_duration()
