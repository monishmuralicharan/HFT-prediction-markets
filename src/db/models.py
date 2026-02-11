"""Data models for the HFT bot"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class OrderSide(str, Enum):
    """Order side"""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type (Kalshi supports limit orders only)"""

    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    """Order status"""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class PositionStatus(str, Enum):
    """Position status"""

    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class ExitReason(str, Enum):
    """Reason for position exit"""

    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TIMEOUT = "TIMEOUT"
    MANUAL = "MANUAL"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    MARKET_CLOSED = "MARKET_CLOSED"


class Market(BaseModel):
    """Market data model"""

    id: str  # Kalshi ticker string
    question: str
    outcomes: List[str]
    end_date: datetime
    active: bool
    volume_24h: Decimal
    liquidity: Decimal

    # Kalshi-specific identifiers
    event_ticker: Optional[str] = None
    series_ticker: Optional[str] = None

    # Current prices
    best_bid: Optional[Decimal] = None
    best_ask: Optional[Decimal] = None
    last_price: Optional[Decimal] = None

    # Calculated fields
    spread: Optional[Decimal] = None
    probability: Optional[Decimal] = None

    def calculate_spread(self) -> Optional[Decimal]:
        """Calculate bid-ask spread"""
        if self.best_bid and self.best_ask:
            self.spread = self.best_ask - self.best_bid
        return self.spread

    def calculate_probability(self) -> Optional[Decimal]:
        """Calculate implied probability from price"""
        if self.last_price:
            self.probability = self.last_price
        return self.probability


class Order(BaseModel):
    """Order model"""

    id: str = Field(default_factory=lambda: str(uuid4()))
    market_id: str
    side: OrderSide
    order_type: OrderType
    price: Decimal
    size: Decimal
    status: OrderStatus = OrderStatus.PENDING

    # Tracking
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None

    # Fill details
    filled_size: Decimal = Decimal("0")
    avg_fill_price: Optional[Decimal] = None

    # External IDs
    exchange_order_id: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def is_filled(self) -> bool:
        """Check if order is fully filled"""
        return self.status == OrderStatus.FILLED

    def is_active(self) -> bool:
        """Check if order is active"""
        return self.status in [OrderStatus.PENDING, OrderStatus.SUBMITTED, OrderStatus.PARTIAL]


class Position(BaseModel):
    """Position model"""

    id: UUID = Field(default_factory=uuid4)
    market_id: str
    market_question: str
    outcome: str

    # Entry details
    entry_time: datetime
    entry_price: Decimal
    entry_probability: Decimal
    position_size: Decimal

    # Exit details
    exit_time: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
    exit_reason: Optional[ExitReason] = None

    # P&L
    realized_pnl: Optional[Decimal] = None
    realized_pnl_pct: Optional[Decimal] = None

    # Risk metrics
    stop_loss_price: Decimal
    take_profit_price: Decimal
    max_drawdown_pct: Optional[Decimal] = None
    max_profit_pct: Optional[Decimal] = None

    # Order IDs
    entry_order_id: Optional[str] = None
    stop_loss_order_id: Optional[str] = None
    take_profit_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    # Status
    status: PositionStatus = PositionStatus.OPEN

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("entry_time", "exit_time", mode="before")
    @classmethod
    def ensure_timezone(cls, v: Optional[datetime]) -> Optional[datetime]:
        """Ensure datetime has timezone"""
        if v and v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v

    def calculate_unrealized_pnl(self, current_price: Decimal) -> Decimal:
        """Calculate unrealized P&L"""
        return (current_price - self.entry_price) * self.position_size

    def calculate_unrealized_pnl_pct(self, current_price: Decimal) -> Decimal:
        """Calculate unrealized P&L percentage"""
        if self.entry_price == 0:
            return Decimal("0")
        return ((current_price - self.entry_price) / self.entry_price) * Decimal("100")

    def close(self, exit_price: Decimal, exit_reason: ExitReason) -> None:
        """Close the position"""
        self.exit_time = datetime.now(timezone.utc)
        self.exit_price = exit_price
        self.exit_reason = exit_reason
        self.status = PositionStatus.CLOSED

        # Calculate realized P&L
        self.realized_pnl = (exit_price - self.entry_price) * self.position_size
        if self.entry_price != 0:
            self.realized_pnl_pct = (
                (exit_price - self.entry_price) / self.entry_price
            ) * Decimal("100")
        else:
            self.realized_pnl_pct = Decimal("0")

    def is_open(self) -> bool:
        """Check if position is open"""
        return self.status == PositionStatus.OPEN

    def hours_open(self) -> float:
        """Calculate hours since position opened"""
        if self.exit_time:
            delta = self.exit_time - self.entry_time
        else:
            delta = datetime.now(timezone.utc) - self.entry_time
        return delta.total_seconds() / 3600


