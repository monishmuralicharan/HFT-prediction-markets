"""Configuration management for the HFT bot"""

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrategyConfig(BaseSettings):
    """Strategy configuration"""

    entry_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    take_profit_pct: float = Field(default=0.02, ge=0.0, le=0.1)
    stop_loss_pct: float = Field(default=0.01, ge=0.0, le=0.1)
    max_hold_time_hours: int = Field(default=2, ge=1)
    min_liquidity: float = Field(default=500.0, ge=0.0)
    min_volume: float = Field(default=10000.0, ge=0.0)
    max_spread_pct: float = Field(default=0.02, ge=0.0, le=0.1)


class RiskConfig(BaseSettings):
    """Risk management configuration"""

    max_position_size_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    max_total_exposure_pct: float = Field(default=0.30, ge=0.0, le=1.0)
    max_daily_loss_pct: float = Field(default=0.05, ge=0.0, le=1.0)
    max_consecutive_losses: int = Field(default=5, ge=1)
    api_error_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    max_disconnect_seconds: int = Field(default=15, ge=1)


class PositionsConfig(BaseSettings):
    """Position management configuration"""

    max_concurrent: int = Field(default=10, ge=1)
    min_position_size: float = Field(default=50.0, ge=0.0)
    max_position_size: float = Field(default=1000.0, ge=0.0)

    @field_validator("max_position_size")
    @classmethod
    def max_gte_min(cls, v: float, info) -> float:
        min_size = info.data.get("min_position_size", 0.0)
        if v < min_size:
            raise ValueError(
                f"max_position_size ({v}) must be >= min_position_size ({min_size})"
            )
        return v


class APIConfig(BaseSettings):
    """API configuration"""

    base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    websocket_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"
    demo_base_url: str = "https://demo-api.kalshi.co/trade-api/v2"
    demo_websocket_url: str = "wss://demo-api.kalshi.co/trade-api/ws/v2"
    use_demo: bool = True
    read_rate_limit_per_second: int = Field(default=20, ge=1)
    write_rate_limit_per_second: int = Field(default=10, ge=1)
    request_timeout: int = Field(default=10, ge=1)
    max_retries: int = Field(default=3, ge=1)
    retry_backoff_base: float = Field(default=2.0, ge=1.0)

    def get_api_base_url(self) -> str:
        """Get the active API base URL based on demo mode"""
        return self.demo_base_url if self.use_demo else self.base_url

    def get_ws_url(self) -> str:
        """Get the active WebSocket URL based on demo mode"""
        return self.demo_websocket_url if self.use_demo else self.websocket_url


class WebSocketConfig(BaseSettings):
    """WebSocket configuration"""

    reconnect_delay_base: int = Field(default=1, ge=1)
    max_reconnect_delay: int = Field(default=30, ge=1)
    ping_interval: int = Field(default=30, ge=1)
    ping_timeout: int = Field(default=10, ge=1)


class DatabaseConfig(BaseSettings):
    """Database configuration"""

    pool_size: int = Field(default=5, ge=1)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout: int = Field(default=30, ge=1)
    pool_recycle: int = Field(default=3600, ge=1)


class LoggingConfig(BaseSettings):
    """Logging configuration"""

    level: str = "INFO"
    format: str = "json"
    log_to_console: bool = True
    log_to_supabase: bool = True

    @field_validator("level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level: {v}")
        return v.upper()


class EmailConfig(BaseSettings):
    """Email alert configuration"""

    enabled: bool = True
    rate_limit_minutes: int = Field(default=5, ge=1)
    send_daily_summary: bool = True
    daily_summary_hour: int = Field(default=20, ge=0, le=23)


class MonitoringConfig(BaseSettings):
    """Monitoring configuration"""

    health_check_port: int = Field(default=8080, ge=1024, le=65535)
    metrics_enabled: bool = True


class SecretsConfig(BaseSettings):
    """Secrets from environment variables"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    kalshi_api_key_id: str = Field(alias="KALSHI_API_KEY_ID")
    kalshi_private_key: str = Field(alias="KALSHI_PRIVATE_KEY")

    supabase_url: str = Field(alias="SUPABASE_URL")
    supabase_key: str = Field(alias="SUPABASE_KEY")

    smtp_host: str = Field(alias="SMTP_HOST")
    smtp_port: int = Field(alias="SMTP_PORT")
    smtp_user: str = Field(alias="SMTP_USER")
    smtp_password: str = Field(alias="SMTP_PASSWORD")
    alert_email: str = Field(alias="ALERT_EMAIL")

    environment: str = Field(default="development", alias="ENVIRONMENT")


class Config:
    """Main configuration class"""

    def __init__(self, config_path: Optional[Path] = None, env_path: Optional[Path] = None):
        # Load YAML config
        if config_path is None:
            config_path = Path(__file__).parent.parent / "config" / "config.yaml"

        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, "r") as f:
            config_data = yaml.safe_load(f)

        # Handle empty or None YAML file
        if not config_data or not isinstance(config_data, dict):
            config_data = {}

        # Initialize config sections (use empty dict for None/missing sections)
        self.strategy = StrategyConfig(**(config_data.get("strategy") or {}))
        self.risk = RiskConfig(**(config_data.get("risk") or {}))
        self.positions = PositionsConfig(**(config_data.get("positions") or {}))
        self.api = APIConfig(**(config_data.get("api") or {}))
        self.websocket = WebSocketConfig(**(config_data.get("websocket") or {}))
        self.database = DatabaseConfig(**(config_data.get("database") or {}))
        self.logging = LoggingConfig(**(config_data.get("logging") or {}))
        self.email = EmailConfig(**(config_data.get("email") or {}))
        self.monitoring = MonitoringConfig(**(config_data.get("monitoring") or {}))

        # Load secrets from environment
        # Load .env file manually using dotenv if env_path is provided
        if env_path:
            from dotenv import load_dotenv

            load_dotenv(env_path, override=True)

        self.secrets = SecretsConfig()

    def is_production(self) -> bool:
        """Check if running in production"""
        return self.secrets.environment.lower() == "production"

    def is_development(self) -> bool:
        """Check if running in development"""
        return self.secrets.environment.lower() == "development"


# Global config instance
_config: Optional[Config] = None


def get_config(
    config_path: Optional[Path] = None, env_path: Optional[Path] = None
) -> Config:
    """Get or create the global config instance"""
    global _config
    if _config is None:
        _config = Config(config_path=config_path, env_path=env_path)
    return _config


def reload_config(
    config_path: Optional[Path] = None, env_path: Optional[Path] = None
) -> Config:
    """Force reload the config"""
    global _config
    _config = Config(config_path=config_path, env_path=env_path)
    return _config
