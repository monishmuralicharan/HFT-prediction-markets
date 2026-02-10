"""Circuit breaker logic for risk management"""

import time
from decimal import Decimal
from enum import Enum
from typing import Optional

from src.db.models import Account
from src.utils.logging import get_logger


class CircuitBreakerType(str, Enum):
    """Circuit breaker type"""

    DAILY_LOSS = "daily_loss"
    CONSECUTIVE_LOSSES = "consecutive_losses"
    API_ERROR_RATE = "api_error_rate"
    WEBSOCKET_DISCONNECT = "websocket_disconnect"
    MANUAL = "manual"


class CircuitBreaker:
    """Circuit breaker to halt trading on adverse conditions"""

    def __init__(
        self,
        max_daily_loss_pct: Decimal,
        max_consecutive_losses: int,
        api_error_threshold: Decimal,
        max_disconnect_seconds: int,
    ):
        """
        Initialize circuit breaker

        Args:
            max_daily_loss_pct: Max daily loss % before halting (e.g., 0.05 for 5%)
            max_consecutive_losses: Max consecutive losses before halting
            api_error_threshold: Max API error rate (0-1)
            max_disconnect_seconds: Max WebSocket disconnect time
        """
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_consecutive_losses = max_consecutive_losses
        self.api_error_threshold = api_error_threshold
        self.max_disconnect_seconds = max_disconnect_seconds
        self.logger = get_logger(__name__)

        # Circuit breaker state
        self.active = False
        self.reason: Optional[CircuitBreakerType] = None
        self.triggered_at: Optional[float] = None

    def check(
        self,
        account: Account,
        api_error_rate: float,
        websocket_disconnect_seconds: float,
    ) -> tuple[bool, Optional[CircuitBreakerType]]:
        """
        Check all circuit breaker conditions

        Args:
            account: Current account state
            api_error_rate: Current API error rate (0-1)
            websocket_disconnect_seconds: WebSocket disconnect duration

        Returns:
            (should_trigger, reason)
        """
        # Check daily loss limit
        # daily_pnl_pct() returns percentage (e.g., 5.0 for 5%)
        # max_daily_loss_pct is a ratio (e.g., 0.05 for 5%)
        daily_loss_pct = abs(account.daily_pnl_pct())
        max_loss_pct = self.max_daily_loss_pct * Decimal("100")
        if account.daily_pnl < 0 and daily_loss_pct >= max_loss_pct:
            self.logger.error(
                "CIRCUIT BREAKER: Daily loss limit exceeded",
                daily_pnl=float(account.daily_pnl),
                daily_pnl_pct=float(daily_loss_pct),
                limit_pct=float(self.max_daily_loss_pct * 100),
            )
            return True, CircuitBreakerType.DAILY_LOSS

        # Check consecutive losses
        if account.consecutive_losses >= self.max_consecutive_losses:
            self.logger.error(
                "CIRCUIT BREAKER: Max consecutive losses reached",
                consecutive_losses=account.consecutive_losses,
                max_consecutive=self.max_consecutive_losses,
            )
            return True, CircuitBreakerType.CONSECUTIVE_LOSSES

        # Check API error rate
        if api_error_rate >= self.api_error_threshold:
            self.logger.error(
                "CIRCUIT BREAKER: API error rate too high",
                error_rate=api_error_rate,
                threshold=self.api_error_threshold,
            )
            return True, CircuitBreakerType.API_ERROR_RATE

        # Check WebSocket disconnect
        if websocket_disconnect_seconds >= self.max_disconnect_seconds:
            self.logger.error(
                "CIRCUIT BREAKER: WebSocket disconnected too long",
                disconnect_seconds=websocket_disconnect_seconds,
                max_seconds=self.max_disconnect_seconds,
            )
            return True, CircuitBreakerType.WEBSOCKET_DISCONNECT

        return False, None

    def trigger(self, reason: CircuitBreakerType) -> None:
        """
        Trigger the circuit breaker

        Args:
            reason: Reason for triggering
        """
        self.active = True
        self.reason = reason
        self.triggered_at = time.time()

        self.logger.critical(
            "CIRCUIT BREAKER ACTIVATED",
            reason=reason.value,
        )

    def reset(self) -> None:
        """Reset the circuit breaker"""
        if self.active:
            self.logger.info(
                "Circuit breaker reset",
                was_active_for=self._get_active_duration(),
                reason=self.reason.value if self.reason else None,
            )

        self.active = False
        self.reason = None
        self.triggered_at = None

    def is_active(self) -> bool:
        """Check if circuit breaker is active"""
        return self.active

    def get_reason(self) -> Optional[str]:
        """Get the reason circuit breaker was triggered"""
        return self.reason.value if self.reason else None

    def _get_active_duration(self) -> float:
        """Get duration circuit breaker has been active"""
        if not self.triggered_at:
            return 0.0
        return time.time() - self.triggered_at

    def should_shutdown(self) -> bool:
        """
        Check if system should shutdown

        Some circuit breakers require immediate shutdown

        Returns:
            True if system should shutdown
        """
        if not self.active:
            return False

        # These conditions require immediate shutdown
        critical_conditions = [
            CircuitBreakerType.DAILY_LOSS,
            CircuitBreakerType.MANUAL,
        ]

        return self.reason in critical_conditions
