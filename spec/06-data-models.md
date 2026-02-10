# Data Models

## Core Domain Models

### Market

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List

@dataclass
class OrderBookLevel:
    price: float
    size: float

@dataclass
class Market:
    """Represents a Kalshi prediction market"""

    # Identifiers
    market_id: str       # Ticker string, e.g. "KXHIGHNY-25JAN09-B56.5"
    event_ticker: Optional[str] = None   # e.g. "KXHIGHNY-25JAN09"
    series_ticker: Optional[str] = None  # e.g. "KXHIGHNY"

    # Market info
    question: str
    category: str
    end_date: datetime

    # Pricing (in dollars, 0.01-0.99)
    best_bid: float
    best_ask: float
    last_price: float

    # Volume & liquidity
    volume_24h: float
    volume_total: float
    bid_liquidity: float  # Size available at best bid
    ask_liquidity: float  # Size available at best ask

    # Order book
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]

    # Metadata
    active: bool
    last_update: datetime

    # Outcomes (always Yes/No on Kalshi)
    outcomes: List[str] = ("Yes", "No")

    @property
    def spread(self) -> float:
        """Bid-ask spread as percentage"""
        return (self.best_ask - self.best_bid) / self.best_bid

    @property
    def mid_price(self) -> float:
        """Mid-point between bid and ask"""
        return (self.best_bid + self.best_ask) / 2

    def meets_entry_criteria(self, config: dict) -> bool:
        """Check if market meets entry criteria"""
        return (
            self.active and
            self.best_bid >= config['entry_threshold'] and
            self.volume_24h >= config['min_volume'] and
            self.bid_liquidity >= config['min_liquidity'] and
            self.spread <= config['max_spread']
        )
```

### Order

```python
from enum import Enum
from typing import Optional
from datetime import datetime

class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderType(Enum):
    LIMIT = "LIMIT"
    # Note: Kalshi only supports LIMIT orders. No MARKET or POST_ONLY.

class OrderStatus(Enum):
    CREATED = "CREATED"
    PENDING = "PENDING"
    OPEN = "OPEN"           # Maps from Kalshi "resting"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"       # Maps from Kalshi "executed"
    CANCELLED = "CANCELLED" # Maps from Kalshi "canceled"
    REJECTED = "REJECTED"

@dataclass
class Order:
    """Represents a trading order"""

    # Order identification
    id: Optional[str] = None  # Set after submission
    client_order_id: str = None  # Our internal ID

    # Order details
    market_id: str          # Kalshi ticker string
    side: OrderSide
    order_type: OrderType   # Always LIMIT on Kalshi
    price: float            # In dollars (0.01-0.99)
    size: float             # In dollars (converted to contract count by client)

    # Kalshi-specific
    yes_side: bool = True   # True for yes contracts, False for no

    # Status
    status: OrderStatus = OrderStatus.CREATED
    filled_size: float = 0.0
    remaining_size: float = 0.0

    # Timestamps
    created_at: datetime = None
    submitted_at: Optional[datetime] = None
    filled_at: Optional[datetime] = None

    # Metadata
    strategy_id: Optional[str] = None  # Which position this belongs to
    order_purpose: Optional[str] = None  # "entry", "stop_loss", "take_profit"

    @property
    def is_active(self) -> bool:
        """Whether order is still active on exchange"""
        return self.status in [OrderStatus.PENDING, OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED]

    @property
    def is_filled(self) -> bool:
        """Whether order is completely filled"""
        return self.status == OrderStatus.FILLED

    @property
    def fill_percentage(self) -> float:
        """Percentage of order filled"""
        return self.filled_size / self.size if self.size > 0 else 0
```

### Position

```python
from typing import Optional
from datetime import datetime, timedelta

class PositionStatus(Enum):
    ENTERING = "ENTERING"
    ENTERED = "ENTERED"
    EXITING = "EXITING"
    CLOSED = "CLOSED"

@dataclass
class Position:
    """Represents an active trading position"""

    # Identification
    id: str  # Unique position ID
    market_id: str  # Kalshi ticker string

    # Position details
    entry_price: float
    size: float
    side: OrderSide = OrderSide.BUY  # Always BUY for this strategy

    # Related orders
    entry_order_id: str
    stop_loss_order_id: Optional[str] = None
    take_profit_order_id: Optional[str] = None

    # Targets
    stop_loss_price: float = None
    take_profit_price: float = None

    # Status
    status: PositionStatus = PositionStatus.ENTERING

    # Timestamps
    created_at: datetime = None
    entered_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None

    # P&L
    exit_price: Optional[float] = None
    realized_pnl: Optional[float] = None
    realized_pnl_pct: Optional[float] = None

    # Risk
    max_loss: float = None  # Dollar amount
    max_gain: float = None  # Dollar amount

    def calculate_targets(self, config: dict):
        """Calculate stop loss and take profit prices"""
        self.stop_loss_price = self.entry_price * (1 - config['stop_loss'])
        self.take_profit_price = self.entry_price * (1 + config['profit_target'])
        self.max_loss = (self.entry_price - self.stop_loss_price) * self.size
        self.max_gain = (self.take_profit_price - self.entry_price) * self.size

    def unrealized_pnl(self, current_price: float) -> float:
        """Calculate current unrealized P&L"""
        if self.status != PositionStatus.ENTERED:
            return 0.0
        return (current_price - self.entry_price) * self.size

    def unrealized_pnl_pct(self, current_price: float) -> float:
        """Calculate unrealized P&L percentage"""
        return (current_price - self.entry_price) / self.entry_price

    def should_timeout(self, max_hold_time: timedelta) -> bool:
        """Check if position has been held too long"""
        if self.entered_at is None:
            return False
        return datetime.utcnow() - self.entered_at > max_hold_time

    def close(self, exit_price: float):
        """Mark position as closed and calculate P&L"""
        self.status = PositionStatus.CLOSED
        self.exit_price = exit_price
        self.closed_at = datetime.utcnow()
        self.realized_pnl = (exit_price - self.entry_price) * self.size
        self.realized_pnl_pct = (exit_price - self.entry_price) / self.entry_price
