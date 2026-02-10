"""Repository layer for data access with in-memory fallback"""

import sys
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from src.db.models import AccountSnapshot, Position, Trade
from src.db.supabase_client import SupabaseClient
from src.utils.logging import get_logger


class TradeRepository:
    """Repository for trade data with in-memory fallback"""

    def __init__(self, supabase: Optional[SupabaseClient] = None):
        self.supabase = supabase
        self.logger = get_logger(__name__)

        # In-memory fallback storage
        self._trades: dict[UUID, Trade] = {}

    def save(self, position: Position) -> bool:
        """
        Save a position as a trade

        Args:
            position: Position to save

        Returns:
            True if successful
        """
        trade = Trade.from_position(position)

        # Always save to in-memory store
        self._trades[trade.id] = trade

        # Try to save to Supabase
        if self.supabase:
            success = self.supabase.insert_trade(trade)
            if not success:
                self.logger.warning(
                    "Failed to save trade to Supabase, stored in memory only",
                    trade_id=str(trade.id),
                )
        else:
            self.logger.warning(
                "No Supabase client, trade stored in memory only",
                trade_id=str(trade.id),
            )
            # Log to stderr as fallback
            print(
                f"[TRADE] {trade.market_question} | "
                f"Entry: {trade.entry_price} | "
                f"Exit: {trade.exit_price} | "
                f"P&L: {trade.realized_pnl}",
                file=sys.stderr,
            )

        return True

    def update(self, trade_id: UUID, position: Position) -> bool:
        """
        Update an existing trade

        Args:
            trade_id: Trade ID to update
            position: Updated position data

        Returns:
            True if successful
        """
        # Update existing trade in-memory rather than creating a new one
        existing_trade = self._trades.get(trade_id)
        if existing_trade:
            existing_trade.exit_time = position.exit_time
            existing_trade.exit_price = position.exit_price
            existing_trade.exit_reason = position.exit_reason.value if position.exit_reason else None
            existing_trade.realized_pnl = position.realized_pnl
            existing_trade.realized_pnl_pct = position.realized_pnl_pct
            existing_trade.status = position.status.value
            existing_trade.exit_order_id = position.exit_order_id
            existing_trade.max_drawdown_pct = position.max_drawdown_pct
            existing_trade.max_profit_pct = position.max_profit_pct
            trade = existing_trade
        else:
            # Fallback: create new trade but preserve the ID
            trade = Trade.from_position(position)
            self._trades[trade_id] = trade

        # Try to update in Supabase
        if self.supabase:
            updates = {
                "exit_time": trade.exit_time.isoformat() if trade.exit_time else None,
                "exit_price": float(trade.exit_price) if trade.exit_price else None,
                "exit_reason": trade.exit_reason,
                "realized_pnl": float(trade.realized_pnl) if trade.realized_pnl else None,
                "realized_pnl_pct": (
                    float(trade.realized_pnl_pct) if trade.realized_pnl_pct else None
                ),
                "status": trade.status,
                "exit_order_id": trade.exit_order_id,
            }
            success = self.supabase.update_trade(trade_id, updates)
            if not success:
                self.logger.warning(
                    "Failed to update trade in Supabase",
                    trade_id=str(trade_id),
                )
        else:
            self.logger.warning(
                "No Supabase client, trade update in memory only",
                trade_id=str(trade_id),
            )

        return True

    def get(self, trade_id: UUID) -> Optional[Trade]:
        """Get a trade by ID from in-memory store"""
        return self._trades.get(trade_id)

    def get_all(self) -> list[Trade]:
        """Get all trades from in-memory store"""
        return list(self._trades.values())

    def get_daily_count(self, date: Optional[datetime] = None) -> int:
        """Get count of trades for a specific date"""
        if date is None:
            date = datetime.now(timezone.utc)

        # Try Supabase first
        if self.supabase:
            trades = self.supabase.get_daily_trades(date)
            return len(trades)

        # Fallback to in-memory count
        start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)

        count = sum(
            1
            for trade in self._trades.values()
            if start_of_day <= trade.entry_time <= end_of_day
        )
        return count


class SnapshotRepository:
    """Repository for account snapshots"""

    def __init__(self, supabase: Optional[SupabaseClient] = None):
        self.supabase = supabase
        self.logger = get_logger(__name__)

        # In-memory fallback storage
        self._snapshots: list[AccountSnapshot] = []

    def save(self, snapshot: AccountSnapshot) -> bool:
        """
        Save an account snapshot

        Args:
            snapshot: AccountSnapshot to save

        Returns:
            True if successful
        """
        # Always save to in-memory store
        self._snapshots.append(snapshot)

        # Limit in-memory snapshots to last 1000
        if len(self._snapshots) > 1000:
            self._snapshots = self._snapshots[-1000:]

        # Try to save to Supabase
        if self.supabase:
            success = self.supabase.insert_snapshot(snapshot)
            if not success:
                self.logger.warning(
                    "Failed to save snapshot to Supabase, stored in memory only"
                )
        else:
            self.logger.warning("No Supabase client, snapshot stored in memory only")
            # Log to stderr as fallback
            print(
                f"[SNAPSHOT] Balance: {snapshot.total_balance} | "
                f"P&L: {snapshot.total_pnl} | "
                f"Exposure: {snapshot.exposure_pct}%",
                file=sys.stderr,
            )

        return True

    def get_latest(self) -> Optional[AccountSnapshot]:
        """Get the latest snapshot"""
        # Try Supabase first
        if self.supabase:
            data = self.supabase.get_latest_snapshot()
            if data:
                return AccountSnapshot(**data)

        # Fallback to in-memory
        if self._snapshots:
            return self._snapshots[-1]

        return None

    def get_all(self) -> list[AccountSnapshot]:
        """Get all snapshots from in-memory store"""
        return self._snapshots.copy()


class LogRepository:
    """Repository for application logs"""

    def __init__(self, supabase: Optional[SupabaseClient] = None):
        self.supabase = supabase
        self.logger = get_logger(__name__)

    def log(
        self,
        level: str,
        event: str,
        logger_name: str,
        data: Optional[dict] = None,
    ) -> bool:
        """
        Log an event

        Args:
            level: Log level
            event: Event message
            logger_name: Logger name
            data: Optional additional data

        Returns:
            True if successful
        """
        # Always log to console
        print(
            f"[{level}] {logger_name}: {event}",
            file=sys.stderr if level in ["ERROR", "CRITICAL"] else sys.stdout,
        )

        # Try to save to Supabase
        if self.supabase:
            success = self.supabase.insert_log(level, event, logger_name, data)
            if not success:
                # Already logged to console, no need to warn
                pass

        return True
