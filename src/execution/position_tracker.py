"""Position tracking and management"""

from decimal import Decimal
from typing import Optional
from uuid import UUID

from src.db.models import ExitReason, Position, PositionStatus
from src.utils.logging import get_logger


class PositionTracker:
    """Track and manage open positions"""

    def __init__(self):
        self.logger = get_logger(__name__)

        # Open positions (position_id -> Position)
        self.open_positions: dict[UUID, Position] = {}

        # Closed positions
        self.closed_positions: dict[UUID, Position] = {}

    def add_position(self, position: Position) -> None:
        """
        Add a new position

        Args:
            position: Position to track
        """
        self.open_positions[position.id] = position

        self.logger.info(
            "Position opened",
            position_id=str(position.id),
            market=position.market_question,
            entry_price=float(position.entry_price),
            size=float(position.position_size),
            stop_loss=float(position.stop_loss_price),
            take_profit=float(position.take_profit_price),
        )

    def close_position(
        self,
        position_id: UUID,
        exit_price: Decimal,
        exit_reason: ExitReason,
    ) -> Optional[Position]:
        """
        Close a position

        Args:
            position_id: Position ID to close
            exit_price: Exit price
            exit_reason: Reason for exit

        Returns:
            Closed position, or None if not found
        """
        position = self.open_positions.get(position_id)
        if not position:
            self.logger.warning("Position not found for closing", position_id=str(position_id))
            return None

        # Close the position
        position.close(exit_price, exit_reason)

        # Move to closed positions
        self.closed_positions[position_id] = position
        del self.open_positions[position_id]

        self.logger.info(
            "Position closed",
            position_id=str(position_id),
            market=position.market_question,
            exit_price=float(exit_price),
            exit_reason=exit_reason.value,
            realized_pnl=float(position.realized_pnl or 0),
            realized_pnl_pct=float(position.realized_pnl_pct or 0),
        )

        return position

    def get_position(self, position_id: UUID) -> Optional[Position]:
        """Get a position by ID"""
        # Check open positions first
        if position_id in self.open_positions:
            return self.open_positions[position_id]

        # Check closed positions
        return self.closed_positions.get(position_id)

    def get_open_positions(self) -> list[Position]:
        """Get all open positions"""
        return list(self.open_positions.values())

    def get_position_for_market(self, market_id: str) -> Optional[Position]:
        """
        Get open position for a market

        Args:
            market_id: Market ID

        Returns:
            Position if exists, None otherwise
        """
        for position in self.open_positions.values():
            if position.market_id == market_id:
                return position
        return None

    def has_position_for_market(self, market_id: str) -> bool:
        """Check if there's an open position for a market"""
        return self.get_position_for_market(market_id) is not None

    def get_open_count(self) -> int:
        """Get number of open positions"""
        return len(self.open_positions)

    def calculate_total_exposure(self) -> Decimal:
        """Calculate total locked capital in open positions"""
        return sum(
            position.position_size for position in self.open_positions.values()
        )

    def calculate_unrealized_pnl(self, market_prices: dict[str, Decimal]) -> Decimal:
        """
        Calculate total unrealized P&L

        Args:
            market_prices: Dictionary of market_id -> current_price

        Returns:
            Total unrealized P&L
        """
        total_pnl = Decimal("0")

        for position in self.open_positions.values():
            current_price = market_prices.get(position.market_id)
            if current_price:
                pnl = position.calculate_unrealized_pnl(current_price)
                total_pnl += pnl

        return total_pnl

    def get_position_by_order(self, order_id: str) -> Optional[Position]:
        """
        Get position by order ID

        Args:
            order_id: Order ID (entry, stop loss, take profit, or exit)

        Returns:
            Position if found
        """
        for position in self.open_positions.values():
            if (
                position.entry_order_id == order_id
                or position.stop_loss_order_id == order_id
                or position.take_profit_order_id == order_id
                or position.exit_order_id == order_id
            ):
                return position

        # Also check closed positions
        for position in self.closed_positions.values():
            if (
                position.entry_order_id == order_id
                or position.stop_loss_order_id == order_id
                or position.take_profit_order_id == order_id
                or position.exit_order_id == order_id
            ):
                return position

        return None

    def update_position_orders(
        self,
        position_id: UUID,
        entry_order_id: Optional[str] = None,
        stop_loss_order_id: Optional[str] = None,
        take_profit_order_id: Optional[str] = None,
        exit_order_id: Optional[str] = None,
    ) -> bool:
        """
        Update position order IDs

        Args:
            position_id: Position ID
            entry_order_id: Entry order ID
            stop_loss_order_id: Stop loss order ID
            take_profit_order_id: Take profit order ID
            exit_order_id: Exit order ID

        Returns:
            True if updated
        """
        position = self.open_positions.get(position_id)
        if not position:
            return False

        if entry_order_id:
            position.entry_order_id = entry_order_id
        if stop_loss_order_id:
            position.stop_loss_order_id = stop_loss_order_id
        if take_profit_order_id:
            position.take_profit_order_id = take_profit_order_id
        if exit_order_id:
            position.exit_order_id = exit_order_id

        return True

    def clear_closed(self, keep_recent: int = 100) -> int:
        """
        Clear closed positions from memory

        Args:
            keep_recent: Number of recent closed positions to keep

        Returns:
            Number of positions cleared
        """
        if len(self.closed_positions) <= keep_recent:
            return 0

        # Sort by close time
        sorted_positions = sorted(
            self.closed_positions.values(),
            key=lambda p: p.exit_time or p.entry_time,
            reverse=True,
        )

        # Keep only recent ones
        to_keep = {p.id: p for p in sorted_positions[:keep_recent]}
        cleared = len(self.closed_positions) - len(to_keep)

        self.closed_positions = to_keep

        self.logger.info(f"Cleared {cleared} closed positions from memory")
        return cleared
