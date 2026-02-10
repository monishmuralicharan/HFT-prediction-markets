"""Tests for strategy engine"""

from decimal import Decimal
from datetime import datetime, timezone

import pytest

from src.db.models import Account, Market
from src.strategy.engine import StrategyEngine


@pytest.fixture
def strategy_engine():
    """Create strategy engine for testing"""
    return StrategyEngine(
        entry_threshold=Decimal("0.85"),
        take_profit_pct=Decimal("0.02"),
        stop_loss_pct=Decimal("0.01"),
        max_hold_time_hours=2,
        max_position_size_pct=Decimal("0.10"),
        min_position_size=Decimal("50"),
        max_position_size=Decimal("1000"),
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


@pytest.fixture
def market():
    """Create test market"""
    return Market(
        id="test_market",
        question="Will team A win?",
        outcomes=["YES", "NO"],
        end_date=datetime.now(timezone.utc),
        active=True,
        volume_24h=Decimal("50000"),
        liquidity=Decimal("1000"),
        best_bid=Decimal("0.88"),
        best_ask=Decimal("0.90"),
        last_price=Decimal("0.89"),
    )


def test_evaluate_market_generates_signal(strategy_engine, account, market):
    """Test that evaluate_market generates a valid signal"""
    market.probability = Decimal("0.90")

    signal = strategy_engine.evaluate_market(market, account)

    assert signal is not None
    assert signal.entry_price == market.best_ask
    assert signal.stop_loss_price < signal.entry_price
    assert signal.take_profit_price > signal.entry_price
    assert signal.position_size >= Decimal("50")


def test_evaluate_market_respects_min_size(strategy_engine, account, market):
    """Test that position size respects minimum"""
    account.available_balance = Decimal("10")  # Very small balance

    signal = strategy_engine.evaluate_market(market, account)

    # Should return None because position size would be too small
    assert signal is None


def test_calculate_position_size(strategy_engine, account):
    """Test position size calculation"""
    size = strategy_engine._calculate_position_size(account)

    # Should be 10% of available balance
    expected = account.available_balance * Decimal("0.10")
    assert size == expected


def test_calculate_position_size_clamped(strategy_engine):
    """Test that position size is clamped to limits"""
    # Test upper limit
    large_account = Account(
        address="kalshi_test_key",
        total_balance=Decimal("100000"),
        available_balance=Decimal("100000"),
        starting_balance=Decimal("100000"),
        daily_starting_balance=Decimal("100000"),
    )

    size = strategy_engine._calculate_position_size(large_account)
    assert size == Decimal("1000")  # Max position size

    # Test lower limit
    small_account = Account(
        address="kalshi_test_key",
        total_balance=Decimal("100"),
        available_balance=Decimal("100"),
        starting_balance=Decimal("100"),
        daily_starting_balance=Decimal("100"),
    )

    size = strategy_engine._calculate_position_size(small_account)
    assert size == Decimal("50")  # Min position size
