"""Kalshi REST API client with dual rate limiting and retry logic"""

import asyncio
import time
from decimal import Decimal
from typing import Any, Optional
from urllib.parse import urlparse

import aiohttp

from src.api.auth import KalshiAuth
from src.api.models import (
    BalanceResponse,
    CancelOrderResponse,
    KALSHI_STATUS_MAP,
    MarketData,
    OrderBook,
    OrderBookLevel,
    OrderRequest,
    OrderResponse,
    OrderStatus,
)
from src.utils.logging import get_logger


class RateLimiter:
    """Token bucket rate limiter"""

    def __init__(self, rate: int):
        """
        Args:
            rate: Maximum requests per second
        """
        self.rate = rate
        self.tokens = float(rate)
        self.last_update = time.time()
        self.lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a token is available"""
        async with self.lock:
            now = time.time()
            elapsed = now - self.last_update
            self.last_update = now

            # Refill tokens
            self.tokens = min(self.rate, self.tokens + elapsed * self.rate)

            # Wait if no tokens available
            if self.tokens < 1.0:
                wait_time = (1.0 - self.tokens) / self.rate
                await asyncio.sleep(wait_time)
                self.tokens = 0.0
            else:
                self.tokens -= 1.0


class KalshiClient:
    """Kalshi API client with cents-to-dollars conversion boundary"""

    def __init__(
        self,
        base_url: str,
        auth: KalshiAuth,
        read_rate_limit: int = 20,
        write_rate_limit: int = 10,
        timeout: int = 10,
        max_retries: int = 3,
        retry_backoff: float = 2.0,
    ):
        """
        Initialize Kalshi client

        Args:
            base_url: API base URL (e.g., https://trading-api.kalshi.com/trade-api/v2)
            auth: Authentication handler
            read_rate_limit: Max read requests per second (GET)
            write_rate_limit: Max write requests per second (POST/DELETE)
            timeout: Request timeout in seconds
            max_retries: Maximum retry attempts
            retry_backoff: Backoff multiplier for retries
        """
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.logger = get_logger(__name__)

        # Dual rate limiters
        self.read_limiter = RateLimiter(read_rate_limit)
        self.write_limiter = RateLimiter(write_rate_limit)

        # Session (created on first use)
        self._session: Optional[aiohttp.ClientSession] = None

        # Error tracking
        self.total_requests = 0
        self.failed_requests = 0

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout)
        return self._session

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
        retry_count: int = 0,
    ) -> dict[str, Any]:
        """
        Make HTTP request with auth headers, rate limiting, and retry logic

        Args:
            method: HTTP method
            endpoint: API endpoint (e.g., "/portfolio/balance")
            data: Request body
            params: Query parameters
            retry_count: Current retry attempt

        Returns:
            Response data
        """
        # Select rate limiter based on method
        limiter = self.read_limiter if method.upper() == "GET" else self.write_limiter
        await limiter.acquire()

        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        session = await self._get_session()

        # Generate auth headers using the path portion of the URL
        parsed = urlparse(url)
        auth_headers = self.auth.get_auth_headers(method.upper(), parsed.path)

        self.total_requests += 1

        try:
            async with session.request(
                method,
                url,
                json=data,
                params=params,
                headers=auth_headers,
            ) as response:
                # Handle successful responses (2xx)
                if 200 <= response.status < 300:
                    if response.status == 204 or response.content_length == 0:
                        response_data = {}
                    else:
                        response_data = await response.json()

                    self.logger.debug(
                        "API request successful",
                        method=method,
                        endpoint=endpoint,
                        status=response.status,
                    )
                    return response_data

                # Parse error response body
                try:
                    response_data = await response.json()
                except Exception:
                    response_data = {"error": await response.text()}

                self.logger.warning(
                    "API request failed",
                    method=method,
                    endpoint=endpoint,
                    status=response.status,
                    response=response_data,
                )

                # Retry on server errors (5xx) and rate limits (429)
                if response.status in [429, 500, 502, 503, 504] and retry_count < self.max_retries:
                    wait_time = self.retry_backoff ** retry_count
                    self.logger.info(
                        "Retrying request",
                        wait_seconds=wait_time,
                        retry=retry_count + 1,
                        max_retries=self.max_retries,
                    )
                    await asyncio.sleep(wait_time)
                    return await self._request(method, endpoint, data, params, retry_count + 1)

                self.failed_requests += 1
                raise Exception(f"API error {response.status}: {response_data}")

        except asyncio.TimeoutError:
            self.logger.error("API request timeout", method=method, endpoint=endpoint)
            self.failed_requests += 1

            if retry_count < self.max_retries:
                wait_time = self.retry_backoff ** retry_count
                self.logger.info(
                    "Retrying after timeout",
                    wait_seconds=wait_time,
                    retry=retry_count + 1,
                )
                await asyncio.sleep(wait_time)
                return await self._request(method, endpoint, data, params, retry_count + 1)

            raise Exception("API request timeout after retries")

        except Exception as e:
            if "API error" in str(e) or "timeout after retries" in str(e):
                raise
            self.logger.error(
                "API request exception",
                method=method,
                endpoint=endpoint,
                error=str(e),
                exc_info=True,
            )
            self.failed_requests += 1
            raise

    async def get_markets(
        self,
        active: bool = True,
        closed: bool = False,
    ) -> list[MarketData]:
        """
        Get markets with pagination

        Args:
            active: Include active markets
            closed: Include closed markets

        Returns:
            List of markets
        """
        markets = []
        cursor: Optional[str] = None

        while True:
            params: dict[str, Any] = {"limit": 200}
            if cursor:
                params["cursor"] = cursor
            if active and not closed:
                params["status"] = "open"

            response = await self._request("GET", "/markets", params=params)

            for market_data in response.get("markets", []):
                try:
                    markets.append(
                        MarketData(
                            id=market_data.get("ticker", ""),
                            question=market_data.get("title", ""),
                            outcomes=["Yes", "No"],
                            active=market_data.get("status") == "open",
                            closed=market_data.get("status") == "closed",
                            end_date_iso=market_data.get("close_time", ""),
                            volume=Decimal(str(market_data.get("volume", 0))),
                            liquidity=Decimal(str(market_data.get("liquidity", 0))),
                            event_ticker=market_data.get("event_ticker"),
                            series_ticker=market_data.get("series_ticker"),
                        )
                    )
                except Exception as e:
                    self.logger.warning(
                        "Failed to parse market",
                        error=str(e),
                        data=market_data,
                    )

            cursor = response.get("cursor")
            if not cursor:
                break

        return markets

    async def get_order_book(self, ticker: str) -> OrderBook:
        """
        Get order book for a market, converting cents to dollars

        Args:
            ticker: Market ticker

        Returns:
            Order book data with prices in dollars
        """
        response = await self._request("GET", f"/markets/{ticker}/orderbook")

        # Convert cents to dollars
        bids = [
            OrderBookLevel(
                price=Decimal(str(level[0])) / Decimal("100"),
                size=Decimal(str(level[1])),
            )
            for level in response.get("yes", [])
        ]
        asks = [
            OrderBookLevel(
                price=Decimal("1") - Decimal(str(level[0])) / Decimal("100"),
                size=Decimal(str(level[1])),
            )
            for level in response.get("no", [])
        ]

        return OrderBook(
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
            timestamp=int(time.time() * 1000),
        )

    async def submit_order(self, order: OrderRequest) -> OrderResponse:
        """
        Submit an order, converting dollars to cents for the Kalshi API

        Args:
            order: Order request (prices in dollars, size in dollars)

        Returns:
            Order response
        """
        # Convert dollar price to cents
        yes_price_cents = int(order.price * 100)

        # Calculate contract count: count = dollar_size / dollar_price
        if order.price > 0:
            count = int(order.size / order.price)
        else:
            count = 0

        payload = {
            "ticker": order.market_id,
            "side": "yes" if order.side.upper() == "BUY" else "no",
            "type": "limit",
            "count": count,
            "yes_price": yes_price_cents,
        }

        if order.time_in_force != "GTC":
            payload["time_in_force"] = order.time_in_force

        response = await self._request("POST", "/portfolio/orders", data=payload)

        order_data = response.get("order", response)
        remaining = order_data.get("remaining_count", count)

        return OrderResponse(
            order_id=order_data.get("order_id", ""),
            status=KALSHI_STATUS_MAP.get(order_data.get("status", ""), order_data.get("status", "")),
            market_id=order.market_id,
            side=order.side,
            price=order.price,
            size=order.size,
            filled_size=Decimal(str(count - remaining)) * order.price,
            remaining_size=Decimal(str(remaining)) * order.price,
            created_at=int(time.time() * 1000),
        )

    async def cancel_order(self, order_id: str) -> CancelOrderResponse:
        """
        Cancel an order

        Args:
            order_id: Order ID to cancel

        Returns:
            Cancellation response
        """
        response = await self._request("DELETE", f"/portfolio/orders/{order_id}")
        return CancelOrderResponse(
            order_id=order_id,
            status="CANCELLED",
            cancelled_at=int(time.time() * 1000),
        )

    async def get_order_status(self, order_id: str) -> OrderStatus:
        """
        Get order status

        Args:
            order_id: Order ID

        Returns:
            Order status
        """
        response = await self._request("GET", f"/portfolio/orders/{order_id}")

        order_data = response.get("order", response)
        kalshi_status = order_data.get("status", "")
        mapped_status = KALSHI_STATUS_MAP.get(kalshi_status, kalshi_status.upper())

        # Convert cents to dollars for prices
        yes_price_cents = order_data.get("yes_price", 0)
        price_dollars = Decimal(str(yes_price_cents)) / Decimal("100")

        total_count = order_data.get("count", 0) if order_data.get("count") else 0
        filled_count = (
            total_count - order_data.get("remaining_count", total_count)
        )

        return OrderStatus(
            order_id=order_data.get("order_id", order_id),
            status=mapped_status,
            market_id=order_data.get("ticker", ""),
            side=order_data.get("side", "").upper(),
            price=price_dollars,
            size=Decimal(str(total_count)) * price_dollars,
            filled_size=Decimal(str(filled_count)) * price_dollars,
            avg_fill_price=price_dollars if filled_count > 0 else None,
            created_at=int(time.time() * 1000),
            updated_at=int(time.time() * 1000),
        )

    async def get_active_orders(self, market_id: Optional[str] = None) -> list[OrderStatus]:
        """
        Get active (resting) orders

        Args:
            market_id: Optional market ticker filter

        Returns:
            List of active orders
        """
        params: dict[str, Any] = {"status": "resting"}
        if market_id:
            params["ticker"] = market_id

        response = await self._request("GET", "/portfolio/orders", params=params)

        orders = []
        for order_data in response.get("orders", []):
            try:
                yes_price_cents = order_data.get("yes_price", 0)
                price_dollars = Decimal(str(yes_price_cents)) / Decimal("100")
                total_count = order_data.get("count", 0) if order_data.get("count") else 0
                filled_count = total_count - order_data.get("remaining_count", total_count)

                orders.append(
                    OrderStatus(
                        order_id=order_data.get("order_id", ""),
                        status="OPEN",
                        market_id=order_data.get("ticker", ""),
                        side=order_data.get("side", "").upper(),
                        price=price_dollars,
                        size=Decimal(str(total_count)) * price_dollars,
                        filled_size=Decimal(str(filled_count)) * price_dollars,
                        created_at=int(time.time() * 1000),
                        updated_at=int(time.time() * 1000),
                    )
                )
            except Exception as e:
                self.logger.warning("Failed to parse order", error=str(e), data=order_data)

        return orders

    async def get_balance(self) -> BalanceResponse:
        """
        Get account balance, converting cents to dollars

        Returns:
            Balance response in dollars
        """
        response = await self._request("GET", "/portfolio/balance")

        # Kalshi returns balance in cents
        balance_cents = response.get("balance", 0)
        available_cents = response.get("available_balance", balance_cents)

        total_dollars = Decimal(str(balance_cents)) / Decimal("100")
        available_dollars = Decimal(str(available_cents)) / Decimal("100")
        locked_dollars = total_dollars - available_dollars

        return BalanceResponse(
            total=total_dollars,
            available=available_dollars,
            locked=locked_dollars,
        )

    def get_error_rate(self) -> float:
        """
        Calculate API error rate

        Returns:
            Error rate (0.0 to 1.0)
        """
        if self.total_requests == 0:
            return 0.0
        return self.failed_requests / self.total_requests

    async def close(self) -> None:
        """Close the HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()
        self.logger.info("Closed Kalshi API client")
