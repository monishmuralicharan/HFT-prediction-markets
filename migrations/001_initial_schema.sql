-- Kalshi HFT Bot - Initial Schema
-- Execute this script in your Supabase SQL editor

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Trades table
CREATE TABLE IF NOT EXISTS trades (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Trade identification
    market_id TEXT NOT NULL,
    market_question TEXT NOT NULL,
    outcome TEXT NOT NULL,

    -- Entry details
    entry_time TIMESTAMPTZ NOT NULL,
    entry_price DECIMAL(10, 4) NOT NULL,
    entry_probability DECIMAL(5, 4) NOT NULL,
    position_size DECIMAL(10, 2) NOT NULL,

    -- Exit details
    exit_time TIMESTAMPTZ,
    exit_price DECIMAL(10, 4),
    exit_reason TEXT,

    -- P&L
    realized_pnl DECIMAL(10, 2),
    realized_pnl_pct DECIMAL(6, 4),

    -- Risk metrics
    stop_loss_price DECIMAL(10, 4),
    take_profit_price DECIMAL(10, 4),
    max_drawdown_pct DECIMAL(6, 4),
    max_profit_pct DECIMAL(6, 4),

    -- Order IDs
    entry_order_id TEXT,
    stop_loss_order_id TEXT,
    take_profit_order_id TEXT,
    exit_order_id TEXT,

    -- Status
    status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'cancelled')),

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Account snapshots table
CREATE TABLE IF NOT EXISTS account_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Balance
    total_balance DECIMAL(10, 2) NOT NULL,
    available_balance DECIMAL(10, 2) NOT NULL,
    locked_balance DECIMAL(10, 2) NOT NULL,

    -- Exposure
    total_exposure DECIMAL(10, 2) NOT NULL,
    exposure_pct DECIMAL(6, 4) NOT NULL,

    -- P&L
    realized_pnl DECIMAL(10, 2) NOT NULL DEFAULT 0,
    unrealized_pnl DECIMAL(10, 2) NOT NULL DEFAULT 0,
    total_pnl DECIMAL(10, 2) NOT NULL DEFAULT 0,

    -- Daily metrics
    daily_pnl DECIMAL(10, 2),
    daily_pnl_pct DECIMAL(6, 4),
    daily_trades INTEGER DEFAULT 0,
    daily_wins INTEGER DEFAULT 0,
    daily_losses INTEGER DEFAULT 0,

    -- Position counts
    open_positions INTEGER NOT NULL DEFAULT 0,

    -- Circuit breaker state
    circuit_breaker_active BOOLEAN DEFAULT FALSE,
    circuit_breaker_reason TEXT,

    -- Metadata
    metadata JSONB DEFAULT '{}'::jsonb
);

-- Logs table
CREATE TABLE IF NOT EXISTS logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Log details
    level TEXT NOT NULL CHECK (level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL')),
    event TEXT NOT NULL,
    logger TEXT,

    -- Log data
    data JSONB DEFAULT '{}'::jsonb
);

-- Create indexes for performance

-- Trades indexes
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_market_id ON trades(market_id);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_trades_exit_time ON trades(exit_time DESC);

-- Account snapshots indexes
CREATE INDEX IF NOT EXISTS idx_account_snapshots_created_at ON account_snapshots(created_at DESC);

-- Logs indexes
CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON logs(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_logs_level ON logs(level);
CREATE INDEX IF NOT EXISTS idx_logs_event ON logs(event);

-- Create views for common queries

-- Daily performance view
CREATE OR REPLACE VIEW daily_performance AS
SELECT
    DATE(created_at) as trade_date,
    COUNT(*) as total_trades,
    SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
    SUM(CASE WHEN realized_pnl < 0 THEN 1 ELSE 0 END) as losses,
    SUM(realized_pnl) as total_pnl,
    AVG(realized_pnl) as avg_pnl,
    AVG(realized_pnl_pct) as avg_pnl_pct,
    MAX(realized_pnl) as max_win,
    MIN(realized_pnl) as max_loss
FROM trades
WHERE status = 'closed' AND realized_pnl IS NOT NULL
GROUP BY DATE(created_at)
ORDER BY trade_date DESC;

-- Active positions view
CREATE OR REPLACE VIEW active_positions AS
SELECT
    id,
    market_id,
    market_question,
    outcome,
    entry_time,
    entry_price,
    position_size,
    stop_loss_price,
    take_profit_price,
    EXTRACT(EPOCH FROM (NOW() - entry_time)) / 3600 as hours_open
FROM trades
WHERE status = 'open'
ORDER BY entry_time DESC;

-- Recent errors view
CREATE OR REPLACE VIEW recent_errors AS
SELECT
    timestamp,
    event,
    logger,
    data
FROM logs
WHERE level IN ('ERROR', 'CRITICAL')
ORDER BY timestamp DESC
LIMIT 100;

-- Comments for documentation
COMMENT ON TABLE trades IS 'All trading positions (open and closed)';
COMMENT ON TABLE account_snapshots IS 'Periodic snapshots of account state';
COMMENT ON TABLE logs IS 'Application logs';

COMMENT ON COLUMN trades.entry_probability IS 'Market probability at entry (0-1)';
COMMENT ON COLUMN trades.realized_pnl_pct IS 'P&L as percentage of position size';
COMMENT ON COLUMN account_snapshots.exposure_pct IS 'Total exposure as percentage of balance';
COMMENT ON COLUMN account_snapshots.daily_pnl_pct IS 'Daily P&L as percentage of starting balance';
