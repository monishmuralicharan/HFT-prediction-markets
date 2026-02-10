# Technical Implementation

## Technology Stack

### Core Language
**Python 3.11+**
- Async/await support (asyncio)
- Type hints for safety
- Rich ecosystem for trading
- Fast prototyping

### Key Libraries

#### WebSocket & Networking
- `websockets` - WebSocket client
- `aiohttp` - Async HTTP client

#### Authentication & Cryptography
- `cryptography` - RSA-PSS signing for Kalshi API authentication

#### Data & Computation
- `pydantic` - Data validation (v2)
- `pydantic-settings` - Environment-based configuration

#### Database & Persistence
- `supabase` - Supabase client library (managed PostgreSQL)

#### Monitoring & Logging
- `structlog` - Structured logging
- `supabase` - Log storage in Supabase tables

#### Testing
- `pytest` - Test framework
- `pytest-asyncio` - Async test support
- `pytest-mock` - Mocking

### Infrastructure

#### Development
- **OS**: Linux/macOS (Docker optional)
- **Python Environment**: pyenv + poetry
- **IDE**: VS Code with Python extensions

#### Production
- **Hosting**: AWS us-east-1 (EC2 for compute, close to Kalshi servers)
- **Process Manager**: systemd or AWS ECS (containerized)
- **Database**: Supabase (managed PostgreSQL)
- **Logging**: Supabase tables (structured logs)
- **Alerting**: Email via SMTP (Gmail/SES)

## Project Structure

```
HFT-prediction-markets/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point
│   ├── config.py               # Configuration management
│   │
│   ├── market/
│   │   ├── __init__.py
│   │   ├── monitor.py          # Market Monitor component
│   │   ├── websocket.py        # WebSocket connection handler
│   │   └── filters.py          # Market filtering logic
│   │
│   ├── strategy/
│   │   ├── __init__.py
│   │   ├── engine.py           # Strategy Engine
│   │   ├── signals.py          # Signal generation
│   │   └── exits.py            # Exit logic
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   ├── engine.py           # Execution Engine
│   │   ├── order_manager.py    # Order Manager
│   │   └── position_tracker.py # Position tracking
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   ├── manager.py          # Risk Manager
│   │   ├── validators.py       # Order validation
│   │   └── circuit_breakers.py # Circuit breakers
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── kalshi.py           # Kalshi API client
│   │   ├── auth.py             # RSA-PSS authentication
│   │   └── models.py           # API data models
│   │
│   ├── db/
│   │   ├── __init__.py
│   │   ├── models.py           # Database models
│   │   └── repository.py       # Data access layer
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py          # Logging setup
│       ├── metrics.py          # Metrics collection
│       └── helpers.py          # Utility functions
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
│
├── config/
│   ├── config.yaml             # Main configuration
│   ├── markets.yaml            # Market filters
│   └── secrets.env             # Credentials (gitignored)
│
├── deploy/
│   ├── hft-bot.service         # systemd service file
│   └── setup_ec2.sh            # EC2 setup script
│
├── spec/                        # This directory
│   └── *.md
│
├── migrations/
│   └── 001_initial_schema.sql  # Database schema
│
├── pyproject.toml              # Poetry dependencies
├── Dockerfile
└── README.md
```

## Core Components Implementation

### 1. Main Application Loop

```python
# src/main.py
import asyncio
import structlog
from api.auth import KalshiAuth
from api.kalshi import KalshiClient
from market.monitor import MarketMonitor
from strategy.engine import StrategyEngine
from execution.engine import ExecutionEngine
from risk.manager import RiskManager

logger = structlog.get_logger()

async def main():
    logger.info("Starting Kalshi HFT Bot")

    # Initialize auth and API client
    auth = KalshiAuth(
        key_id=config.secrets.kalshi_api_key_id,
        private_key_path=config.secrets.kalshi_private_key_path,
    )
    api_client = KalshiClient(
        base_url=config.api.get_api_base_url(),
        auth=auth,
        read_rate_limit=config.api.read_rate_limit_per_second,
        write_rate_limit=config.api.write_rate_limit_per_second,
    )

    # Initialize components
    risk_manager = RiskManager()
    execution_engine = ExecutionEngine(risk_manager, api_client)
    strategy_engine = StrategyEngine(execution_engine, risk_manager)
    market_monitor = MarketMonitor(strategy_engine, auth=auth)

    # Load initial markets via REST before WebSocket
    await market_monitor.load_initial_markets(api_client)

    # Run all components concurrently
    await asyncio.gather(
        market_monitor.run(),
        execution_engine.run(),
        risk_manager.run(),
    )

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. WebSocket Handler

```python
# src/market/websocket.py
import asyncio
import websockets
import json
import structlog
from typing import Optional, Callable

