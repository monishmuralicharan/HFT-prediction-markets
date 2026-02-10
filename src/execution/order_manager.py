"""Order management and tracking"""

from decimal import Decimal
from typing import Optional

from src.db.models import Order, OrderStatus as OrderStatusEnum
from src.utils.logging import get_logger


class OrderManager:
    """Manage and track orders"""

    def __init__(self):
        self.logger = get_logger(__name__)

        # Active orders (order_id -> Order)
        self.active_orders: dict[str, Order] = {}

        # Completed orders
        self.completed_orders: dict[str, Order] = {}

    def add_order(self, order: Order) -> None:
        """
        Add a new order to tracking

        Args:
            order: Order to track
        """
        self.active_orders[order.id] = order
        self.logger.debug(
            "Added order to tracking",
            order_id=order.id,
            market=order.market_id,
            side=order.side.value,
            price=float(order.price),
            size=float(order.size),
        )

    def update_order(
        self,
        order_id: str,
        status: Optional[OrderStatusEnum] = None,
        filled_size: Optional[Decimal] = None,
        avg_fill_price: Optional[Decimal] = None,
        exchange_order_id: Optional[str] = None,
    ) -> bool:
        """
        Update order status

        Args:
            order_id: Order ID to update
            status: New status
            filled_size: Filled size
            avg_fill_price: Average fill price
            exchange_order_id: Exchange order ID

        Returns:
            True if order was updated
        """
        order = self.active_orders.get(order_id)
        if not order:
            self.logger.warning("Order not found for update", order_id=order_id)
            return False

        # Update fields
        if status is not None:
            order.status = status

        if filled_size is not None:
            order.filled_size = filled_size

        if avg_fill_price is not None:
            order.avg_fill_price = avg_fill_price

        if exchange_order_id is not None:
            order.exchange_order_id = exchange_order_id

        # Move to completed if filled or cancelled
        if status is not None and status in [
            OrderStatusEnum.FILLED,
            OrderStatusEnum.CANCELLED,
            OrderStatusEnum.REJECTED,
        ]:
            self.completed_orders[order_id] = order
            del self.active_orders[order_id]

            self.logger.info(
                "Order completed",
                order_id=order_id,
                status=status.value,
                filled_size=float(order.filled_size),
            )

        return True

    def get_order(self, order_id: str) -> Optional[Order]:
        """Get an order by ID"""
        # Check active orders first
        if order_id in self.active_orders:
            return self.active_orders[order_id]

        # Check completed orders
        return self.completed_orders.get(order_id)

    def get_active_orders(self, market_id: Optional[str] = None) -> list[Order]:
        """
        Get active orders

        Args:
            market_id: Optional market ID filter

        Returns:
            List of active orders
        """
        orders = list(self.active_orders.values())

        if market_id:
            orders = [o for o in orders if o.market_id == market_id]

        return orders

    def get_orders_for_market(self, market_id: str) -> list[Order]:
        """Get all orders (active and completed) for a market"""
        active = [o for o in self.active_orders.values() if o.market_id == market_id]
        completed = [o for o in self.completed_orders.values() if o.market_id == market_id]
        return active + completed

    def cancel_all_for_market(self, market_id: str) -> list[str]:
        """
        Mark all active orders for a market as cancelled

        Args:
            market_id: Market ID

        Returns:
            List of cancelled order IDs
        """
        cancelled_ids = []

        for order_id, order in list(self.active_orders.items()):
            if order.market_id == market_id:
                self.update_order(order_id, status=OrderStatusEnum.CANCELLED)
                cancelled_ids.append(order_id)

        return cancelled_ids

    def get_order_count(self) -> int:
        """Get total number of active orders"""
        return len(self.active_orders)

    def clear_completed(self) -> int:
        """
        Clear completed orders from memory

        Returns:
            Number of orders cleared
        """
        count = len(self.completed_orders)
        self.completed_orders.clear()
        return count
