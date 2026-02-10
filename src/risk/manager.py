"""Risk management system"""

from decimal import Decimal
from typing import Callable, Optional

from src.db.models import Account, Order, Position
from src.risk.circuit_breakers import CircuitBreaker, CircuitBreakerType
from src.risk.validators import OrderValidator, PositionValidator
from src.strategy.signals import TradingSignal
from src.utils.logging import get_logger


class RiskManager:
    """Central risk management system"""

    def __init__(
        self,
        max_position_size_pct: Decimal,
        max_total_exposure_pct: Decimal,
        max_concurrent_positions: int,
        max_daily_loss_pct: Decimal,
        max_consecutive_losses: int,
        api_error_threshold: Decimal,
        max_disconnect_seconds: int,
        on_circuit_breaker: Optional[Callable[[CircuitBreakerType], None]] = None,
    ):
        """
        Initialize risk manager

        Args:
            max_position_size_pct: Max single position size as % of balance
            max_total_exposure_pct: Max total exposure as % of balance
            max_concurrent_positions: Max concurrent positions
            max_daily_loss_pct: Max daily loss % before circuit breaker
            max_consecutive_losses: Max consecutive losses before circuit breaker
            api_error_threshold: API error rate threshold
            max_disconnect_seconds: Max WebSocket disconnect time
            on_circuit_breaker: Callback when circuit breaker triggers
        """
        self.logger = get_logger(__name__)
        self.on_circuit_breaker = on_circuit_breaker

        # Validators
        self.order_validator = OrderValidator()
        self.position_validator = PositionValidator(
            max_position_size_pct=max_position_size_pct,
            max_total_exposure_pct=max_total_exposure_pct,
            max_concurrent_positions=max_concurrent_positions,
        )

        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(
            max_daily_loss_pct=max_daily_loss_pct,
            max_consecutive_losses=max_consecutive_losses,
            api_error_threshold=api_error_threshold,
            max_disconnect_seconds=max_disconnect_seconds,
        )

    def validate_signal(
        self,
        signal: TradingSignal,
        account: Account,
        current_positions: int,
    ) -> tuple[bool, Optional[str]]:
        """
        Validate a trading signal before execution

        Args:
            signal: Trading signal to validate
            account: Current account state
            current_positions: Number of open positions

        Returns:
            (is_valid, error_message)
        """
        # Check circuit breaker
        if self.circuit_breaker.is_active():
            return False, f"Circuit breaker active: {self.circuit_breaker.get_reason()}"

        # Validate signal parameters
        is_valid, error = self.order_validator.validate_signal(signal, account)
        if not is_valid:
            self.logger.warning("Signal validation failed", error=error)
            return False, error

        # Validate position limits
        can_open, error = self.position_validator.can_open_position(
            position_size=signal.position_size,
            account=account,
            current_positions=current_positions,
        )
        if not can_open:
            self.logger.warning("Position limit check failed", error=error)
            return False, error

        return True, None

    def validate_order(self, order: Order) -> tuple[bool, Optional[str]]:
        """
        Validate an order before submission

        Args:
            order: Order to validate

        Returns:
            (is_valid, error_message)
        """
        # Check circuit breaker
        if self.circuit_breaker.is_active():
            return False, f"Circuit breaker active: {self.circuit_breaker.get_reason()}"

        return self.order_validator.validate_order(order)

    def check_circuit_breakers(
        self,
        account: Account,
        api_error_rate: float,
        websocket_disconnect_seconds: float,
    ) -> bool:
        """
        Check circuit breaker conditions

        Args:
            account: Current account state
            api_error_rate: API error rate (0-1)
            websocket_disconnect_seconds: WebSocket disconnect duration

        Returns:
            True if circuit breaker triggered
        """
        should_trigger, reason = self.circuit_breaker.check(
            account=account,
            api_error_rate=api_error_rate,
            websocket_disconnect_seconds=websocket_disconnect_seconds,
        )

        if should_trigger and not self.circuit_breaker.is_active():
            self.circuit_breaker.trigger(reason)

            # Call callback
            if self.on_circuit_breaker and reason:
                self.on_circuit_breaker(reason)

            return True

        return False

    def is_circuit_breaker_active(self) -> bool:
        """Check if circuit breaker is active"""
        return self.circuit_breaker.is_active()

    def get_circuit_breaker_reason(self) -> Optional[str]:
        """Get circuit breaker reason"""
        return self.circuit_breaker.get_reason()

    def reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker (manual override)"""
        self.logger.warning("Manually resetting circuit breaker")
        self.circuit_breaker.reset()

    def trigger_manual_shutdown(self) -> None:
        """Manually trigger circuit breaker for shutdown"""
        self.logger.critical("Manual shutdown triggered")
        self.circuit_breaker.trigger(CircuitBreakerType.MANUAL)

        if self.on_circuit_breaker:
            self.on_circuit_breaker(CircuitBreakerType.MANUAL)

    def should_shutdown(self) -> bool:
        """Check if system should shutdown"""
        return self.circuit_breaker.should_shutdown()

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
        return self.order_validator.check_slippage(expected_price, actual_price)

    def get_risk_metrics(self, account: Account) -> dict:
        """
        Get current risk metrics

        Args:
            account: Current account state

        Returns:
            Dictionary of risk metrics
        """
        total_exposure = account.locked_balance
        exposure_pct = (
            (total_exposure / account.total_balance * Decimal("100"))
            if account.total_balance > 0
            else Decimal("0")
        )

        return {
            "total_exposure": float(total_exposure),
            "exposure_pct": float(exposure_pct),
            "available_balance": float(account.available_balance),
            "daily_pnl": float(account.daily_pnl),
            "daily_pnl_pct": float(account.daily_pnl_pct()),
            "consecutive_losses": account.consecutive_losses,
            "circuit_breaker_active": self.circuit_breaker.is_active(),
            "circuit_breaker_reason": self.circuit_breaker.get_reason(),
        }
