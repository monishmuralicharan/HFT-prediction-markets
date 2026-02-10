"""Market filtering logic"""

from decimal import Decimal
from typing import Optional

from src.db.models import Market
from src.utils.logging import get_logger


class MarketFilter:
    """Filter markets based on trading criteria"""

    def __init__(
        self,
        min_probability: Decimal,
        min_liquidity: Decimal,
        min_volume: Decimal,
        max_spread_pct: Decimal,
        take_profit_pct: Decimal,
    ):
        """
        Initialize market filter

        Args:
            min_probability: Minimum probability threshold (e.g., 0.85)
            min_liquidity: Minimum liquidity at best bid ($)
            min_volume: Minimum 24h volume ($)
            max_spread_pct: Maximum bid-ask spread (%)
            take_profit_pct: Target profit percentage (for ceiling check)
        """
        self.min_probability = min_probability
        self.min_liquidity = min_liquidity
        self.min_volume = min_volume
        self.max_spread_pct = max_spread_pct
        self.take_profit_pct = take_profit_pct
        self.logger = get_logger(__name__)

    def filter(self, market: Market) -> tuple[bool, Optional[str]]:
        """
        Check if market passes all filters

        Args:
            market: Market to filter

        Returns:
            (passes, reason) - True if market passes, False with reason if not
        """
        # Check if market is active
        if not market.active:
            return False, "market_closed"

        # Check probability threshold
        if not market.probability or market.probability < self.min_probability:
            return False, "probability_too_low"

        # Check liquidity
        if not market.liquidity or market.liquidity < self.min_liquidity:
            return False, "insufficient_liquidity"

        # Check volume
        if market.volume_24h < self.min_volume:
            return False, "insufficient_volume"

        # Check spread
        if market.best_bid and market.best_ask:
            spread_pct = (
                (market.best_ask - market.best_bid) / market.best_bid * Decimal("100")
            )
            if spread_pct > self.max_spread_pct:
                return False, "spread_too_wide"
        else:
            return False, "missing_prices"

        # Check if we can achieve profit target before hitting ceiling (0.99)
        if not self._can_achieve_profit(market):
            return False, "insufficient_room_for_profit"

        return True, None

    def _can_achieve_profit(self, market: Market) -> bool:
        """
        Check if we can achieve profit target before hitting price ceiling

        Args:
            market: Market to check

        Returns:
            True if sufficient room for profit
        """
        if not market.best_ask:
            return False

        # Calculate target exit price
        target_exit_price = market.best_ask * (Decimal("1") + self.take_profit_pct)

        # Check if target is below ceiling (0.99)
        price_ceiling = Decimal("0.99")

        if target_exit_price >= price_ceiling:
            self.logger.debug(
                "Insufficient room for profit",
                market=market.question,
                entry=float(market.best_ask),
                target=float(target_exit_price),
                ceiling=float(price_ceiling),
            )
            return False

        return True

    def calculate_opportunity_score(self, market: Market) -> Optional[Decimal]:
        """
        Calculate opportunity score for a market (higher is better)

        Args:
            market: Market to score

        Returns:
            Score (0-100), or None if market doesn't pass filters
        """
        passes, reason = self.filter(market)
        if not passes:
            return None

        # Scoring factors (all normalized to 0-1)
        score = Decimal("0")

        # Probability score (higher probability = higher score)
        # 0.85 -> 0, 0.95 -> 100
        if market.probability:
            prob_score = (market.probability - self.min_probability) / (
                Decimal("0.95") - self.min_probability
            )
            score += prob_score * Decimal("40")  # 40% weight

        # Liquidity score (more liquidity = higher score)
        # Use logarithmic scale
        if market.liquidity:
            liq_ratio = market.liquidity / self.min_liquidity
            liq_score = min(Decimal("1"), liq_ratio.ln() / Decimal("2").ln())
            score += liq_score * Decimal("30")  # 30% weight

        # Spread score (tighter spread = higher score)
        if market.best_bid and market.best_ask:
            spread_pct = (market.best_ask - market.best_bid) / market.best_bid * Decimal("100")
            spread_score = Decimal("1") - (spread_pct / self.max_spread_pct)
            score += spread_score * Decimal("20")  # 20% weight

        # Volume score (more volume = higher score)
        if market.volume_24h:
            vol_ratio = market.volume_24h / self.min_volume
            vol_score = min(Decimal("1"), vol_ratio.ln() / Decimal("2").ln())
            score += vol_score * Decimal("10")  # 10% weight

        return min(score, Decimal("100"))
