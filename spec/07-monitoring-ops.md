# Monitoring & Operations

## Logging Strategy

### Log Levels

```python
# INFO: Normal operations
logger.info("Position opened", market_id=market_id, size=100, price=0.92)

# WARNING: Potential issues, recoverable
logger.warning("High slippage detected", expected=0.92, actual=0.925, slippage_pct=0.54)

# ERROR: Errors that affect trading but don't crash the system
logger.error("Order submission failed", order_id=order_id, error=str(e))

# CRITICAL: System-level failures
logger.critical("WebSocket disconnected for >30s", reconnect_attempts=5)
```

### Structured Logging

```python
import structlog

# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
)

logger = structlog.get_logger()

# Usage with context
logger.info(
    "position_opened",
    position_id="pos_123",
    market_id="KXHIGHNY-25JAN09-B56.5",
    entry_price=0.92,
    size=100,
    stop_loss=0.9108,
    take_profit=0.9384
)
```

### Log Categories

#### 1. Trading Logs
```python
# Entry
logger.info("signal_generated", market_id, entry_price, confidence)
logger.info("position_entered", position_id, entry_price, size)

# Exit
logger.info("position_closed", position_id, exit_price, pnl, exit_reason)

# Orders
logger.info("order_submitted", order_id, side, price, size)
logger.info("order_filled", order_id, filled_price, filled_size)
logger.info("order_cancelled", order_id, reason)
```

#### 2. Risk Logs
```python
logger.warning("position_limit_approaching", current=4, max=5)
logger.warning("exposure_high", exposure_pct=0.28, max=0.30)
logger.critical("circuit_breaker_triggered", reason="daily_loss_limit", loss=-0.05)
```

#### 3. System Logs
```python
logger.info("websocket_connected", url=ws_url)
logger.warning("websocket_reconnecting", attempt=3, delay=4)
logger.error("api_error", endpoint="/portfolio/orders", status_code=429, message="Rate limited")
```

#### 4. Performance Logs
```python
logger.info("latency_measured", component="order_submission", latency_ms=87)
logger.warning("slow_operation", component="signal_generation", latency_ms=250)
```

## Metrics Collection

### Supabase Metrics Storage

All metrics are logged to Supabase tables for later analysis. No real-time metrics dashboard initially - focus on email alerts and Supabase queries.

```python
import structlog

logger = structlog.get_logger()

class MetricsCollector:
    def __init__(self, db):
        self.db = db
        self.start_time = time.time()

    async def log_trade(self, trade_data: dict):
        """Log completed trade to Supabase"""
        await self.db.insert_trade(trade_data)
        logger.info("trade_logged", trade_id=trade_data['id'])

    async def log_snapshot(self, account_data: dict):
        """Log account snapshot to Supabase"""
        snapshot = {
            "timestamp": datetime.utcnow(),
            "balance": account_data['balance'],
            "open_positions": account_data['open_positions'],
            "daily_pnl": account_data['daily_pnl'],
            "total_pnl": account_data['total_pnl'],
        }
        await self.db.insert_snapshot(snapshot)
        logger.info("snapshot_logged")

    def get_current_state(self, account, positions) -> dict:
        """Get current system state for monitoring"""
        uptime = time.time() - self.start_time

        return {
            "system": {
                "uptime_seconds": uptime,
                "websocket_connected": self.ws_connected,
            },
            "trading": {
                "positions_open": len(positions),
                "total_exposure": sum(p.value for p in positions.values()),
                "available_balance": account.available_balance,
            },
            "performance": {
                "daily_pnl": account.daily_pnl,
                "daily_pnl_pct": account.daily_pnl_pct,
                "total_pnl": account.total_pnl,
                "win_rate": account.win_rate,
            },
        }
```

### Query Metrics from Supabase

```sql
-- Daily P&L
SELECT
  DATE(entry_time) as date,
  COUNT(*) as trades,
  SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
  SUM(net_pnl) as total_pnl,
  AVG(net_pnl) as avg_pnl
FROM trades
GROUP BY DATE(entry_time)
ORDER BY date DESC;

-- Performance by market
SELECT
  market_id,
  COUNT(*) as trades,
  SUM(net_pnl) as total_pnl,
  AVG(net_pnl) as avg_pnl
FROM trades
GROUP BY market_id
ORDER BY total_pnl DESC;

-- Recent errors
SELECT
  timestamp,
  level,
  message,
  data
FROM logs
WHERE level = 'ERROR'
ORDER BY timestamp DESC
LIMIT 50;
```

## Alerting System

### Email Alerts Only

Using email alerts for all notifications (implementation details in 05-technical-implementation.md).

