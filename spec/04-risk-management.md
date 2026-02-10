# Risk Management

## Core Principles

1. **Capital Preservation**: Never risk more than 1% on a single trade
2. **Position Limits**: Cap exposure to prevent over-concentration
3. **Circuit Breakers**: Auto-shutdown on abnormal losses
4. **Diversification**: Limit correlated positions

## Position Sizing

### Per-Trade Risk
```python
def calculate_position_size(account_balance, entry_price, stop_loss_price):
    """
    Risk 1% of account per trade
    """
    risk_amount = account_balance * 0.01  # 1% risk
    risk_per_share = entry_price - stop_loss_price

    position_size = risk_amount / risk_per_share

    # Max position: 10% of account (Kelly half-sizing)
    max_position_value = account_balance * 0.10
    max_shares = max_position_value / entry_price

    return min(position_size, max_shares)
```

### Example Calculation
```
Account Balance: $10,000
Entry Price: $0.92
Stop Loss: $0.9108 (1% lower)
Risk per share: $0.0092

Risk amount = $10,000 * 0.01 = $100
Position size = $100 / $0.0092 = 10,869 shares
Position value = 10,869 * $0.92 = $9,999.48

Max position check:
10% of $10,000 = $1,000 max position
$1,000 / $0.92 = 1,087 shares max

Final position: 1,087 shares (limited by max position size)
```

## Position Limits

### Per-Position Limits
- **Max Single Position**: 10% of total capital
- **Max Position Size**: $1,000 (absolute cap for early testing)
- **Min Position Size**: $50 (avoid dust trades)

### Aggregate Limits
- **Max Total Exposure**: 30% of capital (max 3 concurrent positions)
- **Max Positions**: 5 concurrent positions
- **Max Correlated Positions**: 2 (e.g., same event markets)

### Correlation Detection
```python
def are_markets_correlated(market1, market2):
    """
    Detect if two markets are about the same event
    """
    # Same event ticker
    if market1.event_ticker == market2.event_ticker:
        return True

    # Same series within 24 hours
    if (market1.series_ticker == market2.series_ticker and
        within_24h(market1.start_time, market2.start_time)):
        return True

    return False
```

## Circuit Breakers

### Daily Loss Limit
```python
class CircuitBreaker:
    max_daily_loss = 0.05  # 5% of starting balance

    def check_daily_loss(self, starting_balance, current_balance):
        daily_loss = (starting_balance - current_balance) / starting_balance

        if daily_loss >= self.max_daily_loss:
            self.trigger_shutdown("Daily loss limit reached")
            return True

        return False
```

### Consecutive Loss Limit
```python
class ConsecutiveLossBreaker:
    max_consecutive_losses = 5

    def check_consecutive_losses(self, trade_history):
        recent_trades = trade_history[-5:]

        if all(trade.pnl < 0 for trade in recent_trades):
            self.trigger_shutdown("5 consecutive losses")
            return True

        return False
```

### API Error Rate Breaker
```python
class APIErrorBreaker:
    max_error_rate = 0.10  # 10% error rate
    window = 100  # last 100 requests

    def check_error_rate(self, request_log):
        recent_requests = request_log[-self.window:]
        error_count = sum(1 for req in recent_requests if req.failed)

        error_rate = error_count / len(recent_requests)

        if error_rate >= self.max_error_rate:
            self.trigger_shutdown("High API error rate")
            return True

        return False
```

### WebSocket Disconnect Breaker
```python
class ConnectionBreaker:
    max_disconnect_time = 15  # seconds

    def check_connection(self, last_message_time):
        time_since_message = now() - last_message_time

        if time_since_message > self.max_disconnect_time:
            self.close_all_positions("WebSocket disconnected too long")
            return True

        return False
```

## Order Validation

### Pre-Submission Checks
```python
def validate_order(order, account_state):
    checks = [
        check_sufficient_balance(order, account_state),
        check_position_limits(order, account_state),
        check_price_validity(order),
        check_size_validity(order),
        check_market_active(order.market),
    ]

    return all(checks)
```

### Detailed Validations

#### 1. Balance Check
```python
def check_sufficient_balance(order, account):
    required_balance = order.price * order.size

    if order.side == "BUY":
        available = account.cash_balance - account.pending_buys
        return available >= required_balance

    return True  # Selling doesn't require balance
```

#### 2. Position Limit Check
```python
def check_position_limits(order, account):
    if order.side == "BUY":
        new_exposure = sum(account.positions.values()) + order.value

        # Single position check
        if order.value > account.balance * 0.10:
            return False

        # Total exposure check
        if new_exposure > account.balance * 0.30:
            return False

    return True
```

#### 3. Price Sanity Check
```python
def check_price_validity(order):
    # Prices must be between 0.01 and 0.99
    if not (0.01 <= order.price <= 0.99):
        return False

    # Check against current market
    market = get_market(order.market_id)

    if order.side == "BUY":
        # Don't buy above 0.95 (limited upside)
        if order.price > 0.95:
            return False

        # Don't pay more than 2% above best ask
        if order.price > market.best_ask * 1.02:
            return False

    return True
```