logger = structlog.get_logger()

class KalshiWebSocket:
    def __init__(self, url: str, on_message_callback,
                 extra_headers: Optional[Callable[[], dict[str, str]]] = None):
        self.url = url
        self.on_message = on_message_callback
        self.extra_headers = extra_headers
        self.ws = None
        self.reconnect_delay = 1
        self._msg_id_counter = 0

    async def connect(self):
        while True:
            try:
                headers = self.extra_headers() if self.extra_headers else {}
                self.ws = await websockets.connect(
                    self.url,
                    additional_headers=headers
                )
                logger.info("WebSocket connected")
                self.reconnect_delay = 1

                await self._listen()

            except Exception as e:
                logger.error("websocket_error", error=str(e))
                await asyncio.sleep(self.reconnect_delay)
                self.reconnect_delay = min(self.reconnect_delay * 2, 30)

    async def _listen(self):
        async for message in self.ws:
            data = json.loads(message)
            await self.on_message(data)

    async def subscribe(self, channels: list[str], market_tickers: list[str]):
        self._msg_id_counter += 1
        subscribe_msg = {
            "id": self._msg_id_counter,
            "cmd": "subscribe",
            "params": {
                "channels": channels,
                "market_tickers": market_tickers,
            }
        }
        await self.ws.send(json.dumps(subscribe_msg))

    async def unsubscribe(self, channels: list[str], market_tickers: list[str]):
        self._msg_id_counter += 1
        unsubscribe_msg = {
            "id": self._msg_id_counter,
            "cmd": "unsubscribe",
            "params": {
                "channels": channels,
                "market_tickers": market_tickers,
            }
        }
        await self.ws.send(json.dumps(unsubscribe_msg))
```

### 3. Order Manager

```python
# src/execution/order_manager.py
from dataclasses import dataclass
from enum import Enum
from typing import Optional
import structlog

logger = structlog.get_logger()