```python
import smtplib
from email.message import EmailMessage
import structlog

logger = structlog.get_logger()

class EmailAlerter:
    def __init__(self, smtp_config: dict):
        self.smtp_host = smtp_config['smtp_host']
        self.smtp_port = smtp_config['smtp_port']
        self.from_email = smtp_config['from_email']
        self.to_email = smtp_config['to_email']
        self.password = smtp_config['password']
        self.enabled = smtp_config.get('enabled', True)

    def send_alert(self, subject: str, body: str, level: str = "INFO"):
        """Send email alert"""
        if not self.enabled:
            return

        try:
            msg = EmailMessage()
            msg['Subject'] = f"[{level}] HFT Bot - {subject}"
            msg['From'] = self.from_email
            msg['To'] = self.to_email
            msg.set_content(body)

            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.login(self.from_email, self.password)
                server.send_message(msg)

            logger.info("email_sent", subject=subject, level=level)
        except Exception as e:
            logger.error("email_failed", error=str(e), subject=subject)
```

### Alert Rules

```python
class AlertManager:
    def __init__(self, email_alerter: EmailAlerter, db):
        self.email = email_alerter
        self.db = db
        self.alert_history = []

    async def check_alerts(self, account: Account, positions: List[Position]):
        # Daily loss approaching limit
        if account.daily_pnl_pct <= -0.03:  # -3%
            await self.send_alert(
                "Daily Loss Warning",
                f"Daily P&L: {account.daily_pnl_pct:.2%} (Limit: -5%)\n"
                f"Current Balance: ${account.current_balance:.2f}\n"
                f"Loss: ${account.daily_pnl:.2f}",
                level="WARNING"
            )

        # Circuit breaker triggered
        if account.daily_pnl_pct <= -0.05:  # -5%
            await self.send_alert(
                "Circuit Breaker Triggered",
                f"Daily loss limit reached. Trading halted.\n\n"
                f"Daily P&L: {account.daily_pnl_pct:.2%}\n"
                f"Loss: ${account.daily_pnl:.2f}\n\n"
                f"Check Supabase logs for details.",
                level="CRITICAL"
            )

        # Position limit approaching
        if len(positions) >= 4:  # Max is 5
            await self.send_alert(
                "Position Limit Approaching",
                f"Open positions: {len(positions)}/5\n"
                f"Total exposure: ${sum(p.value for p in positions.values()):.2f}",
                level="WARNING"
            )

        # WebSocket disconnected
        if not self.ws_connected:
            await self.send_alert(
                "WebSocket Disconnected",
                "Kalshi real-time data feed lost. Attempting reconnection.\n"
                "All positions will be closed if disconnected > 15s.",
                level="CRITICAL"
            )

        # High slippage
        if self.avg_slippage > 0.005:  # 0.5%
            await self.send_alert(
                "High Slippage Detected",
                f"Average slippage: {self.avg_slippage:.2%}\n"
                "Consider adjusting entry strategy.",
                level="WARNING"
            )

    async def send_alert(self, subject: str, body: str, level: str = "INFO"):
        """Send alert and log to database"""
        # Send email
        self.email.send_alert(subject, body, level)

        # Log to Supabase
        await self.db.insert_log({
            'timestamp': datetime.utcnow().isoformat(),
            'level': level,
            'logger': 'alerts',
            'message': subject,
            'data': {'body': body}
        })

        # Track in memory
        self.alert_history.append({
            'timestamp': datetime.utcnow(),
            'level': level,
            'subject': subject,
            'body': body
        })
```

## Monitoring via Supabase

### Supabase Dashboard Queries

Instead of a real-time dashboard, use Supabase SQL queries to monitor performance:

#### Performance Overview
```sql
-- Current day summary
SELECT
  COUNT(*) as trades_today,
  SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
  SUM(net_pnl) as total_pnl,
  AVG(net_pnl) as avg_pnl,
  MAX(net_pnl) as best_trade,
  MIN(net_pnl) as worst_trade
FROM trades
WHERE DATE(entry_time) = CURRENT_DATE;
```

#### Recent Positions
```sql
-- Last 10 trades
SELECT
  market_id,
  entry_price,
  exit_price,
  net_pnl,
  net_pnl / (entry_price * size) * 100 as pnl_pct,
  exit_reason,
  exit_time - entry_time as duration
FROM trades
ORDER BY exit_time DESC
LIMIT 10;
```

#### System Health
```sql
-- Recent errors
SELECT
  timestamp,
  message,
  data
FROM logs
WHERE level IN ('ERROR', 'CRITICAL')
  AND timestamp > NOW() - INTERVAL '1 hour'
ORDER BY timestamp DESC;

-- WebSocket connection status
SELECT
  timestamp,
  message,
  data
FROM logs
WHERE logger = 'websocket'
  AND timestamp > NOW() - INTERVAL '1 hour'
ORDER BY timestamp DESC
LIMIT 20;
```

