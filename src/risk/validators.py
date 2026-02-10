"""Pre-trade validation"""

from decimal import Decimal
from typing import Optional

from src.db.models import Account, Order
from src.strategy.signals import TradingSignal
from src.utils.logging import get_logger


class OrderValidator:
    """Validate orders before submission"""

    def __init__(
        self,
        max_slippage_pct: Decimal = Decimal("0.05"),  # 5% max slippage
    ):
        """
        Initialize order validator

        Args:
            max_slippage_pct: Maximum allowed slippage percentage
        """
        self.max_slippage_pct = max_slippage_pct
        self.logger = get_logger(__name__)

    def validate_order(self, order: Order) -> tuple[bool, Optional[str]]:
        """
        Validate an order before submission

        Args:
            order: Order to validate

        Returns:
            (is_valid, error_message)
        """
        # Check price is positive
        if order.price <= 0:
            return False, f"Invalid price: {order.price}"

        # Check price is within valid range (0.01 - 0.99)
        if order.price < Decimal("0.01") or order.price > Decimal("0.99"):
            return False, f"Price out of range: {order.price}"

        # Check size is positive
        if order.size <= 0:
            return False, f"Invalid size: {order.size}"

        # Check size is reasonable (< $10,000 per order)
        if order.size > Decimal("10000"):
            return False, f"Size too large: {order.size}"

        return True, None

    def validate_signal(
        self,
        signal: TradingSignal,
        account: Account,
    ) -> tuple[bool, Optional[str]]:
        """
        Validate a trading signal before execution

        Args:
            signal: Trading signal to validate
            account: Current account state

        Returns:
            (is_valid, error_message)
        """
        # Check sufficient balance
        if signal.position_size > account.available_balance:
            return False, (
                f"Insufficient balance: need {signal.position_size}, "
                f"have {account.available_balance}"
            )

        # Check position size is reasonable
        if signal.position_size < Decimal("10"):
            return False, f"Position size too small: {signal.position_size}"

        # Check risk/reward ratio is reasonable
        risk = signal.entry_price - signal.stop_loss_price
        reward = signal.take_profit_price - signal.entry_price

        if risk <= 0:
            return False, "Invalid stop loss: risk <= 0"

        if reward <= 0:
            return False, "Invalid take profit: reward <= 0"

        risk_reward_ratio = reward / risk
        if risk_reward_ratio < Decimal("1.5"):
            return False, f"Poor risk/reward ratio: {risk_reward_ratio:.2f}"

        return True, None

    def check_slippage(
        self,
        expected_price: Decimal,
        actual_price: Decimal,
    ) -> tuple[bool, Decimal]:
        """
        Check if slippage is acceptable

        Args:
            expected_price: Expected execution price
            actual_price: Actual execution price

        Returns:
            (is_acceptable, slippage_pct)
        """
        if expected_price == 0:
            return False, Decimal("1")  # 100% slippage if expected is 0
        slippage = abs(actual_price - expected_price) / expected_price
        is_acceptable = slippage <= self.max_slippage_pct

        if not is_acceptable:
            self.logger.warning(
                "Excessive slippage",
                expected=float(expected_price),
                actual=float(actual_price),
                slippage_pct=float(slippage * 100),
            )

        return is_acceptable, slippage


class PositionValidator:
    """Validate positions and limits"""

    def __init__(
        self,
        max_position_size_pct: Decimal,
        max_total_exposure_pct: Decimal,
        max_concurrent_positions: int,
    ):
        """
        Initialize position validator

        Args:
            max_position_size_pct: Max single position size as % of balance
            max_total_exposure_pct: Max total exposure as % of balance
            max_concurrent_positions: Max number of concurrent positions
        """
        self.max_position_size_pct = max_position_size_pct
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_concurrent_positions = max_concurrent_positions
        self.logger = get_logger(__name__)

    def can_open_position(
        self,
        position_size: Decimal,
        account: Account,
        current_positions: int,
    ) -> tuple[bool, Optional[str]]:
        """
        Check if a new position can be opened

        Args:
            position_size: Size of new position
            account: Current account state
            current_positions: Number of currently open positions

        Returns:
            (can_open, reason)
        """
        # Check position count limit
        if current_positions >= self.max_concurrent_positions:
            return False, (
                f"Max concurrent positions reached: "
                f"{current_positions}/{self.max_concurrent_positions}"
            )

        # Check single position size limit
        max_single_size = account.total_balance * self.max_position_size_pct
        if position_size > max_single_size:
            return False, (
                f"Position size exceeds limit: "
                f"{position_size} > {max_single_size} "
                f"({self.max_position_size_pct * 100}% of balance)"
            )

        # Check total exposure limit
        new_total_exposure = account.locked_balance + position_size
        max_total_exposure = account.total_balance * self.max_total_exposure_pct

        if new_total_exposure > max_total_exposure:
            return False, (
                f"Total exposure would exceed limit: "
                f"{new_total_exposure} > {max_total_exposure} "
                f"({self.max_total_exposure_pct * 100}% of balance)"
            )

        # Check available balance
        if position_size > account.available_balance:
            return False, (
                f"Insufficient available balance: "
                f"{position_size} > {account.available_balance}"
            )

        return True, None
