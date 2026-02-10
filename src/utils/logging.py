"""Structured logging with Supabase integration"""

import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import structlog
from structlog.types import EventDict, Processor


class SupabaseLogProcessor:
    """Processor to send logs to Supabase"""

    def __init__(self, supabase_client: Optional[Any] = None):
        self.supabase_client = supabase_client
        self.enabled = supabase_client is not None

    def __call__(self, logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
        """Process log event and send to Supabase"""
        if not self.enabled:
            return event_dict

        # Don't block on Supabase errors
        try:
            log_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "level": event_dict.get("level", "info").upper(),
                "event": event_dict.get("event", ""),
                "logger": event_dict.get("logger", ""),
                "data": {
                    k: v
                    for k, v in event_dict.items()
                    if k not in ["event", "level", "logger", "timestamp"]
                },
            }

            # Async insert to Supabase (fire and forget)
            if self.supabase_client:
                self.supabase_client.table("logs").insert(log_entry).execute()

        except Exception as e:
            # Log to stderr but don't crash
            print(f"Failed to send log to Supabase: {e}", file=sys.stderr)

        return event_dict


def add_timestamp(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add timestamp to log event"""
    event_dict["timestamp"] = datetime.now(timezone.utc).isoformat()
    return event_dict


def add_log_level(logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """Add log level to event dict"""
    event_dict["level"] = method_name
    return event_dict


def setup_logging(
    level: str = "INFO",
    log_format: str = "json",
    log_to_console: bool = True,
    supabase_client: Optional[Any] = None,
) -> None:
    """
    Set up structured logging with optional Supabase integration

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Format type ('json' or 'console')
        log_to_console: Whether to log to console
        supabase_client: Optional Supabase client for remote logging
    """
    # Configure processors
    processors: list[Processor] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        add_log_level,
        add_timestamp,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    # Add Supabase processor if client provided
    if supabase_client:
        processors.append(SupabaseLogProcessor(supabase_client))

    # Add renderer based on format
    if log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer())

    # Configure structlog
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure standard logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout if log_to_console else None,
        level=getattr(logging, level.upper()),
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a logger instance

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return structlog.get_logger(name)
