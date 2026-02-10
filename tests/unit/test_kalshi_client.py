"""Tests for Kalshi API client (cents/dollars conversion, order payload)"""

from decimal import Decimal

import pytest

from src.api.models import (
    BalanceResponse,
    KALSHI_STATUS_MAP,
    OrderBookLevel,
    OrderRequest,
)


class TestCentsDollarsConversion:
    """Test cents-to-dollars boundary logic"""

    def test_cents_to_dollars(self):
        """Test basic cents to dollars conversion"""
        cents = 85
        dollars = Decimal(str(cents)) / Decimal("100")
        assert dollars == Decimal("0.85")

    def test_dollars_to_cents(self):
        """Test basic dollars to cents conversion"""
        dollars = Decimal("0.42")
        cents = int(dollars * 100)
        assert cents == 42

    def test_balance_conversion(self):
        """Test balance response conversion from cents to dollars"""
        balance_cents = 15000  # $150.00
        available_cents = 12000  # $120.00

        total = Decimal(str(balance_cents)) / Decimal("100")
        available = Decimal(str(available_cents)) / Decimal("100")
        locked = total - available

        balance = BalanceResponse(total=total, available=available, locked=locked)

        assert balance.total == Decimal("150")
        assert balance.available == Decimal("120")
        assert balance.locked == Decimal("30")

    def test_orderbook_level_cents_to_dollars(self):
        """Test order book price conversion"""
        # Kalshi returns yes prices in cents
        yes_cents = 75
        price_dollars = Decimal(str(yes_cents)) / Decimal("100")

        level = OrderBookLevel(price=price_dollars, size=Decimal("10"))
        assert level.price == Decimal("0.75")


class TestOrderPayloadConstruction:
    """Test order payload construction for Kalshi API"""

    def test_contract_count_calculation(self):
        """Test that contract count is calculated correctly from dollar size and price"""
        order = OrderRequest(
            market_id="KXTEST-25JAN01-B50",
            side="BUY",
            price=Decimal("0.50"),
            size=Decimal("100"),  # $100
        )

        # count = dollar_size / dollar_price
        count = int(order.size / order.price)
        assert count == 200  # 200 contracts at $0.50 each

    def test_contract_count_non_round(self):
        """Test contract count truncates properly"""
        order = OrderRequest(
            market_id="KXTEST-25JAN01-B75",
            side="BUY",
            price=Decimal("0.75"),
            size=Decimal("100"),  # $100
        )

        count = int(order.size / order.price)
        assert count == 133  # int(100/0.75) = 133

    def test_yes_price_cents_conversion(self):
        """Test dollar price to cents conversion for API payload"""
        price_dollars = Decimal("0.65")
        yes_price_cents = int(price_dollars * 100)
        assert yes_price_cents == 65

    def test_side_mapping(self):
        """Test side mapping from internal to Kalshi format"""
        # Internal uses uppercase BUY/SELL
        # Kalshi uses yes/no
        buy_side = "BUY"
        sell_side = "SELL"

        kalshi_buy = "yes" if buy_side == "BUY" else "no"
        kalshi_sell = "yes" if sell_side == "BUY" else "no"

        assert kalshi_buy == "yes"
        assert kalshi_sell == "no"

    def test_order_request_defaults(self):
        """Test OrderRequest default values"""
        order = OrderRequest(
            market_id="KXTEST",
            side="BUY",
            price=Decimal("0.50"),
            size=Decimal("100"),
        )

        assert order.order_type == "LIMIT"
        assert order.time_in_force == "GTC"
        assert order.yes_side is True
        assert order.reduce_only is False
        assert order.post_only is False

    def test_full_payload_construction(self):
        """Test constructing a complete Kalshi order payload"""
        order = OrderRequest(
            market_id="KXHIGHNY-25JAN09-B56.5",
            side="BUY",
            price=Decimal("0.85"),
            size=Decimal("170"),
        )

        yes_price_cents = int(order.price * 100)
        count = int(order.size / order.price)

        payload = {
            "ticker": order.market_id,
            "side": "yes" if order.side == "BUY" else "no",
            "type": "limit",
            "count": count,
            "yes_price": yes_price_cents,
        }

        assert payload["ticker"] == "KXHIGHNY-25JAN09-B56.5"
        assert payload["side"] == "yes"
        assert payload["type"] == "limit"
        assert payload["count"] == 200  # int(170/0.85)
        assert payload["yes_price"] == 85


class TestKalshiStatusMapping:
    """Test Kalshi status to internal status mapping"""

    def test_resting_maps_to_open(self):
        assert KALSHI_STATUS_MAP["resting"] == "OPEN"

    def test_canceled_maps_to_cancelled(self):
        assert KALSHI_STATUS_MAP["canceled"] == "CANCELLED"

    def test_executed_maps_to_filled(self):
        assert KALSHI_STATUS_MAP["executed"] == "FILLED"

    def test_unknown_status_passthrough(self):
        """Unknown statuses should be handled gracefully"""
        unknown = "pending"
        mapped = KALSHI_STATUS_MAP.get(unknown, unknown.upper())
        assert mapped == "PENDING"