```

### Trade

```python
@dataclass
class Trade:
    """Represents a completed trade (for history/analytics)"""

    # Identification
    id: str
    position_id: str
    market_id: str  # Kalshi ticker string

    # Trade details
    entry_price: float
    exit_price: float
    size: float

    # Timestamps
    entry_time: datetime
    exit_time: datetime
    hold_duration: timedelta

    # P&L
    gross_pnl: float
    gross_pnl_pct: float
    fees: float
    net_pnl: float
    net_pnl_pct: float

    # Exit reason
    exit_reason: str  # "take_profit", "stop_loss", "timeout", "manual"

    # Market conditions at entry/exit
    entry_volume: float
    exit_volume: float
    entry_spread: float
    exit_spread: float

    @property
    def was_winner(self) -> bool:
        return self.net_pnl > 0

    @property
    def hit_target(self) -> bool:
        return self.exit_reason == "take_profit"
```

## Account & Portfolio Models

### Account

```python
@dataclass
class Account:
    """Trading account state"""

    # Identification
    address: str  # Kalshi API key ID

    # Balances (in dollars, converted from cents by client)
    starting_balance: float
    current_balance: float
    available_balance: float  # Cash not in positions or pending orders

    # Positions
    open_positions: int
    total_exposure: float  # Sum of all position values

    # Daily tracking
    daily_starting_balance: float
    daily_pnl: float
    daily_pnl_pct: float

    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int

    # P&L
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float

    # Risk metrics
    max_drawdown: float
    largest_win: float
    largest_loss: float

    # Timestamps
    last_update: datetime

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    def can_open_position(self, position_value: float, config: dict) -> bool:
        """Check if we can open a new position"""
        # Check available balance
        if position_value > self.available_balance:
            return False

        # Check max positions
        if self.open_positions >= config['max_positions']:
            return False

        # Check total exposure
        new_exposure = self.total_exposure + position_value
        if new_exposure > self.current_balance * config['max_total_exposure']:
            return False

        return True
```

### PerformanceMetrics

```python
@dataclass
class PerformanceMetrics:
    """Performance analytics"""

    period: str  # "day", "week", "month", "all"

    # Return metrics
    total_return: float
    total_return_pct: float

    # Trade metrics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float

    # P&L metrics
    avg_win: float
    avg_loss: float
    avg_win_pct: float
    avg_loss_pct: float
    largest_win: float
    largest_loss: float

    # Risk metrics
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_pct: float

    # Execution metrics
    avg_hold_time: timedelta
    avg_slippage: float
    fill_rate: float

    # Frequency
    trades_per_day: float

    @property
    def profit_factor(self) -> float:
        """Gross profit / gross loss"""
        total_wins = self.winning_trades * self.avg_win
        total_losses = abs(self.losing_trades * self.avg_loss)

        if total_losses == 0:
            return float('inf')

        return total_wins / total_losses

    @property
    def expectancy(self) -> float:
        """Expected value per trade"""
        win_amount = self.win_rate * self.avg_win
        loss_amount = (1 - self.win_rate) * abs(self.avg_loss)
        return win_amount - loss_amount
```

## Event Models

### Signal

```python
@dataclass
class TradingSignal:
    """Trading signal from strategy"""

    # Signal details
    market_id: str  # Kalshi ticker string
    signal_type: str  # "ENTER_LONG"

    # Market conditions
    current_price: float
    entry_price: float  # Recommended entry

    # Targets
    stop_loss: float
    take_profit: float

    # Position sizing
    recommended_size: float
    max_position_value: float

    # Signal strength
    confidence: float  # 0-1 scale

    # Context
    market_volume: float
    market_liquidity: float
    spread: float

    # Timestamp
    created_at: datetime

    # Risk assessment
    risk_amount: float  # Dollar risk
    reward_amount: float  # Dollar reward
    risk_reward_ratio: float
```

### Alert

```python
class AlertLevel(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

@dataclass
class Alert:
    """System alert"""

    level: AlertLevel
    title: str
    message: str
    category: str  # "risk", "execution", "system", "market"

    # Context
    related_position_id: Optional[str] = None
    related_market_id: Optional[str] = None

    # Data
    data: dict = None

    # Timestamp
    created_at: datetime = None
    acknowledged: bool = False
```

## Database Models (SQLAlchemy)

```python
from sqlalchemy import Column, Integer, String, Float, DateTime, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class TradeHistory(Base):
    __tablename__ = 'trades'

    id = Column(String, primary_key=True)
    market_id = Column(String, nullable=False)
    position_id = Column(String, nullable=False)

    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    size = Column(Float, nullable=False)

    entry_time = Column(DateTime, nullable=False)
    exit_time = Column(DateTime, nullable=False)

    gross_pnl = Column(Float, nullable=False)
    net_pnl = Column(Float, nullable=False)

    exit_reason = Column(String, nullable=False)

class AccountSnapshot(Base):
    __tablename__ = 'account_snapshots'

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False)

    balance = Column(Float, nullable=False)
    open_positions = Column(Integer, nullable=False)
    daily_pnl = Column(Float, nullable=False)
    total_pnl = Column(Float, nullable=False)
```
