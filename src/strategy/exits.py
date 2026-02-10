"""Exit logic for positions"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from src.db.models import ExitReason, Position
from src.utils.logging import get_logger


class ExitManager:
    """Manage position exits"""

    def __init__(self, max_hold_time_hours: int):
        """
        Initialize exit manager

        Args:
            max_hold_time_hours: Maximum hours to hold a position
        """
        self.max_hold_time_hours = max_hold_time_hours
        self.logger = get_logger(__name__)

    def should_exit(
        self,
        position: Position,
        current_price: Decimal,
        market_closing: bool = False,
    ) -> tuple[bool, Optional[ExitReason]]:
        """
        Check if position should be exited

        Args:
            position: Position to check
            current_price: Current market price
            market_closing: Whether market is about to close

        Returns:
            (should_exit, exit_reason)
        """
        # Check market closing
        if market_closing:
            self.logger.info(
                "Market closing - exit position",
                position_id=str(position.id),
                market=position.market_question,
            )
            return True, ExitReason.MARKET_CLOSED

        # Check stop loss
        if current_price <= position.stop_loss_price:
            self.logger.info(
                "Stop loss hit",
                position_id=str(position.id),
                market=position.market_question,
                current_price=float(current_price),
                stop_loss=float(position.stop_loss_price),
            )
            return True, ExitReason.STOP_LOSS

        # Check take profit
        if current_price >= position.take_profit_price:
            self.logger.info(
                "Take profit hit",
                position_id=str(position.id),
                market=position.market_question,
                current_price=float(current_price),
                take_profit=float(position.take_profit_price),
            )
            return True, ExitReason.TAKE_PROFIT

        # Check timeout
        hours_open = position.hours_open()
        if hours_open >= self.max_hold_time_hours:
            self.logger.info(
                "Max hold time reached",
                position_id=str(position.id),
                market=position.market_question,
                hours_open=hours_open,
                max_hours=self.max_hold_time_hours,
            )
            return True, ExitReason.TIMEOUT

        return False, None

    def calculate_exit_price(
        self,
        position: Position,
        current_price: Decimal,
        exit_reason: ExitReason,
    ) -> Decimal:
        """
        Calculate actual exit price based on reason

        Args:
            position: Position being exited
            current_price: Current market price
            exit_reason: Reason for exit

        Returns:
            Exit price to use
        """
        # For stop loss and take profit, use the limit prices
        # (assumes limit orders will be filled at those prices)
        if exit_reason == ExitReason.STOP_LOSS:
            return position.stop_loss_price
        elif exit_reason == ExitReason.TAKE_PROFIT:
            return position.take_profit_price

        # For timeout and market close, use current price
        # (will submit market order)
        return current_price

    def update_position_metrics(
        self,
        position: Position,
        current_price: Decimal,
    ) -> None:
        """
        Update position tracking metrics (max profit/drawdown)

        Args:
            position: Position to update
            current_price: Current market price
        """
        # Calculate current P&L percentage
        pnl_pct = position.calculate_unrealized_pnl_pct(current_price)

        # Update max profit
        if position.max_profit_pct is None or pnl_pct > position.max_profit_pct:
            position.max_profit_pct = pnl_pct
            self.logger.debug(
                "New max profit",
                position_id=str(position.id),
                max_profit_pct=float(pnl_pct),
            )

        # Update max drawdown
        if position.max_drawdown_pct is None or pnl_pct < position.max_drawdown_pct:
            position.max_drawdown_pct = pnl_pct
            self.logger.debug(
                "New max drawdown",
                position_id=str(position.id),
                max_drawdown_pct=float(pnl_pct),
            )
