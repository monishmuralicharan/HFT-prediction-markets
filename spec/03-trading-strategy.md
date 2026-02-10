# Trading Strategy

## Core Strategy: Mean Reversion on High-Probability Outcomes

### Thesis
Prediction markets at 90%+ probability are often efficiently priced, but experience micro-fluctuations due to:
- Temporary order book imbalances
- Emotional betting on underdogs
- Market-making spread dynamics

By entering at 90%+ and capturing 2% moves, we exploit these micro-inefficiencies with favorable risk/reward.

## Entry Criteria

### Primary Filters
1. **Probability Threshold**: Best bid >= 0.85 (85%)
2. **Market Type**: Prediction markets (all categories)
3. **Time to Resolution**: 1-24 hours (optimal liquidity)
4. **Liquidity**:
   - Total market volume >= $10,000
   - Depth at entry price >= $500
5. **Spread**: Bid-ask spread <= 2%

### Signal Generation
```python
def should_enter(market):
    bid_price = market.best_bid
    ask_price = market.best_ask

    # Core criteria
    if bid_price < 0.85:
        return False

    # Liquidity check
    if market.bid_volume < 500:
        return False

    # Spread check
    spread = (ask_price - bid_price) / bid_price
    if spread > 0.02:
        return False

    # Profitability check (can we get 2% profit?)
    target_exit = bid_price * 1.02
    if target_exit > 0.95:  # Too close to ceiling
        return False

    return True
```

## Order Placement Strategy

### Three-Order System

When entry criteria met, submit three orders simultaneously:

#### 1. Market Entry (BUY Order)
- **Type**: Limit order (slightly above best ask for aggressive fill)
- **Price**: `best_ask + 0.001` (aggressive entry)
- **Size**: Calculated based on position sizing (see Risk Management)
- **Timeout**: 15 seconds (cancel if not filled)

#### 2. Stop Loss Order (SELL)
- **Trigger**: Entry price - 1%
- **Type**: Limit order at aggressive price (entry * 0.95) to ensure fill
- **Size**: Match entry size
- **Purpose**: Limit downside risk

#### 3. Take Profit Order (SELL)
- **Trigger**: Entry price + 2%
- **Type**: Limit order at target price
- **Size**: Match entry size
- **Purpose**: Capture profit target

**Note**: All orders on Kalshi are LIMIT orders. There are no market orders. For stop loss and emergency exits, aggressive pricing (well below/above market) is used to simulate immediate execution.

### Example
```
Entry at: $0.92
- BUY: 100 contracts @ $0.92
- STOP LOSS: Sell 100 contracts @ $0.8740 (aggressive limit at 0.92 * 0.95)
- TAKE PROFIT: Sell 100 contracts @ $0.9384 (0.92 * 1.02)
```

## Position Management

### Active Position States

```
ENTERING → ENTERED → MONITORING → EXITING → CLOSED
```

#### ENTERING
- BUY order submitted, awaiting fill
- Timeout: 5 seconds
- If not filled: Cancel and abandon

#### ENTERED
- BUY order filled
- Stop loss and take profit orders active
- Monitor price for trigger events

#### MONITORING
- Track real-time price updates
- Check stop loss/take profit order status
- Update orders if market conditions change

#### EXITING
- Stop loss OR take profit triggered
- Awaiting exit order fill
- Cancel remaining order

#### CLOSED
- Position fully exited
- Record P&L
- Release capital for next trade

### Order Prioritization

1. **Stop Loss**: Highest priority, cancel-replace if needed
2. **Take Profit**: Adjust if better opportunities arise
3. **Entry**: Only when capital available and criteria met

## Exit Logic

### Automatic Exits

#### 1. Take Profit Hit (Target Scenario)
```python
if current_price >= entry_price * 1.02:
    # Take profit order should fill automatically
    # Verify fill, cancel stop loss
    cancel_order(stop_loss_order_id)
    close_position(profit=True)
```

#### 2. Stop Loss Hit (Risk Control)
```python
if current_price <= entry_price * 0.99:
    # Stop loss order should fill automatically
    # Verify fill, cancel take profit
    cancel_order(take_profit_order_id)
    close_position(profit=False)
```

### Manual Exits (Override Scenarios)

#### 3. Time-Based Exit
- If position held > 2 hours without hitting targets
- Exit at aggressive limit price to free capital
- Prevents capital lock-up

#### 4. Event-Based Exit
- Major news (event cancelled, unexpected outcome)
- Abnormal volume spike (> 3x average)
- Exit immediately at aggressive limit price

#### 5. Market Close Warning
- 30 minutes before market resolution
- Exit all positions regardless of P&L
- Prevents binary risk

## Order Management

### Order State Tracking
```python
class Position:
    entry_order: Order
    stop_loss_order: Order
    take_profit_order: Order
    entry_price: float
    size: float
    status: PositionState
    created_at: timestamp
    filled_at: timestamp
```

### Order Synchronization
- Poll order status every 1 second
- Verify fills via WebSocket fill/order_update messages
- Reconcile position with account balance every 30s

### Partial Fills
```python
if entry_order.filled_qty < entry_order.qty:
    # Reduce stop loss and take profit sizes
    stop_loss_size = entry_order.filled_qty
    take_profit_size = entry_order.filled_qty
    update_orders()
```

## Edge Cases

### 1. Immediate Adverse Move
- Entry fills, price immediately drops 0.5%
- Keep stop loss active, wait for mean reversion
- Max hold time: 2 hours

### 2. Slow Fill on Entry
- Entry order partially filled after 3 seconds
- Decision: Cancel remainder or wait
- Default: Cancel, take what we got

### 3. Stop Loss Not Filled
- Price gapped through stop loss level
- Submit aggressive limit order (at 95% of stop price) immediately
- Record slippage event

### 4. Both Orders Cancelled
- Market closed or delisted
- Check position via API
- Force exit at aggressive limit price

## Performance Metrics

### Target Metrics
- **Win Rate**: 60%+ (majority of positions hit take profit)
- **Average Profit**: 2% per winning trade
- **Average Loss**: 1% per losing trade
- **Risk/Reward Ratio**: 2:1
- **Max Drawdown**: < 5% of total capital
- **Trades per Day**: 10-50 (depends on opportunity flow)

### Kelly Criterion Sizing
```
f* = (bp - q) / b
Where:
  b = 2 (profit ratio)
  p = 0.6 (win probability estimate)
  q = 0.4 (loss probability)

f* = (2*0.6 - 0.4) / 2 = 0.4 / 2 = 0.2 (20% of bankroll)
```

Conservative sizing: Use half-Kelly = **10% max per position**

## Strategy Improvements (Future)

1. **Multi-Level Scaling**
   - Add to winners (scale in at +0.5%)
   - Reduce losers (scale out at -0.5%)

2. **Dynamic Stop Loss**
   - Trail stop loss as profit increases
   - Lock in profits above +1%

3. **Market-Making Mode**
   - Place both bid and ask orders
   - Capture spread + directional edge

4. **Cross-Market Arbitrage**
   - Same event on different markets
   - Exploit price discrepancies

5. **Volume-Weighted Entry**
   - Enter larger positions during high liquidity
   - Reduce size during thin markets
