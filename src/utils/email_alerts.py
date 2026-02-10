"""Email alerting system"""

import asyncio
import smtplib
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from src.db.models import Account, Position
from src.risk.circuit_breakers import CircuitBreakerType
from src.utils.logging import get_logger


class EmailAlerter:
    """Send email alerts for critical events"""

    def __init__(
        self,
        smtp_host: str,
        smtp_port: int,
        smtp_user: str,
        smtp_password: str,
        alert_email: str,
        enabled: bool = True,
        rate_limit_minutes: int = 5,
    ):
        """
        Initialize email alerter

        Args:
            smtp_host: SMTP server host
            smtp_port: SMTP server port
            smtp_user: SMTP username
            smtp_password: SMTP password
            alert_email: Email address to send alerts to
            enabled: Whether email alerts are enabled
            rate_limit_minutes: Minimum minutes between emails
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_user = smtp_user
        self.smtp_password = smtp_password
        self.alert_email = alert_email
        self.enabled = enabled
        self.rate_limit_minutes = rate_limit_minutes
        self.logger = get_logger(__name__)

        # Rate limiting
        self.last_email_time: dict[str, float] = {}

    async def send_circuit_breaker_alert(
        self,
        reason: CircuitBreakerType,
        account: Account,
    ) -> bool:
        """
        Send circuit breaker alert

        Args:
            reason: Circuit breaker reason
            account: Current account state

        Returns:
            True if sent successfully
        """
        subject = f"🚨 CIRCUIT BREAKER TRIGGERED: {reason.value.upper()}"

        body = f"""
CRITICAL ALERT: Circuit breaker has been activated

Reason: {reason.value}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

Account Status:
- Total Balance: ${account.total_balance:,.2f}
- Available Balance: ${account.available_balance:,.2f}
- Locked Balance: ${account.locked_balance:,.2f}

Daily Performance:
- Daily P&L: ${account.daily_pnl:,.2f} ({account.daily_pnl_pct():.2f}%)
- Daily Trades: {account.daily_trades}
- Daily Wins: {account.daily_wins}
- Daily Losses: {account.daily_losses}
- Consecutive Losses: {account.consecutive_losses}

All trading has been halted. Please review the system immediately.
"""

        return await self._send_email(subject, body, "circuit_breaker")

    async def send_position_opened_alert(self, position: Position) -> bool:
        """
        Send position opened alert

        Args:
            position: Opened position

        Returns:
            True if sent successfully
        """
        subject = f"📈 Position Opened: {position.market_question[:50]}"

        body = f"""
New position opened:

Market: {position.market_question}
Outcome: {position.outcome}

Entry Details:
- Entry Price: ${position.entry_price:.4f}
- Position Size: ${position.position_size:,.2f}
- Entry Probability: {position.entry_probability * 100:.2f}%

Risk Management:
- Stop Loss: ${position.stop_loss_price:.4f}
- Take Profit: ${position.take_profit_price:.4f}

Time: {position.entry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}
"""

        return await self._send_email(subject, body, "position_opened")

    async def send_position_closed_alert(self, position: Position) -> bool:
        """
        Send position closed alert

        Args:
            position: Closed position

        Returns:
            True if sent successfully
        """
        pnl_emoji = "✅" if position.realized_pnl and position.realized_pnl > 0 else "❌"
        subject = f"{pnl_emoji} Position Closed: {position.market_question[:50]}"

        body = f"""
Position closed:

Market: {position.market_question}
Outcome: {position.outcome}

Entry Details:
- Entry Price: ${position.entry_price:.4f}
- Entry Time: {position.entry_time.strftime('%Y-%m-%d %H:%M:%S UTC')}

Exit Details:
- Exit Price: ${position.exit_price:.4f}
- Exit Time: {position.exit_time.strftime('%Y-%m-%d %H:%M:%S UTC') if position.exit_time else 'N/A'}
- Exit Reason: {position.exit_reason.value if position.exit_reason else 'N/A'}
- Hold Time: {position.hours_open():.2f} hours

