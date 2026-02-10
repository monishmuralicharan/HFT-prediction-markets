"""Supabase client for database operations"""

import sys
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from supabase import Client, create_client

from src.db.models import AccountSnapshot, Trade
from src.utils.logging import get_logger


class SupabaseClient:
    """Supabase client with graceful error handling"""

    def __init__(self, url: str, key: str):
        self.url = url
        self.key = key
        self.client: Optional[Client] = None
        self.logger = get_logger(__name__)
        self.connected = False

        # Try to connect
        self._connect()

    def _connect(self) -> None:
        """Connect to Supabase"""
        try:
            self.client = create_client(self.url, self.key)
            self.connected = True
            self.logger.info("Connected to Supabase", url=self.url)
        except Exception as e:
            self.logger.error(
                "Failed to connect to Supabase",
                error=str(e),
                url=self.url,
                exc_info=True,
            )
            self.connected = False
            # Don't raise - allow graceful degradation

    def _execute_with_fallback(self, operation: str, func: Any, *args, **kwargs) -> bool:
        """
        Execute a Supabase operation with error handling

        Returns True if successful, False otherwise (logs error but doesn't crash)
        """
        if not self.connected or not self.client:
            self.logger.warning(
                f"Skipping {operation} - Supabase not connected",
                operation=operation,
            )
            return False

        try:
            result = func(*args, **kwargs)
            return True
        except Exception as e:
            self.logger.error(
                f"Supabase {operation} failed",
                operation=operation,
                error=str(e),
                exc_info=True,
            )
            # Log to stderr as fallback
            print(
                f"[SUPABASE ERROR] {operation}: {str(e)}",
                file=sys.stderr,
            )
            return False

    def insert_trade(self, trade: Trade) -> bool:
        """
        Insert a trade record

        Args:
            trade: Trade object to insert

        Returns:
            True if successful, False otherwise
        """

        def _insert():
            data = trade.model_dump(mode="json")
            # Convert UUID to string
            data["id"] = str(data["id"])
            self.client.table("trades").insert(data).execute()

        return self._execute_with_fallback("insert_trade", _insert)

    def update_trade(self, trade_id: UUID, updates: dict[str, Any]) -> bool:
        """
        Update a trade record

        Args:
            trade_id: Trade ID to update
            updates: Dictionary of fields to update

        Returns:
            True if successful, False otherwise
        """

        def _update():
            self.client.table("trades").update(updates).eq("id", str(trade_id)).execute()

        return self._execute_with_fallback("update_trade", _update)

    def insert_snapshot(self, snapshot: AccountSnapshot) -> bool:
        """
        Insert an account snapshot

        Args:
            snapshot: AccountSnapshot object to insert

        Returns:
            True if successful, False otherwise
        """

        def _insert():
            data = snapshot.model_dump(mode="json")
            # Convert UUID to string
            data["id"] = str(data["id"])
            self.client.table("account_snapshots").insert(data).execute()

        return self._execute_with_fallback("insert_snapshot", _insert)

    def insert_log(
        self,
        level: str,
        event: str,
        logger: str,
        data: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Insert a log entry

        Args:
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            event: Log event/message
            logger: Logger name
            data: Optional additional data

        Returns:
            True if successful, False otherwise
        """

        def _insert():
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": level.upper(),
                "event": event,
                "logger": logger,
                "data": data or {},
            }
            self.client.table("logs").insert(log_entry).execute()

        return self._execute_with_fallback("insert_log", _insert)

    def get_daily_trades(self, date: Optional[datetime] = None) -> list[dict[str, Any]]:
        """
        Get trades for a specific date

        Args:
            date: Date to query (defaults to today)

        Returns:
            List of trade records (empty list if error)
        """
        if date is None:
            date = datetime.now(timezone.utc)

        if not self.connected or not self.client:
            self.logger.warning("Cannot get daily trades - Supabase not connected")
            return []

        try:
            start_of_day = date.replace(hour=0, minute=0, second=0, microsecond=0)
            end_of_day = date.replace(hour=23, minute=59, second=59, microsecond=999999)

            response = (
                self.client.table("trades")
                .select("*")
                .gte("entry_time", start_of_day.isoformat())
                .lte("entry_time", end_of_day.isoformat())
                .execute()
            )

            return response.data if response.data else []

        except Exception as e:
            self.logger.error(
                "Failed to get daily trades",
                error=str(e),
                date=date.isoformat(),
                exc_info=True,
            )
            return []

    def get_open_positions(self) -> list[dict[str, Any]]:
        """
        Get all open positions

        Returns:
            List of open position records (empty list if error)
        """
        if not self.connected or not self.client:
            self.logger.warning("Cannot get open positions - Supabase not connected")
            return []

        try:
            response = (
                self.client.table("trades")
                .select("*")
                .eq("status", "open")
                .order("entry_time", desc=True)
                .execute()
            )

            return response.data if response.data else []

        except Exception as e:
            self.logger.error(
                "Failed to get open positions",
                error=str(e),
                exc_info=True,
            )
            return []

    def get_latest_snapshot(self) -> Optional[dict[str, Any]]:
        """
        Get the most recent account snapshot

        Returns:
            Latest snapshot record or None if error
        """
        if not self.connected or not self.client:
            self.logger.warning("Cannot get latest snapshot - Supabase not connected")
            return None

        try:
            response = (
                self.client.table("account_snapshots")
                .select("*")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )

            if response.data and len(response.data) > 0:
                return response.data[0]
            return None

        except Exception as e:
            self.logger.error(
                "Failed to get latest snapshot",
                error=str(e),
                exc_info=True,
            )
            return None

    def health_check(self) -> bool:
        """
        Check if Supabase connection is healthy

        Returns:
            True if healthy, False otherwise
        """
        if not self.connected or not self.client:
            return False

        try:
            # Try a simple query
            self.client.table("logs").select("id").limit(1).execute()
            return True
        except Exception as e:
            self.logger.warning("Supabase health check failed", error=str(e))
            return False

    def close(self) -> None:
        """Close the Supabase connection"""
        self.logger.info("Closing Supabase connection")
        self.connected = False
        self.client = None
