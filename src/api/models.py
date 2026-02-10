"""Kalshi API request and response models"""

from decimal import Decimal
from typing import Any, Optional

from pydantic import BaseModel, Field


class OrderBookLevel(BaseModel):
    """Order book price level"""

    price: Decimal
    size: Decimal


class OrderBook(BaseModel):
    """Order book data"""

    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: int


class MarketData(BaseModel):
    """Market data from API"""

    id: str  # Kalshi ticker string (e.g., "KXHIGHNY-25JAN09-B56.5")
    question: str
    outcomes: list[str] = Field(default_factory=lambda: ["Yes", "No"])
    active: bool
    closed: bool
    end_date_iso: str
    volume: Decimal = Field(default=Decimal("0"))
    liquidity: Decimal = Field(default=Decimal("0"))
    event_ticker: Optional[str] = None
    series_ticker: Optional[str] = None


class OrderRequest(BaseModel):
    """Order submission request (internal representation in dollars)"""

    market_id: str  # Kalshi ticker
    side: str  # "BUY" or "SELL" (internal); KalshiClient maps to lowercase
    price: Decimal  # Dollar price (0.01-0.99); KalshiClient converts to cents
    size: Decimal  # Dollar amount; KalshiClient converts to contract count
    order_type: str = "LIMIT"
    time_in_force: str = "GTC"
    reduce_only: bool = False
    post_only: bool = False
    yes_side: bool = True  # Whether this is a Yes-side order


class OrderResponse(BaseModel):
    """Order submission response"""

    order_id: str
    status: str
    market_id: str
    side: str
    price: Decimal
    size: Decimal
    filled_size: Decimal = Decimal("0")
    remaining_size: Decimal
    created_at: int


# Map Kalshi statuses to internal statuses
KALSHI_STATUS_MAP = {
    "resting": "OPEN",
    "canceled": "CANCELLED",
    "executed": "FILLED",
}


class OrderStatus(BaseModel):
    """Order status response"""

    order_id: str
    status: str  # "PENDING", "OPEN", "FILLED", "CANCELLED", "REJECTED"
    market_id: str
    side: str
    price: Decimal
    size: Decimal
    filled_size: Decimal
    avg_fill_price: Optional[Decimal] = None
    created_at: int
    updated_at: int


class CancelOrderResponse(BaseModel):
    """Cancel order response"""

    order_id: str
    status: str
    cancelled_at: int


class BalanceResponse(BaseModel):
    """Account balance response (dollar-denominated, client converts from cents)"""

    asset: str = "USD"
    total: Decimal
    available: Decimal
    locked: Decimal = Decimal("0")


class PositionResponse(BaseModel):
    """Position response"""

    market_id: str
    outcome: str
    size: Decimal
    entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal


class APIError(BaseModel):
    """API error response"""

    code: str
    message: str
    details: Optional[dict[str, Any]] = None