class OrderStatus(Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"

@dataclass
class Order:
    id: Optional[str]
    market_id: str
    side: str  # "yes" or "no"
    price: float
    size: float
    status: OrderStatus
    filled_size: float = 0.0

class OrderManager:
    def __init__(self):
        self.active_orders = {}
        self.positions = {}

    async def submit_order(self, order: Order) -> str:
        """Submit order to Kalshi"""
        order_id = await self.api.submit_order(order)
        order.id = order_id
        self.active_orders[order_id] = order
        return order_id

    async def cancel_order(self, order_id: str):
        """Cancel an active order"""
        await self.api.cancel_order(order_id)
        if order_id in self.active_orders:
            self.active_orders[order_id].status = OrderStatus.CANCELLED
            del self.active_orders[order_id]

    async def update_order_status(self, order_id: str, status: dict):
        """Update order status from API/WebSocket"""
        if order_id in self.active_orders:
            order = self.active_orders[order_id]
            order.status = OrderStatus(status['status'])
            order.filled_size = status.get('filled_size', 0)

            if order.status == OrderStatus.FILLED:
                await self._handle_fill(order)

    async def _handle_fill(self, order: Order):
        """Process filled order"""
        logger.info("order_filled", order_id=order.id)
        # Update positions
        # Trigger related orders (stop loss, take profit)
```

### 4. Position Tracker

```python
# src/execution/position_tracker.py
from dataclasses import dataclass
from datetime import datetime
import structlog

logger = structlog.get_logger()

@dataclass
class Position:
    market_id: str
    entry_price: float
    size: float
    entry_time: datetime
    entry_order_id: str
    stop_loss_order_id: Optional[str] = None
    take_profit_order_id: Optional[str] = None

    @property
    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.size

    @property
    def pnl_pct(self, current_price: float) -> float:
        return (current_price - self.entry_price) / self.entry_price

class PositionTracker:
    def __init__(self):
        self.positions = {}

    def add_position(self, position: Position):
        self.positions[position.market_id] = position

    def remove_position(self, market_id: str):
        if market_id in self.positions:
            del self.positions[market_id]

    def get_total_exposure(self) -> float:
        return sum(p.entry_price * p.size for p in self.positions.values())

    def get_unrealized_pnl(self, market_prices: dict) -> float:
        total_pnl = 0
        for market_id, position in self.positions.items():
            if market_id in market_prices:
                total_pnl += position.unrealized_pnl(market_prices[market_id])
        return total_pnl
```

## Configuration Management

### config/config.yaml
```yaml
# Trading parameters
trading:
  entry_threshold: 0.85  # 85% minimum probability
  profit_target: 0.02    # 2% profit
  stop_loss: 0.01        # 1% stop loss
  max_position_size: 0.10  # 10% of capital
  max_total_exposure: 0.30  # 30% of capital
  max_positions: 5

# Market filters
markets:
  categories:
    - all
  min_volume: 10000
  min_liquidity: 500
  max_spread: 0.02
  time_to_event:
    min_hours: 1
    max_hours: 24

# Risk management
risk:
  max_daily_loss: 0.05  # 5%
  max_consecutive_losses: 5
  circuit_breaker_enabled: true
  websocket_max_disconnect: 15  # seconds

# API configuration (Kalshi)
api:
  base_url: "https://trading-api.kalshi.com/trade-api/v2"
  websocket_url: "wss://trading-api.kalshi.com/trade-api/ws/v2"
  demo_base_url: "https://demo-api.kalshi.co/trade-api/v2"
  demo_websocket_url: "wss://demo-api.kalshi.co/trade-api/ws/v2"
  use_demo: true
  read_rate_limit_per_second: 20
  write_rate_limit_per_second: 10
  timeout: 5

# Database (Supabase)
database:
  url: "${SUPABASE_URL}"
  key: "${SUPABASE_KEY}"
  pool_size: 10

# Logging
logging:
  level: INFO
  format: json
  destination: supabase  # Logs stored in Supabase

# Email Alerts
alerts:
  email:
    enabled: true
    smtp_host: "smtp.gmail.com"
    smtp_port: 587
    from_email: "${ALERT_EMAIL_FROM}"
    to_email: "${ALERT_EMAIL_TO}"
    password: "${ALERT_EMAIL_PASSWORD}"
```

## Deployment

### Docker Container
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY pyproject.toml poetry.lock ./
RUN pip install poetry && poetry install --only main

# Copy application
COPY src/ ./src/
COPY config/ ./config/

# Kalshi HFT Trading Bot
CMD ["poetry", "run", "python", "src/main.py"]
```

### AWS EC2 Deployment

#### EC2 Instance Setup
```bash
# Launch EC2 instance
# - Instance type: t3.small (2 vCPU, 2GB RAM)
# - AMI: Amazon Linux 2023 or Ubuntu 22.04
# - Region: us-east-1 (close to Kalshi infrastructure)
# - Security group: Allow outbound HTTPS/WSS only

# Install dependencies
sudo yum update -y
sudo yum install python3.11 git docker -y

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Clone repository
git clone <your-repo-url>
cd HFT-prediction-markets

# Set up environment variables
cp config/secrets.env.example config/secrets.env
# Edit secrets.env with your credentials
```

#### Environment Variables (config/secrets.env)
```bash
# Kalshi API credentials
KALSHI_API_KEY_ID=your-api-key-id
KALSHI_PRIVATE_KEY_PATH=/path/to/your/kalshi-private-key.pem

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key

# Email Alerts
ALERT_EMAIL_FROM=your-bot@gmail.com
ALERT_EMAIL_TO=your-email@gmail.com
ALERT_EMAIL_PASSWORD=your-app-password
```

#### Systemd Service (for non-Docker deployment)
```ini
# /etc/systemd/system/hft-bot.service
[Unit]
Description=Kalshi HFT Trading Bot
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/HFT-prediction-markets
EnvironmentFile=/home/ec2-user/HFT-prediction-markets/config/secrets.env
ExecStart=/usr/local/bin/poetry run python src/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Start Service
```bash
# Using systemd
sudo systemctl enable hft-bot
sudo systemctl start hft-bot
sudo systemctl status hft-bot

# Or using Docker
docker-compose up -d
docker-compose logs -f
```

### Supabase Setup

#### Database Schema
```sql
-- Trades table
CREATE TABLE trades (
    id TEXT PRIMARY KEY,
    position_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    entry_price DECIMAL(10, 6) NOT NULL,
    exit_price DECIMAL(10, 6) NOT NULL,
    size DECIMAL(18, 6) NOT NULL,
    entry_time TIMESTAMP NOT NULL,
    exit_time TIMESTAMP NOT NULL,
    gross_pnl DECIMAL(18, 6) NOT NULL,
    net_pnl DECIMAL(18, 6) NOT NULL,
    exit_reason TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Account snapshots
CREATE TABLE account_snapshots (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    balance DECIMAL(18, 6) NOT NULL,
    open_positions INTEGER NOT NULL,
    daily_pnl DECIMAL(18, 6) NOT NULL,
    total_pnl DECIMAL(18, 6) NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Application logs
CREATE TABLE logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    level TEXT NOT NULL,
    logger TEXT,
    message TEXT NOT NULL,
    data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create indexes
CREATE INDEX idx_trades_market ON trades(market_id);
CREATE INDEX idx_trades_entry_time ON trades(entry_time);
CREATE INDEX idx_logs_timestamp ON logs(timestamp);
CREATE INDEX idx_logs_level ON logs(level);
```

#### Supabase Client Implementation
```python
# src/db/supabase_client.py
from supabase import create_client, Client
import os

class SupabaseDB:
    def __init__(self):
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_KEY")
        self.client: Client = create_client(url, key)

    async def insert_trade(self, trade: dict):
        """Insert completed trade"""
        return self.client.table('trades').insert(trade).execute()

    async def insert_log(self, log: dict):
        """Insert application log"""
        return self.client.table('logs').insert(log).execute()

    async def insert_snapshot(self, snapshot: dict):
        """Insert account snapshot"""
        return self.client.table('account_snapshots').insert(snapshot).execute()

    async def get_daily_trades(self, date: str):
        """Get trades for a specific day"""
        return self.client.table('trades')\
            .select('*')\
            .gte('entry_time', f"{date}T00:00:00")\
            .lt('entry_time', f"{date}T23:59:59")\
            .execute()
```

#### Logging to Supabase
```python
# src/utils/logging.py
import structlog
from datetime import datetime
from db.supabase_client import SupabaseDB

class SupabaseLogProcessor:
    def __init__(self):
        self.db = SupabaseDB()

    def __call__(self, logger, method_name, event_dict):
        """Process log and send to Supabase"""
        log_entry = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': event_dict.get('level', 'INFO').upper(),
            'logger': event_dict.get('logger', 'app'),
            'message': event_dict.get('event', ''),
            'data': {k: v for k, v in event_dict.items()
                    if k not in ['level', 'logger', 'event', 'timestamp']}
        }

        # Send to Supabase (non-blocking)
        try:
            self.db.insert_log(log_entry)
        except Exception as e:
            # Fallback to console if Supabase fails
            print(f"Failed to log to Supabase: {e}")

        return event_dict

# Configure structlog with Supabase
structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        SupabaseLogProcessor(),
        structlog.processors.JSONRenderer()
    ]
)
```

### Email Alerting Implementation

```python
# src/utils/email_alerts.py
import smtplib
from email.message import EmailMessage
from typing import Optional
import structlog

