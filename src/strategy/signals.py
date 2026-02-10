"""Trading signal generation"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from src.db.models import Market


class SignalType(str, Enum):
    """Signal type"""

    ENTRY = "entry"
    EXIT = "exit"


class SignalStrength(str, Enum):
    """Signal strength"""

    WEAK = "weak"
    MEDIUM = "medium"
    STRONG = "strong"


@dataclass
class TradingSignal:
    """Trading signal"""

    type: SignalType
    market: Market
    strength: SignalStrength
    confidence: Decimal  # 0-100
    entry_price: Decimal
    stop_loss_price: Decimal
    take_profit_price: Decimal
    position_size: Decimal
    reason: str

    def is_valid(self) -> bool:
        """Check if signal is valid"""
        # Entry price should be positive
        if self.entry_price <= 0:
            return False

        # Stop loss should be below entry
        if self.stop_loss_price >= self.entry_price:
            return False

        # Take profit should be above entry
        if self.take_profit_price <= self.entry_price:
            return False

        # Position size should be positive
        if self.position_size <= 0:
            return False

        return True


class SignalGenerator:
    """Generate trading signals"""

    def __init__(
        self,
        entry_threshold: Decimal,
        take_profit_pct: Decimal,
        stop_loss_pct: Decimal,
    ):
        """
        Initialize signal generator

        Args:
            entry_threshold: Minimum probability for entry
            take_profit_pct: Take profit percentage
            stop_loss_pct: Stop loss percentage
        """
        self.entry_threshold = entry_threshold
        self.take_profit_pct = take_profit_pct
        self.stop_loss_pct = stop_loss_pct

    def generate_entry_signal(
        self,
        market: Market,
        position_size: Decimal,
    ) -> TradingSignal:
        """
        Generate entry signal for a market

        Args:
            market: Market to trade
            position_size: Position size in dollars

        Returns:
            Trading signal
        """
        # Use best ask as entry price (avoid falsy Decimal("0") with `or`)
        entry_price = market.best_ask if market.best_ask is not None else market.last_price

        if entry_price is None or entry_price <= 0:
            raise ValueError("No entry price available")

        # Calculate stop loss and take profit
        stop_loss_price = entry_price * (Decimal("1") - self.stop_loss_pct)
        take_profit_price = entry_price * (Decimal("1") + self.take_profit_pct)

        # Calculate confidence based on probability
        if market.probability is not None:
            # Map 0.85-0.95 to 60-100 confidence
            confidence = (
                (market.probability - self.entry_threshold)
                / (Decimal("0.95") - self.entry_threshold)
            ) * Decimal("40") + Decimal("60")
            # Floor at 0 to prevent negative values, cap at 100
            confidence = max(Decimal("0"), min(confidence, Decimal("100")))
        else:
            confidence = Decimal("70")  # Default

        # Determine signal strength
        if confidence >= 90:
            strength = SignalStrength.STRONG
        elif confidence >= 75:
            strength = SignalStrength.MEDIUM
        else:
            strength = SignalStrength.WEAK

        return TradingSignal(
            type=SignalType.ENTRY,
            market=market,
            strength=strength,
            confidence=confidence,
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            position_size=position_size,
            reason=f"High probability ({float(market.probability or 0):.2%}) entry opportunity",
        )

    def validate_signal(self, signal: TradingSignal) -> tuple[bool, str]:
        """
        Validate a trading signal

        Args:
            signal: Signal to validate

        Returns:
            (is_valid, reason)
        """
        if not signal.is_valid():
            return False, "Invalid signal parameters"

        # Check entry price is within valid prediction market range
        if signal.entry_price < Decimal("0.01") or signal.entry_price > Decimal("0.99"):
            return False, f"Entry price out of range: {signal.entry_price}"

        # Check take profit doesn't exceed ceiling
        if signal.take_profit_price > Decimal("0.99"):
            return False, f"Take profit exceeds ceiling: {signal.take_profit_price}"

        # Check stop loss is reasonable (> 0)
        if signal.stop_loss_price <= Decimal("0"):
            return False, f"Stop loss too low: {signal.stop_loss_price}"

        return True, "Signal valid"
