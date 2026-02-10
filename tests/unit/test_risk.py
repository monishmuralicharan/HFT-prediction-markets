"""Tests for risk management"""

from decimal import Decimal

import pytest

from src.db.models import Account
from src.risk.validators import PositionValidator
from src.risk.circuit_breakers import CircuitBreaker, CircuitBreakerType


@pytest.fixture
def position_validator():
    """Create position validator for testing"""
    return PositionValidator(
        max_position_size_pct=Decimal("0.10"),
        max_total_exposure_pct=Decimal("0.30"),
        max_concurrent_positions=10,
    )


@pytest.fixture
def circuit_breaker():
    """Create circuit breaker for testing"""
    return CircuitBreaker(
        max_daily_loss_pct=Decimal("0.05"),
        max_consecutive_losses=5,
        api_error_threshold=Decimal("0.10"),
        max_disconnect_seconds=15,
    )


@pytest.fixture
def account():
    """Create test account"""
    return Account(
        address="kalshi_test_key",
        total_balance=Decimal("10000"),
        available_balance=Decimal("10000"),
        starting_balance=Decimal("10000"),
        daily_starting_balance=Decimal("10000"),
    )


def test_position_validator_accepts_valid_position(position_validator, account):
    """Test that valid position passes validation"""
    position_size = Decimal("500")  # 5% of balance
    current_positions = 2

    can_open, error = position_validator.can_open_position(
        position_size, account, current_positions
    )

    assert can_open is True
    assert error is None


def test_position_validator_rejects_oversized_position(position_validator, account):
    """Test that oversized position is rejected"""
    position_size = Decimal("2000")  # 20% of balance (exceeds 10% limit)
    current_positions = 0

    can_open, error = position_validator.can_open_position(
        position_size, account, current_positions
    )

    assert can_open is False
    assert "exceeds limit" in error


def test_position_validator_rejects_too_many_positions(position_validator, account):
    """Test that too many positions are rejected"""
    position_size = Decimal("500")
    current_positions = 10  # At limit

    can_open, error = position_validator.can_open_position(
        position_size, account, current_positions
    )

    assert can_open is False
    assert "Max concurrent positions" in error


def test_position_validator_rejects_excess_exposure(position_validator, account):
    """Test that excessive total exposure is rejected"""
    account.locked_balance = Decimal("2500")  # 25% already locked
    position_size = Decimal("1000")  # Would bring total to 35% (exceeds 30% limit)
    current_positions = 3

    can_open, error = position_validator.can_open_position(
        position_size, account, current_positions
    )

    assert can_open is False
    assert "exposure" in error.lower() and "exceed" in error.lower()


def test_circuit_breaker_triggers_on_daily_loss(circuit_breaker, account):
    """Test circuit breaker triggers on daily loss limit"""
    account.daily_pnl = Decimal("-600")  # -6% loss (exceeds -5% limit)

    should_trigger, reason = circuit_breaker.check(
        account=account,
        api_error_rate=0.0,
        websocket_disconnect_seconds=0.0,
    )

    assert should_trigger is True
    assert reason == CircuitBreakerType.DAILY_LOSS


def test_circuit_breaker_triggers_on_consecutive_losses(circuit_breaker, account):
    """Test circuit breaker triggers on consecutive losses"""
    account.consecutive_losses = 5  # At limit

    should_trigger, reason = circuit_breaker.check(
        account=account,
        api_error_rate=0.0,
        websocket_disconnect_seconds=0.0,
    )

    assert should_trigger is True
    assert reason == CircuitBreakerType.CONSECUTIVE_LOSSES


def test_circuit_breaker_triggers_on_api_errors(circuit_breaker, account):
    """Test circuit breaker triggers on API error rate"""
    should_trigger, reason = circuit_breaker.check(
        account=account,
        api_error_rate=0.15,  # 15% error rate (exceeds 10% threshold)
        websocket_disconnect_seconds=0.0,
    )

    assert should_trigger is True
    assert reason == CircuitBreakerType.API_ERROR_RATE


def test_circuit_breaker_triggers_on_websocket_disconnect(circuit_breaker, account):
    """Test circuit breaker triggers on WebSocket disconnect"""
    should_trigger, reason = circuit_breaker.check(
        account=account,
        api_error_rate=0.0,
        websocket_disconnect_seconds=20.0,  # Exceeds 15 second limit
    )

    assert should_trigger is True
    assert reason == CircuitBreakerType.WEBSOCKET_DISCONNECT


def test_circuit_breaker_doesnt_trigger_on_normal_conditions(circuit_breaker, account):
    """Test circuit breaker doesn't trigger on normal conditions"""
    account.daily_pnl = Decimal("100")  # Positive
    account.consecutive_losses = 2  # Below limit

    should_trigger, reason = circuit_breaker.check(
        account=account,
        api_error_rate=0.05,  # Below threshold
        websocket_disconnect_seconds=5.0,  # Below limit
    )

    assert should_trigger is False
    assert reason is None
