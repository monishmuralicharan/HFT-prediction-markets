# Kalshi Integration

## API Overview

Kalshi is a CFTC-regulated prediction market exchange with REST and WebSocket APIs. All orders are limit orders (no market orders). Prices are in integer cents (1–99) on the API but converted to Decimal dollars (0.01–0.99) at the client boundary.

### Base URLs
- **REST API (Production)**: `https://trading-api.kalshi.com/trade-api/v2`
- **REST API (Demo)**: `https://demo-api.kalshi.co/trade-api/v2`
- **WebSocket (Production)**: `wss://trading-api.kalshi.com/trade-api/ws/v2`
- **WebSocket (Demo)**: `wss://demo-api.kalshi.co/trade-api/ws/v2`

## Authentication

### RSA-PSS Per-Request Signing
- RSA private key (PEM format) required for all API requests
- Each request signed with RSA-PSS (SHA-256) using `timestamp_ms + METHOD + path`
- No per-order signing — authentication is at the HTTP request level

### Required Components
- Kalshi API Key ID (from Kalshi account settings)
- RSA private key file (PEM format, generated locally)
- `cryptography` Python library for signing

### Auth Header Generation
```python
# Sign: timestamp_ms (string) + HTTP_METHOD + request_path
message = f"{timestamp_ms}{method}{path}"
signature = private_key.sign(
    message.encode(),
    padding.PSS(
        mgf=padding.MGF1(hashes.SHA256()),
        salt_length=padding.PSS.MAX_LENGTH,
    ),
    hashes.SHA256(),
)

headers = {
    "KALSHI-ACCESS-KEY": api_key_id,
    "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    "KALSHI-ACCESS-TIMESTAMP": str(timestamp_ms),
}
```

## WebSocket Integration

### Connection Setup
- Authentication happens at handshake via `extra_headers`
- Headers are generated fresh on each (re)connect for valid timestamps
- Uses `cmd`-based message format with incremental message IDs

### Subscribe Message
```json
{
  "id": 1,
  "cmd": "subscribe",
  "params": {
    "channels": ["orderbook_delta", "ticker", "trade"],
    "market_tickers": ["KXHIGHNY-25JAN09-B56.5", "KXBTC-26FEB09-B95000"]
  }
}
```

### Unsubscribe Message
```json
{
  "id": 2,
  "cmd": "unsubscribe",
  "params": {
    "channels": ["ticker"],
    "market_tickers": ["KXHIGHNY-25JAN09-B56.5"]
  }
}
```

### Message Types

#### 1. Ticker Update
```json
{
  "type": "ticker",
  "msg": {
    "market_ticker": "KXHIGHNY-25JAN09-B56.5",
    "yes_price": 92,
    "yes_bid": 91,
    "yes_ask": 93,
    "volume": 15000,
    "open_interest": 8500
  }
}
```

#### 2. Order Book Delta
```json
{
  "type": "orderbook_delta",
  "msg": {
    "market_ticker": "KXHIGHNY-25JAN09-B56.5",
    "yes": [[91, 250], [90, 500]],
    "no": [[9, 150], [10, 300]]
  }
}
```

#### 3. Trade
```json
{
  "type": "trade",
  "msg": {
    "market_ticker": "KXHIGHNY-25JAN09-B56.5",
    "yes_price": 92,
    "count": 50,
    "taker_side": "yes",
    "ts": 1234567890000
  }
}
```

#### 4. Fill (Own Trades)
```json
{
  "type": "fill",
  "msg": {
    "order_id": "abc-123",
    "market_ticker": "KXHIGHNY-25JAN09-B56.5",
    "yes_price": 92,
    "count": 10,
    "side": "yes",
    "is_taker": true
  }
}
```

#### 5. Order Update (Own Orders)
```json
{
  "type": "order_update",
  "msg": {
    "order_id": "abc-123",
    "market_ticker": "KXHIGHNY-25JAN09-B56.5",
    "status": "resting",
    "remaining_count": 90
  }
}
```

### Subscription Strategy
- Load initial markets via REST API before subscribing
- Subscribe to `orderbook_delta`, `ticker`, `trade` for market data
- Subscribe to `fill`, `order_update` for own order tracking
- Filter by minimum liquidity threshold and probability > 85%

## REST API Endpoints

### Market Data

#### Get Markets (Paginated)
```
GET /markets?status=open&cursor=<cursor>
Response: {
  "markets": [...],
  "cursor": "next_page_cursor"
}
```

#### Get Order Book
```
GET /markets/{ticker}/orderbook
Response: {
  "orderbook": {
    "yes": [[price_cents, size], ...],
    "no": [[price_cents, size], ...]
  }
}
```
Note: Prices are in cents. The client converts to dollars.

#### Get Market Info
```
GET /markets/{ticker}
Response: {
  "market": {
    "ticker": "KXHIGHNY-25JAN09-B56.5",
    "event_ticker": "KXHIGHNY-25JAN09",
    "series_ticker": "KXHIGHNY",
    "subtitle": "...",
    "yes_bid": 91,
    "yes_ask": 93,
    "volume": 15000,
    ...
  }
}
```

