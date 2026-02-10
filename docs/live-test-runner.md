# Live Test Runner

`testing/live_test.py` — run the full trading pipeline locally against live Kalshi market data.

## Quick Start

```bash
# Paper trade a single market
python3.11 testing/live_test.py KXNCAAMBGAME-26JAN29SFPACHS-SFPA

# Paper trade all markets in an event
python3.11 testing/live_test.py --event KXNCAAMBGAME

# From a Kalshi URL
python3.11 testing/live_test.py https://kalshi.com/markets/kxncaambgame/.../ticker

# Live mode (real orders — requires confirmation)
python3.11 testing/live_test.py TICKER --live
```

## Modes

| Mode | Execution | Balance | Safety |
|------|-----------|---------|--------|
| **Paper** (default) | `PaperExecutionEngine` — simulated fills | `--balance` flag (default $1000) | None needed |
| **Live** (`--live`) | `ExecutionEngine` — real Kalshi API orders | Fetched from Kalshi | Red warning + "type yes" prompt |

## CLI Flags

| Flag | Default | Description |
|------|---------|-------------|
| `tickers` (positional) | — | Market ticker(s), event ticker(s), or Kalshi URLs |
| `--event EVENT` | — | Expand an event ticker into its sub-markets |
| `--mode paper\|live` | `paper` | Trading mode |
| `--live` | — | Shorthand for `--mode live` |
| `--balance N` | `1000` | Starting paper balance in dollars |
| `--threshold N` | `0.85` (from config.yaml) | Minimum probability for entry |
| `--take-profit N` | `0.02` | Take profit percentage |
| `--stop-loss N` | `0.01` | Stop loss percentage |
| `--max-hold N` | `2` | Maximum position hold time in hours |

## Architecture

```
WebSocket (live market data, cents)
    |
    v
OrderbookState + ticker updates
    |
    v
update_market_from_ws() --> Market model (dollars)
    |
    v
On every market update, three things run in order:
    |
    |-- 1. check_fills()         Check if pending orders should fill
    |-- 2. maybe_generate_signal()   StrategyEngine + RiskManager --> new entry?
    |-- 3. check_strategy_exits()    Every 3s: timeout/market-close exits
    |
    v
render_dashboard()  (throttled to ~3x/sec)
```

## Paper Fill Simulation

Orders don't fill instantly. They rest as pending until the live market price crosses them, matching real limit order behavior:

- **BUY LIMIT at price P**: fills when `yes_ask <= P`
- **SELL LIMIT at price P**: fills when `yes_bid >= P`
- **Entry timeout**: 60 seconds, then auto-cancelled
- **Exit timeout**: 300 seconds

### Order lifecycle

1. `StrategyEngine.evaluate_market()` generates a `TradingSignal`
2. `RiskManager.validate_signal()` checks circuit breakers and position limits
3. `PaperExecutionEngine.execute_signal()` submits a pending BUY LIMIT order
4. On each market update, `check_fills()` checks if ask has dropped to the order price
5. When the entry fills:
   - A `Position` is created and added to `PositionTracker`
   - Funds are locked in `Account`
   - Two SELL LIMIT orders are submitted: stop-loss and take-profit (also pending)
6. When an exit fills:
   - The other exit order is cancelled (SL if TP filled, or vice versa)
   - Position is closed, funds unlocked, P&L recorded
7. Every 3 seconds, `check_strategy_exits()` handles TIMEOUT and MARKET_CLOSED exits

## Dashboard Sections

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  KALSHI LIVE TEST   14:32:05 UTC   PAPER MODE   123 msgs   5 trades
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

1. **Market Data** — bid/ask/last/spread, orderbook depth (3 levels)
2. **Pending Orders** — BUY/SELL orders waiting to fill, with current market price and age
3. **Active Positions** — entry price, current price, size, unrealized P&L, hold time
4. **Signal Log** — generated signals with confidence, pass/fail, fill/timeout events
5. **Bot Trades** — completed OPEN/CLOSE trades with P&L
6. **Account** — balance, daily P&L, exposure, win/loss counters

## Stopping & Session Summary

Press **Ctrl+C** to stop. Instead of just quitting, the runner prints a full session summary:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  SESSION SUMMARY
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Mode: PAPER   Duration: 12m   Messages: 1,234
  Markets: SFPA, SFPB

  PERFORMANCE
  Starting balance:  $  1,000.00
  Ending balance:    $  1,004.30
  Return:                 +0.43%
  Realized P&L:      $     +2.30
  Unrealized P&L:    $     +2.00
  Total P&L:         $     +4.30

  STATISTICS
  Total trades:      2
  Wins / Losses:     1 / 1
  Win rate:          50.0%
  Best trade:        $+1.74
  Worst trade:       $-0.90
  Signals generated: 5
  Pending orders:    0
  Open positions:    1

  CLOSED TRADES
    Ticker     Entry      Exit       Size        P&L        Reason    Hold
       WIN  $   0.87  $   0.89  $  100.00  $   +1.74   TAKE_PROFIT    4m
      LOSS  $   0.90  $   0.89  $  100.00  $   -0.90     STOP_LOSS    2m

  STILL OPEN (unrealized)
    Ticker     Entry   Current       Size        P&L    Hold
      OPEN  $   0.85  $   0.87  $  100.00  $   +2.00    6m
```

The summary includes:
- **Performance**: starting/ending balance, return %, realized + unrealized P&L
- **Statistics**: trade count, win/loss/win rate, best/worst trade, signal count
- **Closed trades table**: every completed trade with entry, exit, P&L, exit reason, hold time
- **Still open**: any positions that were open when you stopped, with unrealized P&L

## Component Wiring

The runner imports and wires real `src/` components — no mocks:

| Component | Source | Purpose |
|-----------|--------|---------|
| `StrategyEngine` | `src/strategy/engine.py` | Signal generation, exit checks |
| `RiskManager` | `src/risk/manager.py` | Signal validation, circuit breakers |
| `OrderManager` | `src/execution/order_manager.py` | Order tracking |
| `PositionTracker` | `src/execution/position_tracker.py` | Position tracking |
| `Account` | `src/db/models.py` | Balance, P&L, fund locking |
| `Market` | `src/db/models.py` | Market data model (dollars) |

Config defaults are loaded from `config/config.yaml` and can be overridden via CLI flags.

## Dependencies

Uses only packages already in the project (`websockets`, `pyyaml`, `cryptography`) plus Python stdlib (`urllib.request`, `argparse`, `asyncio`). No additional installs needed.

## Structlog Suppression

The `src/` modules use `structlog` for logging. To prevent JSON log lines from corrupting the terminal dashboard, logging is redirected to `/dev/null` before any `src/` import:

```python
logging.basicConfig(level=logging.CRITICAL, stream=open(os.devnull, "w"))
```

## No Changes to src/

This file is entirely self-contained in `testing/`. It imports from `src/` but does not modify any existing source files.