#### Risk Metrics
```sql
-- Daily P&L history
SELECT
  DATE(entry_time) as date,
  SUM(net_pnl) as daily_pnl,
  COUNT(*) as trades,
  SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)::float / COUNT(*) as win_rate
FROM trades
GROUP BY DATE(entry_time)
ORDER BY date DESC
LIMIT 30;
```

### Simple Health Check Endpoint

Optional simple HTTP endpoint for uptime monitoring:

```python
from aiohttp import web

class HealthCheck:
    def __init__(self, bot):
        self.bot = bot

    async def health(self, request):
        """Simple health check endpoint"""
        health = {
            "status": "healthy" if self.bot.is_healthy() else "unhealthy",
            "exchange": "kalshi",
            "websocket_connected": self.bot.ws_connected,
            "open_positions": len(self.bot.positions),
            "uptime_seconds": time.time() - self.bot.start_time,
            "last_trade": self.bot.last_trade_time.isoformat() if self.bot.last_trade_time else None
        }

        status_code = 200 if health["status"] == "healthy" else 503
        return web.json_response(health, status=status_code)

    def start(self, port=8080):
        """Start health check server"""
        app = web.Application()
        app.router.add_get('/health', self.health)
        web.run_app(app, port=port, access_log=None)
```

## Operational Procedures

### Startup Procedure

1. **Pre-flight Checks**
   ```python
   async def pre_flight_checks():
       # Check account balance via Kalshi API
       balance = await api.get_balance()
       assert balance > MIN_BALANCE, "Insufficient balance"

       # Check API connectivity
       markets = await api.get_markets()
       assert len(markets) > 0, "Cannot fetch markets"

       # Check WebSocket connection
       assert await ws.connect(), "WebSocket connection failed"

       # Load configuration
       config = load_config()
       validate_config(config)

       logger.info("Pre-flight checks passed")
   ```

2. **Initialize Components** (auth, API client, strategy, risk, execution)
3. **Load initial markets via REST**
4. **Connect to WebSocket data feeds**
5. **Verify market access and subscriptions**
6. **Start trading**

### Shutdown Procedure

1. **Stop accepting new signals**
2. **Cancel all entry orders**
3. **Close or preserve positions** (configurable)
4. **Disconnect WebSocket**
5. **Save state to disk**
6. **Generate shutdown report**

```python
async def graceful_shutdown():
    logger.info("Initiating graceful shutdown")

    # Stop accepting new positions
    global TRADING_ENABLED
    TRADING_ENABLED = False

    # Cancel entry orders
    await cancel_all_entry_orders()

    # Handle existing positions
    if config.close_on_shutdown:
        await emergency_exit_all()  # Uses aggressive limit orders
    else:
        logger.info("Preserving open positions", count=len(positions))

    # Disconnect
    await ws.disconnect()

    # Save state
    save_state()

    # Report
    generate_shutdown_report()

    logger.info("Shutdown complete")
```

### Recovery Procedure

```python
async def recover_from_crash():
    """Recover state after unexpected shutdown"""

    # Load saved state
    state = load_state()

    # Verify positions with Kalshi API
    api_positions = await api.get_positions()

    # Reconcile
    for position in state.positions:
        if position.id in api_positions:
            # Position still open, restore tracking
            restore_position(position, api_positions[position.id])
        else:
            # Position was closed, record as such
            mark_position_closed(position)

    # Sync orders
    api_orders = await api.get_active_orders()
    sync_orders(state.orders, api_orders)

    logger.info("Recovery complete")
```

## Maintenance Tasks

### Daily Tasks
- Review P&L and performance
- Check alert history
- Verify database integrity
- Review log files for errors
- Update position sizing based on new balance

### Weekly Tasks
- Analyze strategy performance
- Review and adjust risk parameters
- Check for Kalshi API/system updates
- Backup trade history database
- Review and optimize code

### Monthly Tasks
- Full performance review
- Strategy backtesting with recent data
- Infrastructure review
- Security audit (RSA keys, access)
- Cost analysis (fees, infrastructure)

## Monitoring Checklist

### System Health
- [ ] WebSocket connection active (Kalshi WS v2)
- [ ] Kalshi API responding (< 500ms)
- [ ] No rate limiting (< 80% of read/write limits)
- [ ] Disk space available (> 20%)
- [ ] Memory usage normal (< 80%)
- [ ] CPU usage normal (< 70%)

### Trading Health
- [ ] Positions within limits
- [ ] Orders being filled (> 70% fill rate)
- [ ] Slippage acceptable (< 0.5%)
- [ ] No circuit breakers triggered
- [ ] Win rate on target (> 50%)
- [ ] Daily P&L within expectations

### Risk Health
- [ ] No position over 10% of capital
- [ ] Total exposure < 30%
- [ ] Daily loss < 5%
- [ ] Stop losses active on all positions
- [ ] No stuck orders (> 1 hour old)