### Order Management

#### Create Order
```
POST /portfolio/orders
Body: {
  "ticker": "KXHIGHNY-25JAN09-B56.5",
  "side": "yes",
  "type": "limit",
  "count": 100,
  "yes_price": 92
}
```
Notes:
- `side` is lowercase (`"yes"` or `"no"`)
- `type` is always `"limit"` (no market orders on Kalshi)
- `count` is number of contracts (integer)
- `yes_price` is in cents (integer, 1–99)

#### Cancel Order
```
DELETE /portfolio/orders/{order_id}
```
Returns 204 No Content on success — do not parse response body.

#### Get Order Status
```
GET /portfolio/orders/{order_id}
Response: { "order": { "status": "resting", ... } }
```

#### Get Active Orders
```
GET /portfolio/orders?status=resting
Response: { "orders": [...], "cursor": "..." }
```

### Account

#### Get Balance
```
GET /portfolio/balance
Response: { "balance": 10000 }
```
Note: Balance is in cents. The client converts to dollars.

## Order Types

### Limit Order (Only Type Available)
- Specify exact price in cents
- Can be maker or taker depending on match
- Used for all orders: entry, stop loss, and take profit

### Aggressive Limit for Exits
Since Kalshi has no market orders, exit orders use aggressive pricing to simulate immediate fills:
- **Stop Loss**: Price at `entry_price * 0.95` (well below market for guaranteed fill)
- **Take Profit**: Standard limit at target price
- The aggressive pricing ensures exit orders fill promptly

## Rate Limits

### Dual Rate Limiters
- **Read operations** (GET): 20 requests/second
- **Write operations** (POST, DELETE): 10 requests/second
- Separate tracking for each bucket

### WebSocket
- Standard connection limits apply
- Subscribe to relevant markets only (not all)

### Rate Limit Handling
- 429 status code triggers exponential backoff
- Pre-request rate limiting to stay under limits
- Separate `RateLimiter` instances for read and write

## Error Codes

| Code | Meaning | Action |
|------|---------|--------|
| 400 | Invalid request | Validate parameters |
| 401 | Unauthorized | Check RSA signature/key |
| 403 | Forbidden | Check permissions |
| 404 | Not found | Verify ticker/order ID |
| 429 | Rate limited | Exponential backoff |
| 500 | Server error | Retry with jitter |
| 503 | Service unavailable | Wait and reconnect |

## Order Lifecycle

```
PENDING → RESTING → EXECUTED
              ↓
          CANCELED
```

### Status Mapping (Kalshi → Internal)
| Kalshi Status | Internal Status |
|---------------|----------------|
| `resting` | OPEN |
| `canceled` | CANCELLED |
| `executed` | FILLED |

### State Transitions
1. **PENDING**: Order submitted to API
2. **RESTING**: Accepted and on order book
3. **EXECUTED**: Fully filled
4. **CANCELED**: Cancelled by user or system

## Market Selection Criteria

### Market Identifiers
Markets on Kalshi use ticker strings (not UUIDs):
- **Market ticker**: `KXHIGHNY-25JAN09-B56.5` (specific contract)
- **Event ticker**: `KXHIGHNY-25JAN09` (event grouping)
- **Series ticker**: `KXHIGHNY` (series grouping)

### Price Criteria
- Yes price >= 85 cents ($0.85, 85% probability)
- Sufficient liquidity at price level
- Spread < 2% (ensures profitable exit)

### Filtering
```python
{
    "status": "open",
    "yes_bid": {"$gte": 85},
    "volume": {"$gte": 10000}
}
```

## Contract Count Conversion

### Dollars to Contracts
```python
# Convert dollar position size to contract count
# count = int(dollar_size / dollar_price)
# Example: $920 position at $0.92/contract = 1000 contracts
count = int(Decimal("920") / Decimal("0.92"))  # = 1000
```

### Cents ↔ Dollars Boundary
- **Inside KalshiClient**: Converts between cents (API) and dollars (internal)
- **Rest of system**: Always operates in Decimal dollars (0.01–0.99)
- This keeps strategy, risk, and execution logic clean

## WebSocket Reconnection Strategy

1. **Detect Disconnect**: Connection closed or ping timeout
2. **Exponential Backoff**: 1s, 2s, 4s, 8s, 16s, 30s (max)
3. **Fresh Auth**: Generate new auth headers on each reconnect (fresh timestamp)
4. **Re-subscribe**: Re-subscribe to all tracked market tickers
5. **Order Sync**: Fetch active orders from REST API to reconcile state

## Monitoring Requirements

- WebSocket connection health (heartbeat/ping-pong)
- Message latency (target < 50ms)
- Order acknowledgment time (target < 100ms)
- API error rate (alert > 5%)
- Rate limit proximity (alert > 80% of each bucket)
- Auth signature validity (check for clock drift)