class Trade(BaseModel):
    """Trade record for database"""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Trade identification
    market_id: str
    market_question: str
    outcome: str

    # Entry details
    entry_time: datetime
    entry_price: Decimal
    entry_probability: Decimal
    position_size: Decimal

    # Exit details
    exit_time: Optional[datetime] = None
    exit_price: Optional[Decimal] = None
    exit_reason: Optional[str] = None

    # P&L
    realized_pnl: Optional[Decimal] = None
    realized_pnl_pct: Optional[Decimal] = None

    # Risk metrics
    stop_loss_price: Decimal
    take_profit_price: Decimal
    max_drawdown_pct: Optional[Decimal] = None
    max_profit_pct: Optional[Decimal] = None

    # Order IDs
    entry_order_id: Optional[str] = None
    stop_loss_order_id: Optional[str] = None
    take_profit_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None

    # Status
    status: str

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_position(cls, position: Position) -> "Trade":
        """Create Trade from Position"""
        return cls(
            id=position.id,
            market_id=position.market_id,
            market_question=position.market_question,
            outcome=position.outcome,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            entry_probability=position.entry_probability,
            position_size=position.position_size,
            exit_time=position.exit_time,
            exit_price=position.exit_price,
            exit_reason=position.exit_reason.value if position.exit_reason else None,
            realized_pnl=position.realized_pnl,
            realized_pnl_pct=position.realized_pnl_pct,
            stop_loss_price=position.stop_loss_price,
            take_profit_price=position.take_profit_price,
            max_drawdown_pct=position.max_drawdown_pct,
            max_profit_pct=position.max_profit_pct,
            entry_order_id=position.entry_order_id,
            stop_loss_order_id=position.stop_loss_order_id,
            take_profit_order_id=position.take_profit_order_id,
            exit_order_id=position.exit_order_id,
            status=position.status.value,
            metadata=position.metadata,
        )


class AccountSnapshot(BaseModel):
    """Account snapshot model"""

    id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Balance
    total_balance: Decimal
    available_balance: Decimal
    locked_balance: Decimal

    # Exposure
    total_exposure: Decimal
    exposure_pct: Decimal

    # P&L
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")

    # Daily metrics
    daily_pnl: Optional[Decimal] = None
    daily_pnl_pct: Optional[Decimal] = None
    daily_trades: int = 0
    daily_wins: int = 0
    daily_losses: int = 0

    # Position counts
    open_positions: int = 0

    # Circuit breaker state
    circuit_breaker_active: bool = False
    circuit_breaker_reason: Optional[str] = None

    # Metadata
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Account(BaseModel):
    """Account state model (in-memory)"""

    address: str
    total_balance: Decimal
    available_balance: Decimal
    locked_balance: Decimal = Decimal("0")

    # P&L tracking
    starting_balance: Decimal
    realized_pnl: Decimal = Decimal("0")
    unrealized_pnl: Decimal = Decimal("0")

    # Daily metrics
    daily_starting_balance: Decimal
    daily_pnl: Decimal = Decimal("0")
    daily_trades: int = 0
    daily_wins: int = 0
    daily_losses: int = 0

    # Tracking
    consecutive_losses: int = 0
    last_reset: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def update_balance(self, new_balance: Decimal) -> None:
        """Update account balance"""
        self.total_balance = new_balance
        self.available_balance = new_balance - self.locked_balance

    def lock_funds(self, amount: Decimal) -> None:
        """Lock funds for a position"""
        self.locked_balance += amount
        self.available_balance -= amount

    def unlock_funds(self, amount: Decimal) -> None:
        """Unlock funds from a position"""
        self.locked_balance -= amount
        self.available_balance += amount

    def record_trade(self, pnl: Decimal) -> None:
        """Record a completed trade"""
        self.realized_pnl += pnl
        self.daily_pnl += pnl
        self.daily_trades += 1

        if pnl > 0:
            self.daily_wins += 1
            self.consecutive_losses = 0
        else:
            self.daily_losses += 1
            self.consecutive_losses += 1

    def daily_pnl_pct(self) -> Decimal:
        """Calculate daily P&L percentage"""
        if self.daily_starting_balance == 0:
            return Decimal("0")
        return (self.daily_pnl / self.daily_starting_balance) * Decimal("100")

    def total_pnl(self) -> Decimal:
        """Calculate total P&L"""
        return self.realized_pnl + self.unrealized_pnl

    def reset_daily_metrics(self) -> None:
        """Reset daily metrics (call at start of new day)"""
        self.daily_starting_balance = self.total_balance
        self.daily_pnl = Decimal("0")
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self.last_reset = datetime.now(timezone.utc)

    def to_snapshot(self, open_positions: int = 0) -> AccountSnapshot:
        """Convert to AccountSnapshot for database"""
        total_pnl = self.total_pnl()
        total_exposure = self.locked_balance

        return AccountSnapshot(
            total_balance=self.total_balance,
            available_balance=self.available_balance,
            locked_balance=self.locked_balance,
            total_exposure=total_exposure,
            exposure_pct=(
                (total_exposure / self.total_balance) * Decimal("100")
                if self.total_balance > 0
                else Decimal("0")
            ),
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
            total_pnl=total_pnl,
            daily_pnl=self.daily_pnl,
            daily_pnl_pct=self.daily_pnl_pct(),
            daily_trades=self.daily_trades,
            daily_wins=self.daily_wins,
            daily_losses=self.daily_losses,
            open_positions=open_positions,
        )