Performance:
- Realized P&L: ${position.realized_pnl:,.2f} ({position.realized_pnl_pct:.2f}%)
- Position Size: ${position.position_size:,.2f}
- Max Profit: {position.max_profit_pct:.2f}%
- Max Drawdown: {position.max_drawdown_pct:.2f}%
"""

        return await self._send_email(subject, body, "position_closed")

    async def send_daily_summary(
        self,
        account: Account,
        positions_opened: int,
        positions_closed: int,
    ) -> bool:
        """
        Send daily summary

        Args:
            account: Current account state
            positions_opened: Number of positions opened today
            positions_closed: Number of positions closed today

        Returns:
            True if sent successfully
        """
        win_rate = (
            (account.daily_wins / account.daily_trades * 100)
            if account.daily_trades > 0
            else 0
        )

        subject = f"📊 Daily Summary - {datetime.now().strftime('%Y-%m-%d')}"

        body = f"""
Daily Trading Summary

Date: {datetime.now().strftime('%Y-%m-%d')}

Performance:
- Daily P&L: ${account.daily_pnl:,.2f} ({account.daily_pnl_pct():.2f}%)
- Total P&L: ${account.total_pnl():,.2f}

Trading Activity:
- Positions Opened: {positions_opened}
- Positions Closed: {positions_closed}
- Completed Trades: {account.daily_trades}
- Wins: {account.daily_wins}
- Losses: {account.daily_losses}
- Win Rate: {win_rate:.1f}%

Account Status:
- Total Balance: ${account.total_balance:,.2f}
- Available Balance: ${account.available_balance:,.2f}
- Locked Balance: ${account.locked_balance:,.2f}

Risk Metrics:
- Consecutive Losses: {account.consecutive_losses}
- Exposure: ${account.locked_balance:,.2f}
"""

        return await self._send_email(subject, body, "daily_summary")

    async def send_error_alert(self, error_type: str, error_message: str) -> bool:
        """
        Send critical error alert

        Args:
            error_type: Type of error
            error_message: Error message

        Returns:
            True if sent successfully
        """
        subject = f"⚠️ Critical Error: {error_type}"

        body = f"""
A critical error has occurred:

Error Type: {error_type}
Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}

Error Message:
{error_message}

Please investigate immediately.
"""

        return await self._send_email(subject, body, "error")

    async def _send_email(
        self,
        subject: str,
        body: str,
        alert_type: str,
    ) -> bool:
        """
        Send email with rate limiting

        Args:
            subject: Email subject
            body: Email body
            alert_type: Type of alert (for rate limiting)

        Returns:
            True if sent successfully
        """
        if not self.enabled:
            self.logger.debug("Email alerts disabled", alert_type=alert_type)
            return False

        # Check rate limit
        if not self._check_rate_limit(alert_type):
            self.logger.warning(
                "Email rate limited",
                alert_type=alert_type,
            )
            return False

        try:
            # Run in executor to avoid blocking
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                self._send_email_sync,
                subject,
                body,
            )

            # Update rate limit
            self.last_email_time[alert_type] = time.time()

            self.logger.info("Email sent", subject=subject, alert_type=alert_type)
            return True

        except Exception as e:
            self.logger.error(
                "Failed to send email",
                error=str(e),
                alert_type=alert_type,
                exc_info=True,
            )
            return False

    def _send_email_sync(self, subject: str, body: str) -> None:
        """Synchronous email sending"""
        msg = MIMEMultipart()
        msg["From"] = self.smtp_user
        msg["To"] = self.alert_email
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        # Connect and send
        with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)

    def _check_rate_limit(self, alert_type: str) -> bool:
        """
        Check if email can be sent based on rate limit

        Args:
            alert_type: Type of alert

        Returns:
            True if email can be sent
        """
        # Circuit breaker alerts are never rate limited
        if alert_type == "circuit_breaker":
            return True

        last_time = self.last_email_time.get(alert_type, 0)
        elapsed_minutes = (time.time() - last_time) / 60

        return elapsed_minutes >= self.rate_limit_minutes
