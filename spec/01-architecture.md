# Architecture Overview

## System Components

```
+-------------------------------------------------------------+
|                     HFT Trading Bot                          |
+-------------------------------------------------------------+
|                                                               |
|  +------------------+         +------------------+           |
|  |  Market Monitor  |         |  Order Manager   |           |
|  |                  |-------->|                  |           |
|  | - WebSocket Feed |         | - Order Creation |           |
|  | - Price Updates  |         | - Order Tracking |           |
|  | - Market Filters |         | - Position Mgmt  |           |
|  +------------------+         +------------------+           |
|           |                            |                     |
|           |                            |                     |
|           v                            v                     |
|  +------------------+         +------------------+           |
|  | Strategy Engine  |-------->|  Risk Manager    |           |
|  |                  |         |                  |           |
|  | - Entry Logic    |         | - Position Limits|           |
|  | - Exit Logic     |         | - Circuit Breaker|           |
|  | - Signal Gen     |         | - Loss Limits    |           |
|  +------------------+         +------------------+           |
|           |                            |                     |
|           +------------+---------------+                     |
|                        v                                     |
|               +-----------------+                            |
|               | Execution Engine|                            |
|               |                 |                            |
|               | - Order Submit  |                            |
|               | - Retry Logic   |                            |
|               | - Confirmation  |                            |
|               +-----------------+                            |
|                        |                                     |
+------------------------+-------------------------------------+
                         |
                         v
              +----------------------+
              |     Kalshi API       |
              |                      |
              | - REST API           |
              | - WebSocket (WS v2) |
              | - Order Book         |
              +----------------------+
```

## Component Responsibilities

### 1. Market Monitor
- Maintains WebSocket connection to Kalshi (authenticated at handshake)
- Receives real-time price updates via ticker, orderbook_delta, trade channels
- Loads initial markets via REST before subscribing to WebSocket
- Filters markets based on criteria (liquidity, probability > 85%)
- Emits market opportunities to Strategy Engine

### 2. Strategy Engine
- Evaluates market opportunities
- Determines entry/exit points
- Calculates order prices (entry, stop loss, take profit)
- Generates trading signals

### 3. Order Manager
- Manages all active orders and positions
- Tracks order lifecycle (pending, resting, executed, canceled)
- Maintains position state
- Coordinates stop loss and take profit orders

### 4. Risk Manager
- Enforces position limits
- Monitors total exposure
- Implements circuit breakers
- Validates all orders before submission

### 5. Execution Engine
- Submits limit orders to Kalshi (no market orders available)
- Handles order responses and confirmations
- Implements retry logic for failed orders
- Uses aggressive pricing for exit orders to simulate market orders

## Data Flow

1. **Market Discovery**
   - REST API fetches initial markets -> MarketMonitor populates state
   - WebSocket receives price updates -> Market Monitor
   - Market Monitor filters by criteria -> Strategy Engine

2. **Signal Generation**
   - Strategy Engine evaluates opportunity -> Risk Manager
   - Risk Manager validates -> Execution Engine

3. **Order Execution**
   - Execution Engine submits LIMIT BUY order -> Kalshi
   - On confirmation -> Submit STOP LOSS and TAKE PROFIT limit orders
   - Order Manager tracks all three orders

4. **Position Management**
   - Price updates trigger order status checks
   - Stop loss or take profit hit -> Close position
   - Update position tracking and P&L

## Concurrency Model

- **Async/Await**: Use asyncio for non-blocking I/O
- **Event-Driven**: React to market updates in real-time
- **Thread Safety**: Protect shared state (positions, orders) with locks
- **Queue-Based**: Order execution queue to prevent race conditions

## Error Handling

- **WebSocket Disconnection**: Auto-reconnect with exponential backoff, fresh auth headers
- **API Failures**: Retry with jitter, max 3 attempts
- **Partial Fills**: Handle fractional order completion
- **Network Issues**: Graceful degradation, preserve state

## Persistence

- **In-Memory**: Active orders and positions (fast access)
- **Database**: Trade history, P&L, audit log (Supabase)
- **Disk**: Configuration, RSA private key (protected)

## Scalability Considerations

- Single market instance initially
- Horizontal scaling potential for multiple markets
- Stateless components where possible
- Centralized state management for positions
