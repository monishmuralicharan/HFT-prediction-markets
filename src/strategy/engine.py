"""Strategy engine for signal generation and position management"""

from decimal import Decimal
from typing import Optional

from src.db.models import Account, ExitReason, Market, Position
from src.strategy.exits import ExitManager
from src.strategy.signals import SignalGenerator, TradingSignal
from src.utils.logging import get_logger


class StrategyEngine:
    """Main strategy engine"""

    def __init__(
        self,
        entry_threshold: Decimal,
        take_profit_pct: Decimal,
        stop_loss_pct: Decimal,
        max_hold_time_hours: int,
        max_position_size_pct: Decimal,
        min_position_size: Decimal,
        max_position_size: Decimal,
    ):
        """
        Initialize strategy engine

        Args:
            entry_threshold: Minimum probability for entry
            take_profit_pct: Take profit percentage
            stop_loss_pct: Stop loss percentage
            max_hold_time_hours: Maximum hours to hold position
            max_position_size_pct: Max position size as % of account
            min_position_size: Minimum position size ($)
            max_position_size: Maximum position size ($)
        """
        self.entry_threshold = entry_threshold
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.max_hold_time_hours = max_hold_time_hours
        self.max_position_size_pct = max_position_size_pct
        self.min_position_size = min_position_size
        self.max_position_size = max_position_size
        self.logger = get_logger(__name__)

        # Components
        self.signal_generator = SignalGenerator(
            entry_threshold=entry_threshold,
            take_profit_pct=take_profit_pct,
            stop_loss_pct=stop_loss_pct,
        )
        self.exit_manager = ExitManager(max_hold_time_hours=max_hold_time_hours)

    def evaluate_market(
        self,
        market: Market,
        account: Account,
    ) -> Optional[TradingSignal]:
        """
        Evaluate a market and generate entry signal if appropriate

        Args:
            market: Market to evaluate
            account: Current account state

        Returns:
            Trading signal if opportunity exists, None otherwise
        """
        # Calculate position size based on risk
        position_size = self._calculate_position_size(account)

        if position_size < self.min_position_size:
            self.logger.debug(
                "Position size too small",
                size=float(position_size),
                min_size=float(self.min_position_size),
            )
            return None

        # Generate entry signal
        try:
            signal = self.signal_generator.generate_entry_signal(
                market=market,
                position_size=position_size,
            )

            # Validate signal
            is_valid, reason = self.signal_generator.validate_signal(signal)
            if not is_valid:
                self.logger.warning(
                    "Invalid signal generated",
                    market=market.question,
                    reason=reason,
                )
                return None

            self.logger.info(
                "Generated entry signal",
                market=market.question,
                entry_price=float(signal.entry_price),
                stop_loss=float(signal.stop_loss_price),
                take_profit=float(signal.take_profit_price),
                size=float(signal.position_size),
                confidence=float(signal.confidence),
            )

            return signal

        except Exception as e:
            self.logger.error(
                "Failed to generate signal",
                market=market.question,
                error=str(e),
                exc_info=True,
            )
            return None

    def _calculate_position_size(self, account: Account) -> Decimal:
        """
        Calculate position size based on account balance and risk

        Uses 1% risk rule: risk 1% of account on each trade
        Risk = (entry_price - stop_loss_price) * size
        Therefore: size = (account * 0.01) / (entry_price - stop_loss_price)

        For simplicity, we'll use a fixed percentage of available balance

        Args:
            account: Current account state

        Returns:
            Position size in dollars
        """
        # Use percentage of available balance
        size = account.available_balance * self.max_position_size_pct

        # Clamp to min/max
        size = max(size, self.min_position_size)
        size = min(size, self.max_position_size)

        # Also clamp to available balance
        size = min(size, account.available_balance)

        return size

    def check_exit(
        self,
        position: Position,
        current_price: Decimal,
        market_closing: bool = False,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if position should be exited

        Args:
            position: Position to check
            current_price: Current market price
            market_closing: Whether market is closing

        Returns:
            (should_exit, exit_reason)
        """
        should_exit, exit_reason = self.exit_manager.should_exit(
            position=position,
            current_price=current_price,
            market_closing=market_closing,
        )

        if should_exit:
            return True, exit_reason.value if exit_reason else None

        # Update position metrics
        self.exit_manager.update_position_metrics(position, current_price)

        return False, None

    def calculate_exit_price(
        self,
        position: Position,
        current_price: Decimal,
        exit_reason: str,
    ) -> Decimal:
        """
        Calculate exit price for a position

        Args:
            position: Position being exited
            current_price: Current market price
            exit_reason: Reason for exit

        Returns:
            Exit price
        """
        # Convert string to enum
        try:
            reason_enum = ExitReason(exit_reason)
        except ValueError:
            # If not a valid enum, use current price
            return current_price

        return self.exit_manager.calculate_exit_price(
            position=position,
            current_price=current_price,
            exit_reason=reason_enum,
        )
