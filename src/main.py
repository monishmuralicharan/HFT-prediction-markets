"""Main entry point for the Kalshi HFT bot"""

import asyncio
import signal
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from src.api.auth import KalshiAuth
from src.api.kalshi import KalshiClient
from src.config import get_config
from src.db.models import Account
from src.db.repository import SnapshotRepository, TradeRepository
from src.db.supabase_client import SupabaseClient
from src.execution.engine import ExecutionEngine
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.market.filters import MarketFilter
from src.market.monitor import MarketMonitor
from src.risk.circuit_breakers import CircuitBreakerType
from src.risk.manager import RiskManager
from src.strategy.engine import StrategyEngine
from src.utils.email_alerts import EmailAlerter
from src.utils.health import HealthCheckServer
from src.utils.logging import get_logger, setup_logging


class HFTBot:
    """Main HFT bot application"""

    def __init__(self):
        self.config = get_config()
        self.logger = get_logger(__name__)
        self.running = False
        self.shutdown_event = asyncio.Event()

        # Components (initialized in start())
        self.supabase: Optional[SupabaseClient] = None
        self.trade_repo: Optional[TradeRepository] = None
        self.snapshot_repo: Optional[SnapshotRepository] = None
        self.api_client: Optional[KalshiClient] = None
        self.market_monitor: Optional[MarketMonitor] = None
        self.strategy_engine: Optional[StrategyEngine] = None
        self.risk_manager: Optional[RiskManager] = None
        self.order_manager: Optional[OrderManager] = None
        self.position_tracker: Optional[PositionTracker] = None
        self.execution_engine: Optional[ExecutionEngine] = None
        self.email_alerter: Optional[EmailAlerter] = None
        self.health_server: Optional[HealthCheckServer] = None

        # Account state
        self.account: Optional[Account] = None

        # Background tasks
        self.monitor_task: Optional[asyncio.Task] = None
        self.risk_check_task: Optional[asyncio.Task] = None
        self.snapshot_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the bot"""
        self.logger.info(
            "Starting Kalshi HFT Bot",
            version="0.2.0",
            environment=self.config.secrets.environment,
        )

        self.running = True

        # Register signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(self.shutdown()))

        try:
            # Initialize components
            await self._initialize_components()

            self.logger.info("Bot started successfully")

            # Start background tasks
            self.monitor_task = asyncio.create_task(self._monitor_positions())
            self.risk_check_task = asyncio.create_task(self._check_risk_loop())
            self.snapshot_task = asyncio.create_task(self._snapshot_loop())

            # Wait for shutdown signal
            await self.shutdown_event.wait()

        except Exception as e:
            self.logger.error("Fatal error in main loop", error=str(e), exc_info=True)
            raise
        finally:
            await self.cleanup()

    async def _initialize_components(self):
        """Initialize all components"""
        # Database
        self.supabase = SupabaseClient(
            url=self.config.secrets.supabase_url,
            key=self.config.secrets.supabase_key,
        )
        self.trade_repo = TradeRepository(self.supabase)
        self.snapshot_repo = SnapshotRepository(self.supabase)

        # Kalshi authentication
        auth = KalshiAuth(
            key_id=self.config.secrets.kalshi_api_key_id,
            private_key=self.config.secrets.kalshi_private_key,
        )

        # Kalshi API client with dual rate limiters
        self.api_client = KalshiClient(
            base_url=self.config.api.get_api_base_url(),
            auth=auth,
            read_rate_limit=self.config.api.read_rate_limit_per_second,
            write_rate_limit=self.config.api.write_rate_limit_per_second,
            timeout=self.config.api.request_timeout,
            max_retries=self.config.api.max_retries,
            retry_backoff=self.config.api.retry_backoff_base,
        )

        # Initialize account state
        try:
            balance_response = await self.api_client.get_balance()
            starting_balance = balance_response.total
        except Exception as e:
            self.logger.warning(
                "Failed to fetch balance from API, using default",
                error=str(e),
            )
            starting_balance = Decimal("1000")  # Default for testing

        self.account = Account(
            address=self.config.secrets.kalshi_api_key_id,
            total_balance=starting_balance,
            available_balance=starting_balance,
            starting_balance=starting_balance,
            daily_starting_balance=starting_balance,
        )

        # Order and position tracking
        self.order_manager = OrderManager()
        self.position_tracker = PositionTracker()

        # Execution engine
        self.execution_engine = ExecutionEngine(
            api_client=self.api_client,
            order_manager=self.order_manager,
            position_tracker=self.position_tracker,
        )

        # Email alerter
        self.email_alerter = EmailAlerter(
            smtp_host=self.config.secrets.smtp_host,
            smtp_port=self.config.secrets.smtp_port,
            smtp_user=self.config.secrets.smtp_user,
            smtp_password=self.config.secrets.smtp_password,
            alert_email=self.config.secrets.alert_email,
            enabled=self.config.email.enabled,
            rate_limit_minutes=self.config.email.rate_limit_minutes,
        )

        # Risk manager
        self.risk_manager = RiskManager(
            max_position_size_pct=self.config.risk.max_position_size_pct,
            max_total_exposure_pct=self.config.risk.max_total_exposure_pct,
            max_concurrent_positions=self.config.positions.max_concurrent,
            max_daily_loss_pct=self.config.risk.max_daily_loss_pct,
            max_consecutive_losses=self.config.risk.max_consecutive_losses,
            api_error_threshold=self.config.risk.api_error_threshold,
            max_disconnect_seconds=self.config.risk.max_disconnect_seconds,
            on_circuit_breaker=self._on_circuit_breaker,
        )

        # Strategy engine
        self.strategy_engine = StrategyEngine(
            entry_threshold=self.config.strategy.entry_threshold,
            take_profit_pct=self.config.strategy.take_profit_pct,
            stop_loss_pct=self.config.strategy.stop_loss_pct,
            max_hold_time_hours=self.config.strategy.max_hold_time_hours,
            max_position_size_pct=self.config.risk.max_position_size_pct,
            min_position_size=Decimal(str(self.config.positions.min_position_size)),
            max_position_size=Decimal(str(self.config.positions.max_position_size)),
        )

        # Market filter
        market_filter = MarketFilter(
            min_probability=self.config.strategy.entry_threshold,
            min_liquidity=Decimal(str(self.config.strategy.min_liquidity)),
            min_volume=Decimal(str(self.config.strategy.min_volume)),
            max_spread_pct=self.config.strategy.max_spread_pct,
            take_profit_pct=self.config.strategy.take_profit_pct,
        )

        # Market monitor with Kalshi auth
        self.market_monitor = MarketMonitor(
            websocket_url=self.config.api.get_ws_url(),
            market_filter=market_filter,
            auth=auth,
            on_opportunity=self._on_market_opportunity,
        )

        # Load initial markets via REST before starting WebSocket
        await self.market_monitor.load_initial_markets(self.api_client)

        # Health check server
        self.health_server = HealthCheckServer(
            port=self.config.monitoring.health_check_port,
            get_status=self._get_system_status,
        )
        await self.health_server.start()

        # Start market monitor
        asyncio.create_task(self.market_monitor.start())

        self.logger.info("All components initialized")

    def _on_market_opportunity(self, market):
        """Handle market opportunity (callback from market monitor)"""
        asyncio.create_task(self._process_opportunity(market))

    async def _process_opportunity(self, market):
        """Process a market opportunity"""
        try:
            # Generate signal
            signal = self.strategy_engine.evaluate_market(market, self.account)
            if not signal:
                return

            # Validate signal with risk manager
            is_valid, error = self.risk_manager.validate_signal(
                signal,
                self.account,
                self.position_tracker.get_open_count(),
            )

            if not is_valid:
                self.logger.info(
                    "Signal rejected by risk manager",
                    market=market.question,
                    reason=error,
                )
                return

            # Execute signal
            position = await self.execution_engine.execute_signal(signal)

            if position:
                # Lock funds
                self.account.lock_funds(position.position_size)

                # Save to database
                self.trade_repo.save(position)

                # Send email alert
                await self.email_alerter.send_position_opened_alert(position)

        except Exception as e:
            self.logger.error(
                "Failed to process opportunity",
                error=str(e),
                exc_info=True,
            )

    async def _monitor_positions(self):
        """Monitor open positions for exit conditions"""
        while self.running:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds

                for position in self.position_tracker.get_open_positions():
                    # Get current market price
                    market = self.market_monitor.get_market(position.market_id)
                    if not market or not market.last_price:
                        continue

                    # Check if position should be exited
                    should_exit, exit_reason = self.strategy_engine.check_exit(
                        position=position,
                        current_price=market.last_price,
                        market_closing=not market.active,
                    )

                    if should_exit:
                        # Calculate exit price
                        exit_price = self.strategy_engine.calculate_exit_price(
                            position=position,
                            current_price=market.last_price,
                            exit_reason=exit_reason,
                        )

                        # Close position
                        success = await self.execution_engine.close_position(
                            position=position,
                            exit_price=exit_price,
                            exit_reason=exit_reason,
                        )

                        if success:
                            # Update account
                            self.account.unlock_funds(position.position_size)
                            if position.realized_pnl:
                                self.account.record_trade(position.realized_pnl)

                            # Update in database
                            self.trade_repo.update(position.id, position)

                            # Send email alert
                            await self.email_alerter.send_position_closed_alert(position)

            except Exception as e:
                self.logger.error(
                    "Error in position monitoring",
                    error=str(e),
                    exc_info=True,
                )

    async def _check_risk_loop(self):
        """Periodically check risk conditions"""
        while self.running:
            try:
                await asyncio.sleep(10)  # Check every 10 seconds

                # Check circuit breakers
                self.risk_manager.check_circuit_breakers(
                    account=self.account,
                    api_error_rate=self.api_client.get_error_rate(),
                    websocket_disconnect_seconds=self.market_monitor.disconnect_duration(),
                )

                # If circuit breaker active, initiate shutdown
                if self.risk_manager.should_shutdown():
                    self.logger.critical("Circuit breaker requires shutdown")
                    await self.shutdown()

            except Exception as e:
                self.logger.error(
                    "Error in risk check loop",
                    error=str(e),
                    exc_info=True,
                )

    async def _snapshot_loop(self):
        """Periodically save account snapshots"""
        while self.running:
            try:
                await asyncio.sleep(300)  # Every 5 minutes

                snapshot = self.account.to_snapshot(
                    open_positions=self.position_tracker.get_open_count()
                )
                self.snapshot_repo.save(snapshot)

            except Exception as e:
                self.logger.error(
                    "Error in snapshot loop",
                    error=str(e),
                    exc_info=True,
                )

    def _on_circuit_breaker(self, reason: CircuitBreakerType):
        """Handle circuit breaker trigger"""
        asyncio.create_task(
            self.email_alerter.send_circuit_breaker_alert(reason, self.account)
        )

    def _get_system_status(self) -> dict:
        """Get current system status for health check"""
        return {
            "running": self.running,
            "circuit_breaker_active": self.risk_manager.is_circuit_breaker_active()
            if self.risk_manager
            else False,
            "open_positions": self.position_tracker.get_open_count()
            if self.position_tracker
            else 0,
            "account_balance": float(self.account.total_balance) if self.account else 0,
            "daily_pnl": float(self.account.daily_pnl) if self.account else 0,
            "websocket_connected": self.market_monitor.ws_client.connected
            if self.market_monitor
            else False,
        }

    async def shutdown(self):
        """Graceful shutdown"""
        if not self.running:
            return

        self.logger.info("Initiating graceful shutdown...")
        self.running = False

        # Cancel background tasks and await them
        tasks_to_cancel = []
        if self.monitor_task:
            self.monitor_task.cancel()
            tasks_to_cancel.append(self.monitor_task)
        if self.risk_check_task:
            self.risk_check_task.cancel()
            tasks_to_cancel.append(self.risk_check_task)
        if self.snapshot_task:
            self.snapshot_task.cancel()
            tasks_to_cancel.append(self.snapshot_task)

        if tasks_to_cancel:
            await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

        # Cancel all pending orders
        if self.order_manager:
            for order in self.order_manager.get_active_orders():
                if order.exchange_order_id:
                    try:
                        await self.api_client.cancel_order(order.exchange_order_id)
                    except Exception as e:
                        self.logger.error("Failed to cancel order", order_id=order.id, error=str(e))

        self.shutdown_event.set()

    async def cleanup(self):
        """Cleanup resources"""
        self.logger.info("Cleaning up resources...")

        # Stop market monitor
        if self.market_monitor:
            await self.market_monitor.stop()

        # Close API client
        if self.api_client:
            await self.api_client.close()

        # Stop health server
        if self.health_server:
            await self.health_server.stop()

        # Close database
        if self.supabase:
            self.supabase.close()

        self.logger.info("Cleanup complete")


async def main():
    """Main entry point"""
    # Load configuration
    try:
        config = get_config()
    except Exception as e:
        print(f"Failed to load configuration: {e}", file=sys.stderr)
        sys.exit(1)

    # Setup logging
    try:
        setup_logging(
            level=config.logging.level,
            log_format=config.logging.format,
            log_to_console=config.logging.log_to_console,
            supabase_client=None,  # Initialized later
        )
    except Exception as e:
        print(f"Failed to setup logging: {e}", file=sys.stderr)
        sys.exit(1)

    # Create and start bot
    bot = HFTBot()

    try:
        await bot.start()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger = get_logger(__name__)
        logger.error("Fatal error", error=str(e), exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
