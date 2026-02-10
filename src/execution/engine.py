"""Order execution engine"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import uuid4

from src.api.models import OrderRequest
from src.api.kalshi import KalshiClient
from src.db.models import (
    ExitReason,
    Order,
    OrderSide,
    OrderStatus as OrderStatusEnum,
    OrderType,
    Position,
)
from src.execution.order_manager import OrderManager
from src.execution.position_tracker import PositionTracker
from src.strategy.signals import TradingSignal
from src.utils.logging import get_logger


class ExecutionEngine:
    """Execute orders and manage the three-order system"""

    def __init__(
        self,
        api_client: KalshiClient,
        order_manager: OrderManager,
        position_tracker: PositionTracker,
    ):
        """
        Initialize execution engine

        Args:
            api_client: Kalshi API client
            order_manager: Order manager
            position_tracker: Position tracker
        """
        self.api_client = api_client
        self.order_manager = order_manager
        self.position_tracker = position_tracker
        self.logger = get_logger(__name__)

    async def execute_signal(self, signal: TradingSignal) -> Optional[Position]:
        """
        Execute a trading signal (three-order system)

        Opens position with entry order, stop loss, and take profit

        Args:
            signal: Trading signal to execute

        Returns:
            Position if successful, None otherwise
        """
        self.logger.info(
            "Executing trading signal",
            market=signal.market.question,
            entry_price=float(signal.entry_price),
            size=float(signal.position_size),
        )

        try:
            # Create position
            position = Position(
                market_id=signal.market.id,
                market_question=signal.market.question,
                outcome=signal.market.outcomes[0] if signal.market.outcomes else signal.market.id,
                entry_time=datetime.now(timezone.utc),
                entry_price=signal.entry_price,
                entry_probability=signal.market.probability or signal.entry_price,
                position_size=signal.position_size,
                stop_loss_price=signal.stop_loss_price,
                take_profit_price=signal.take_profit_price,
            )

            # Submit entry order
            entry_order = await self._submit_entry_order(signal, position)
            if not entry_order:
                self.logger.error("Failed to submit entry order")
                return None

            position.entry_order_id = entry_order.id

            # Wait for entry order to fill
            filled = await self._wait_for_fill(entry_order.id, timeout=30)
            if not filled:
                self.logger.error("Entry order not filled, cancelling")
                await self._cancel_order(entry_order.id)
                return None

            # Submit stop loss order
            stop_loss_order = await self._submit_stop_loss_order(signal, position)
            if stop_loss_order:
                position.stop_loss_order_id = stop_loss_order.id

            # Submit take profit order
            take_profit_order = await self._submit_take_profit_order(signal, position)
            if take_profit_order:
                position.take_profit_order_id = take_profit_order.id

            # Add position to tracker
            self.position_tracker.add_position(position)

            self.logger.info(
                "Position opened successfully",
                position_id=str(position.id),
                market=position.market_question,
            )

            return position

        except Exception as e:
            self.logger.error(
                "Failed to execute signal",
                error=str(e),
                exc_info=True,
            )
            return None

    async def _submit_entry_order(
        self,
        signal: TradingSignal,
        position: Position,
    ) -> Optional[Order]:
        """Submit entry order"""
        order = Order(
            id=str(uuid4()),
            market_id=signal.market.id,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            price=signal.entry_price,
            size=signal.position_size,
        )

        # Submit to API
        try:
            order_request = OrderRequest(
                market_id=order.market_id,
                side=order.side.value,
                price=order.price,
                size=order.size,
                order_type=order.order_type.value,
            )

            response = await self.api_client.submit_order(order_request)

            # Update order with exchange ID
            order.exchange_order_id = response.order_id
            order.status = OrderStatusEnum.SUBMITTED
            order.submitted_at = datetime.now(timezone.utc)

            # Track order
            self.order_manager.add_order(order)

            self.logger.info(
                "Entry order submitted",
                order_id=order.id,
                exchange_order_id=response.order_id,
            )

            return order

        except Exception as e:
            self.logger.error(
                "Failed to submit entry order",
                error=str(e),
                exc_info=True,
            )
            return None

    async def _submit_stop_loss_order(
        self,
        signal: TradingSignal,
        position: Position,
    ) -> Optional[Order]:
        """Submit stop loss order"""
        order = Order(
            id=str(uuid4()),
            market_id=signal.market.id,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=signal.stop_loss_price,
            size=signal.position_size,
        )

        try:
            order_request = OrderRequest(
                market_id=order.market_id,
                side=order.side.value,
                price=order.price,
                size=order.size,
                order_type=order.order_type.value,
            )

            response = await self.api_client.submit_order(order_request)

            order.exchange_order_id = response.order_id
            order.status = OrderStatusEnum.SUBMITTED
            order.submitted_at = datetime.now(timezone.utc)

            self.order_manager.add_order(order)

            self.logger.info(
                "Stop loss order submitted",
                order_id=order.id,
                price=float(order.price),
            )

            return order

        except Exception as e:
            self.logger.error(
                "Failed to submit stop loss order",
                error=str(e),
                exc_info=True,
            )
            return None

    async def _submit_take_profit_order(
        self,
        signal: TradingSignal,
        position: Position,
    ) -> Optional[Order]:
        """Submit take profit order"""
        order = Order(
            id=str(uuid4()),
            market_id=signal.market.id,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=signal.take_profit_price,
            size=signal.position_size,
        )

        try:
            order_request = OrderRequest(
                market_id=order.market_id,
                side=order.side.value,
                price=order.price,
                size=order.size,
                order_type=order.order_type.value,
            )

            response = await self.api_client.submit_order(order_request)

            order.exchange_order_id = response.order_id
            order.status = OrderStatusEnum.SUBMITTED
            order.submitted_at = datetime.now(timezone.utc)

            self.order_manager.add_order(order)

            self.logger.info(
                "Take profit order submitted",
                order_id=order.id,
                price=float(order.price),
            )

            return order

        except Exception as e:
            self.logger.error(
                "Failed to submit take profit order",
                error=str(e),
                exc_info=True,
            )
            return None

    async def close_position(
        self,
        position: Position,
        exit_price: Decimal,
        exit_reason: ExitReason,
    ) -> bool:
        """
        Close a position

        Args:
            position: Position to close
            exit_price: Exit price
            exit_reason: Reason for exit

        Returns:
            True if successful
        """
        self.logger.info(
            "Closing position",
            position_id=str(position.id),
            market=position.market_question,
            exit_price=float(exit_price),
            reason=exit_reason.value,
        )

        try:
            # Cancel pending stop loss and take profit orders
            if position.stop_loss_order_id:
                await self._cancel_order(position.stop_loss_order_id)
            if position.take_profit_order_id:
                await self._cancel_order(position.take_profit_order_id)

            # Submit exit order (market order for immediate fill)
            exit_order = await self._submit_exit_order(position, exit_price)
            if not exit_order:
                self.logger.error("Failed to submit exit order")
                return False

            position.exit_order_id = exit_order.id

            # Wait for fill
            filled = await self._wait_for_fill(exit_order.id, timeout=30)
            if not filled:
                self.logger.warning("Exit order not filled immediately")

            # Close position in tracker
            self.position_tracker.close_position(
                position.id,
                exit_price,
                exit_reason,
            )

            return True

        except Exception as e:
            self.logger.error(
                "Failed to close position",
                error=str(e),
                exc_info=True,
            )
            return False

    async def _submit_exit_order(
        self,
        position: Position,
        exit_price: Decimal,
    ) -> Optional[Order]:
        """Submit exit order (aggressive limit order since Kalshi is limit-only)"""
        # Use aggressive pricing to ensure fill: sell at 95% of exit price
        aggressive_price = (exit_price * Decimal("0.95")).quantize(Decimal("0.01"))
        # Ensure price stays within valid range
        aggressive_price = max(aggressive_price, Decimal("0.01"))

        order = Order(
            id=str(uuid4()),
            market_id=position.market_id,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            price=aggressive_price,
            size=position.position_size,
        )

        try:
            order_request = OrderRequest(
                market_id=order.market_id,
                side=order.side.value,
                price=order.price,
                size=order.size,
                order_type=order.order_type.value,
            )

            response = await self.api_client.submit_order(order_request)

            order.exchange_order_id = response.order_id
            order.status = OrderStatusEnum.SUBMITTED
            order.submitted_at = datetime.now(timezone.utc)

            self.order_manager.add_order(order)

            return order

        except Exception as e:
            self.logger.error(
                "Failed to submit exit order",
                error=str(e),
                exc_info=True,
            )
            return None

    async def _cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            order = self.order_manager.get_order(order_id)
            if not order or not order.exchange_order_id:
                return False

            await self.api_client.cancel_order(order.exchange_order_id)

            self.order_manager.update_order(
                order_id,
                status=OrderStatusEnum.CANCELLED,
            )

            self.logger.info("Order cancelled", order_id=order_id)
            return True

        except Exception as e:
            self.logger.error(
                "Failed to cancel order",
                order_id=order_id,
                error=str(e),
            )
            return False

    async def _wait_for_fill(self, order_id: str, timeout: int = 30) -> bool:
        """
        Wait for an order to fill

        Args:
            order_id: Order ID
            timeout: Timeout in seconds

        Returns:
            True if filled
        """
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        while (loop.time() - start_time) < timeout:
            order = self.order_manager.get_order(order_id)
            if not order:
                return False

            if order.is_filled():
                return True

            # Check order status from API
            if order.exchange_order_id:
                try:
                    status = await self.api_client.get_order_status(order.exchange_order_id)
                    self.order_manager.update_order(
                        order_id,
                        status=OrderStatusEnum(status.status),
                        filled_size=status.filled_size,
                        avg_fill_price=status.avg_fill_price,
                    )

                    if status.status == "FILLED":
                        return True

                except Exception as e:
                    self.logger.warning(
                        "Failed to check order status",
                        order_id=order_id,
                        error=str(e),
                    )

            await asyncio.sleep(1)

        return False