logger = structlog.get_logger()

class EmailAlerter:
    def __init__(self, config: dict):
        self.smtp_host = config['smtp_host']
        self.smtp_port = config['smtp_port']
        self.from_email = config['from_email']
        self.to_email = config['to_email']
        self.password = config['password']
        self.enabled = config.get('enabled', True)

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

    def alert_circuit_breaker(self, reason: str, details: dict):
        """Alert when circuit breaker triggers"""
        subject = "Circuit Breaker Triggered"
        body = f"""
Circuit Breaker Alert

Reason: {reason}

Details:
{chr(10).join(f'  {k}: {v}' for k, v in details.items())}

The bot has stopped trading. Please review the logs in Supabase.
        """
        self.send_alert(subject, body, level="CRITICAL")

    def alert_daily_summary(self, stats: dict):
        """Send daily performance summary"""
        subject = "Daily Trading Summary"
        body = f"""
Daily Performance Report

Date: {stats['date']}

P&L: ${stats['daily_pnl']:.2f} ({stats['daily_pnl_pct']:.2%})
Trades: {stats['total_trades']}
Win Rate: {stats['win_rate']:.1%}
Balance: ${stats['ending_balance']:.2f}

Largest Win: ${stats['largest_win']:.2f}
Largest Loss: ${stats['largest_loss']:.2f}

Open Positions: {stats['open_positions']}
        """
        self.send_alert(subject, body, level="INFO")

    def alert_position_opened(self, position: dict):
        """Alert when new position opened"""
        subject = f"Position Opened - {position['market_id'][:20]}..."
        body = f"""
New Position

Market: {position['market_id']}
Entry: ${position['entry_price']:.4f}
Size: {position['size']}
Value: ${position['value']:.2f}

Stop Loss: ${position['stop_loss']:.4f}
Take Profit: ${position['take_profit']:.4f}
        """
        self.send_alert(subject, body, level="INFO")

    def alert_position_closed(self, position: dict):
        """Alert when position closed"""
        subject = f"Position Closed - {position['exit_reason']}"
        body = f"""
Position Closed

Market: {position['market_id']}
Entry: ${position['entry_price']:.4f}
Exit: ${position['exit_price']:.4f}
Size: {position['size']}

P&L: ${position['pnl']:.2f} ({position['pnl_pct']:.2%})
Reason: {position['exit_reason']}
Duration: {position['duration']}
        """
        level = "INFO" if position['pnl'] > 0 else "WARNING"
        self.send_alert(subject, body, level=level)
