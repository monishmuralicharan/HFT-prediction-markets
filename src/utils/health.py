"""Health check HTTP server"""

import asyncio
import json
import time
from aiohttp import web
from typing import Callable, Optional

from src.utils.logging import get_logger


class HealthCheckServer:
    """Simple HTTP server for health checks"""

    def __init__(
        self,
        port: int = 8080,
        get_status: Optional[Callable[[], dict]] = None,
    ):
        """
        Initialize health check server

        Args:
            port: Port to listen on
            get_status: Callback to get system status
        """
        self.port = port
        self.get_status = get_status
        self.logger = get_logger(__name__)

        # Server state
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self.start_time = time.time()

    async def start(self) -> None:
        """Start the health check server"""
        self.app = web.Application()
        self.app.router.add_get("/health", self.health_handler)
        self.app.router.add_get("/status", self.status_handler)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        self.site = web.TCPSite(self.runner, "0.0.0.0", self.port)
        await self.site.start()

        self.logger.info("Health check server started", port=self.port)

    async def stop(self) -> None:
        """Stop the health check server"""
        if self.runner:
            await self.runner.cleanup()

        self.logger.info("Health check server stopped")

    async def health_handler(self, request: web.Request) -> web.Response:
        """
        Handle health check requests

        Returns 200 if system is healthy
        """
        return web.Response(
            text=json.dumps({"status": "healthy", "uptime": self.get_uptime()}),
            content_type="application/json",
        )

    async def status_handler(self, request: web.Request) -> web.Response:
        """
        Handle status requests

        Returns detailed system status
        """
        status = {
            "uptime": self.get_uptime(),
            "timestamp": time.time(),
        }

        # Get system status from callback
        if self.get_status:
            try:
                system_status = self.get_status()
                status.update(system_status)
            except Exception as e:
                self.logger.error(
                    "Failed to get system status",
                    error=str(e),
                )
                status["error"] = str(e)

        return web.Response(
            text=json.dumps(status),
            content_type="application/json",
        )

    def get_uptime(self) -> float:
        """Get system uptime in seconds"""
        return time.time() - self.start_time
