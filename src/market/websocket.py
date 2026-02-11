"""Kalshi WebSocket client with auth headers and auto-reconnect"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Optional

import websockets
from websockets.client import WebSocketClientProtocol

from src.utils.logging import get_logger


class WebSocketClient:
    """WebSocket client with auth headers, auto-reconnect, and heartbeat"""

    def __init__(
        self,
        url: str,
        extra_headers: Optional[Callable[[], dict[str, str]]] = None,
        reconnect_delay: int = 1,
        max_reconnect_delay: int = 30,
        ping_interval: int = 30,
        ping_timeout: int = 10,
    ):
        """
        Initialize WebSocket client

        Args:
            url: WebSocket URL
            extra_headers: Callable that returns auth headers (called fresh on each connect)
            reconnect_delay: Initial reconnect delay (seconds)
            max_reconnect_delay: Maximum reconnect delay (seconds)
            ping_interval: Ping interval (seconds)
            ping_timeout: Ping timeout (seconds)
        """
        self.url = url
        self.extra_headers = extra_headers
        self.reconnect_delay = reconnect_delay
        self.max_reconnect_delay = max_reconnect_delay
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        self.logger = get_logger(__name__)

        # Connection state
        self.ws: Optional[WebSocketClientProtocol] = None
        self.connected = False
        self.running = False

        # Callbacks
        self.on_message: Optional[Callable[[dict[str, Any]], None]] = None
        self.on_connect: Optional[Callable[[], None]] = None
        self.on_disconnect: Optional[Callable[[], None]] = None

        # Reconnect tracking
        self.current_delay = reconnect_delay
        self.disconnect_time: Optional[float] = None

        # Message ID counter for Kalshi cmd-based protocol
        self._msg_id_counter = 0

        # Background tasks
        self._receive_task: Optional[asyncio.Task] = None
        self._ping_task: Optional[asyncio.Task] = None

    def _next_msg_id(self) -> int:
        """Get next message ID for Kalshi protocol"""
        self._msg_id_counter += 1
        return self._msg_id_counter

    async def connect(self) -> None:
        """Connect to WebSocket with auth headers"""
        self.running = True

        while self.running:
            try:
                self.logger.info("Connecting to WebSocket", url=self.url)

                # Generate fresh auth headers on each connect attempt
                headers = {}
                if self.extra_headers:
                    headers = self.extra_headers()

                self.ws = await websockets.connect(
                    self.url,
                    additional_headers=headers,
                    ping_interval=None,  # We'll handle pings ourselves
                    ping_timeout=None,
                )

                self.connected = True
                self.current_delay = self.reconnect_delay
                self.disconnect_time = None

                self.logger.info("Connected to WebSocket")

                # Call on_connect callback
                if self.on_connect:
                    self.on_connect()

                # Start background tasks
                self._receive_task = asyncio.create_task(self._receive_loop())
                self._ping_task = asyncio.create_task(self._ping_loop())

                # Wait for disconnect
                await self._receive_task

            except Exception as e:
                self.logger.error(
                    "WebSocket connection error",
                    error=str(e),
                    exc_info=True,
                )

            finally:
                self.connected = False
                self.disconnect_time = time.time()

                # Cancel background tasks
                if self._receive_task:
                    self._receive_task.cancel()
                if self._ping_task:
                    self._ping_task.cancel()

                # Call on_disconnect callback
                if self.on_disconnect:
                    self.on_disconnect()

                # Close WebSocket
                if self.ws:
                    await self.ws.close()
                    self.ws = None

                # Reconnect with exponential backoff
                if self.running:
                    self.logger.info(
                        "Reconnecting to WebSocket",
                        delay=self.current_delay,
                    )
                    await asyncio.sleep(self.current_delay)
                    self.current_delay = min(
                        self.current_delay * 2,
                        self.max_reconnect_delay,
                    )

    async def _receive_loop(self) -> None:
        """Receive messages from WebSocket"""
        try:
            async for message in self.ws:
                try:
                    data = json.loads(message)

                    if self.on_message:
                        self.on_message(data)

                except json.JSONDecodeError as e:
                    self.logger.warning(
                        "Failed to parse WebSocket message",
                        error=str(e),
                        message=message,
                    )
                except Exception as e:
                    self.logger.error(
                        "Error processing WebSocket message",
                        error=str(e),
                        exc_info=True,
                    )

        except websockets.exceptions.ConnectionClosed:
            self.logger.warning("WebSocket connection closed")
        except Exception as e:
            self.logger.error(
                "Error in receive loop",
                error=str(e),
                exc_info=True,
            )

    async def _ping_loop(self) -> None:
        """Send periodic pings to keep connection alive"""
        while self.connected:
            try:
                await asyncio.sleep(self.ping_interval)

                if self.ws and self.connected:
                    pong = await self.ws.ping()
                    await asyncio.wait_for(pong, timeout=self.ping_timeout)
                    self.logger.debug("WebSocket ping/pong successful")

            except asyncio.TimeoutError:
                self.logger.warning("WebSocket ping timeout, retrying")
                continue
            except Exception as e:
                self.logger.error(
                    "Error in ping loop",
                    error=str(e),
                    exc_info=True,
                )
                break

    async def send(self, data: dict[str, Any]) -> None:
        """
        Send message to WebSocket

        Args:
            data: Message data to send
        """
        if not self.ws or not self.connected:
            self.logger.warning("Cannot send message - not connected")
            return

        try:
            message = json.dumps(data)
            await self.ws.send(message)
            self.logger.debug("Sent WebSocket message", cmd=data.get("cmd"))
        except Exception as e:
            self.logger.error(
                "Failed to send WebSocket message",
                error=str(e),
                exc_info=True,
            )

    async def subscribe(
        self,
        channels: list[str],
        market_tickers: Optional[list[str]] = None,
    ) -> None:
        """
        Subscribe to Kalshi WebSocket channels

        Args:
            channels: List of channel names (e.g., ["orderbook_delta", "ticker"])
            market_tickers: List of market tickers to subscribe to
        """
        params: dict[str, Any] = {"channels": channels}
        if market_tickers:
            params["market_tickers"] = market_tickers

        message = {
            "id": self._next_msg_id(),
            "cmd": "subscribe",
            "params": params,
        }
        await self.send(message)
        self.logger.info(
            "Subscribed to channels",
            channels=channels,
            tickers_count=len(market_tickers) if market_tickers else 0,
        )

    async def unsubscribe(
        self,
        channels: list[str],
        market_tickers: Optional[list[str]] = None,
    ) -> None:
        """
        Unsubscribe from Kalshi WebSocket channels

        Args:
            channels: List of channel names
            market_tickers: List of market tickers
        """
        params: dict[str, Any] = {"channels": channels}
        if market_tickers:
            params["market_tickers"] = market_tickers

        message = {
            "id": self._next_msg_id(),
            "cmd": "unsubscribe",
            "params": params,
        }
        await self.send(message)
        self.logger.info("Unsubscribed from channels", channels=channels)

    def disconnect_duration(self) -> float:
        """
        Get duration of current disconnect in seconds

        Returns:
            Seconds disconnected, or 0 if connected
        """
        if self.connected:
            return 0.0

        if self.disconnect_time:
            return time.time() - self.disconnect_time

        return 0.0

    async def close(self) -> None:
        """Close the WebSocket connection"""
        self.logger.info("Closing WebSocket connection")
        self.running = False

        if self.ws and not self.ws.closed:
            await self.ws.close()

        self.connected = False
        self.ws = None