## Slippage Control

### Expected vs Actual Execution
```python
class SlippageMonitor:
    max_slippage = 0.005  # 0.5%

    def check_execution(self, expected_price, actual_price, side):
        if side == "BUY":
            slippage = (actual_price - expected_price) / expected_price
        else:  # SELL
            slippage = (expected_price - actual_price) / expected_price

        if slippage > self.max_slippage:
            self.log_warning(f"High slippage: {slippage:.2%}")

        return slippage
```

### Fill Rate Monitoring
```python
class FillRateMonitor:
    min_fill_rate = 0.70  # 70% of orders should fill

    def check_fill_rate(self, order_history):
        recent_orders = order_history[-100:]
        filled = sum(1 for o in recent_orders if o.status == "FILLED")

        fill_rate = filled / len(recent_orders)

        if fill_rate < self.min_fill_rate:
            self.alert("Low fill rate - adjust strategy")
```

## Market-Specific Risks

### Liquidity Risk
```python
def assess_liquidity_risk(market):
    """
    Rate market liquidity (0-1 scale)
    1 = high liquidity, low risk
    0 = low liquidity, high risk
    """
    score = 0

    # Volume score (0-0.4)
    if market.volume > 100000:
        score += 0.4
    elif market.volume > 50000:
        score += 0.3
    elif market.volume > 10000:
        score += 0.2
    else:
        score += 0.1

    # Depth score (0-0.3)
    total_depth = sum(level.size for level in market.bids[:3])
    if total_depth > 5000:
        score += 0.3
    elif total_depth > 1000:
        score += 0.2
    else:
        score += 0.1

    # Spread score (0-0.3)
    spread = (market.best_ask - market.best_bid) / market.best_bid
    if spread < 0.01:
        score += 0.3
    elif spread < 0.02:
        score += 0.2
    else:
        score += 0.1

    return score
```

### Event Risk
```python
def check_event_risk(market):
    """
    Identify high-risk events
    """
    high_risk_factors = []

    # Time to event
    time_to_event = market.event_time - now()
    if time_to_event < timedelta(minutes=15):
        high_risk_factors.append("Event starting soon")

    # Volatility
    recent_price_range = market.high_24h - market.low_24h
    if recent_price_range > 0.10:  # 10% range
        high_risk_factors.append("High volatility")

    # Unusual volume
    if market.volume_1h > market.avg_volume_1h * 3:
        high_risk_factors.append("Volume spike")

    return high_risk_factors
```

## Emergency Procedures

### Force Exit All Positions
```python
def emergency_exit_all():
    """
    Close all positions immediately using aggressive limit orders.
    Kalshi does not support market orders, so we use limit orders
    priced at 95% of current price to ensure rapid fill.
    """
    positions = get_all_positions()

    for position in positions:
        # Cancel all pending orders
        cancel_order(position.stop_loss_order)
        cancel_order(position.take_profit_order)

        # Submit aggressive limit sell order
        current_price = get_market_price(position.market_id)
        aggressive_price = max(current_price * 0.95, 0.01)

        aggressive_limit_order = create_limit_order(
            side="SELL",
            size=position.size,
            market=position.market_id,
            price=aggressive_price
        )

        submit_order(aggressive_limit_order)
        log_emergency_exit(position)
```

### Graceful Shutdown
```python
def graceful_shutdown():
    """
    Stop accepting new positions, exit existing safely
    """
    global ACCEPTING_NEW_POSITIONS
    ACCEPTING_NEW_POSITIONS = False

    # Cancel all entry orders
    cancel_all_entry_orders()

    # Let existing positions hit stops or targets
    # Or exit after timeout
    monitor_positions_until_closed(timeout=3600)  # 1 hour max
```

## Risk Reporting

### Real-Time Metrics
- Current total exposure (% of capital)
- Number of open positions
- Unrealized P&L
- Daily realized P&L
- Win rate (today, this week, all-time)

### Alerts
- Position size exceeds 8% (approaching limit)
- Total exposure exceeds 25% (approaching limit)
- Daily loss reaches 3% (warning)
- Daily loss reaches 5% (critical - shutdown)
- 3 consecutive losses (warning)
- 5 consecutive losses (critical - shutdown)
- WebSocket disconnected > 10s (warning)
- WebSocket disconnected > 30s (critical - exit positions)

### Daily Risk Report
```python
def generate_daily_risk_report():
    return {
        "date": today(),
        "starting_balance": account.starting_balance,
        "ending_balance": account.current_balance,
        "daily_pnl": account.daily_pnl,
        "daily_pnl_pct": account.daily_pnl_pct,
        "total_trades": account.trade_count,
        "winning_trades": account.win_count,
        "win_rate": account.win_rate,
        "largest_win": account.largest_win,
        "largest_loss": account.largest_loss,
        "max_drawdown": account.max_drawdown,
        "sharpe_ratio": calculate_sharpe_ratio(),
        "circuit_breaker_triggers": account.cb_triggers,
    }
```