```

## Performance Optimization

### Latency Targets
- **WebSocket message processing**: < 10ms
- **Order decision**: < 50ms
- **Order submission**: < 100ms
- **End-to-end (signal to order)**: < 200ms

### Optimization Techniques
1. **Async I/O**: Non-blocking operations
2. **Connection Pooling**: Reuse HTTP connections
3. **Local Caching**: Cache market metadata
4. **Batch Processing**: Group API calls where possible
5. **Precomputation**: Pre-calculate common values

### Monitoring Latency
```python
import time
from functools import wraps
import structlog

logger = structlog.get_logger()

def measure_latency(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        latency = (time.perf_counter() - start) * 1000  # ms

        # Log to Supabase via structlog
        logger.info("latency_measured",
                   component=func.__name__,
                   latency_ms=latency)

        if latency > 200:  # Alert if slow
            logger.warning("slow_operation",
                          component=func.__name__,
                          latency_ms=latency)

        return result
    return wrapper
```

## Testing Strategy

### Unit Tests
- Test each component in isolation
- Mock external dependencies (API, WebSocket)
- Test edge cases and error conditions

### Integration Tests
- Test component interactions
- Use Kalshi demo API for integration tests
- Verify order lifecycle

### Simulation Testing
- Replay historical market data
- Test strategy performance
- Measure latency under load

### Production Testing
- Paper trading mode (no real orders)
- Small position sizes initially on demo
- Gradual ramp-up to production
