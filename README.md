# Kalshi HFT Trading Bot

High-frequency trading bot for Kalshi prediction markets with automated risk management.

## Features

- Real-time market monitoring via WebSocket
- Automated entry/exit with stop loss and take profit
- Comprehensive risk management with circuit breakers
- Position tracking and P&L monitoring
- Email alerts for critical events
- Supabase logging and analytics

## Project Status

Currently in development. See implementation plan for progress.

## Setup

### Prerequisites

- Python 3.11+
- Poetry
- Supabase account
- Kalshi account with API access

### Installation

1. Install dependencies:
```bash
poetry install
```

2. Configure secrets:
```bash
cp config/secrets.env.example config/secrets.env
# Edit config/secrets.env with your credentials
```

3. Configure strategy (optional):
```bash
# Edit config/config.yaml to adjust trading parameters
```

### Running

```bash
poetry run python src/main.py
```

## Configuration

- `config/config.yaml` - Strategy parameters, risk limits, and system settings
- `config/secrets.env` - API keys and sensitive credentials (not committed)

## Project Structure

```
src/
├── main.py              # Application entry point
├── config.py            # Configuration management
├── market/              # Market monitoring (WebSocket)
├── strategy/            # Trading strategy and signals
├── execution/           # Order execution and position management
├── risk/                # Risk management and circuit breakers
├── api/                 # Kalshi API client
├── db/                  # Database and data models
└── utils/               # Utilities (logging, email)
```

## Safety Features

- Circuit breakers for daily loss, consecutive losses, and API errors
- Position size limits (per position and total exposure)
- Automatic stop loss and take profit orders
- Email alerts for critical events
- Comprehensive logging to Supabase

## Development

Run tests:
```bash
poetry run pytest
```

Run linter:
```bash
poetry run ruff check src/
```

Format code:
```bash
poetry run black src/
```

## License

MIT
