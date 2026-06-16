import asyncio
import math
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

# Add project to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


try:
    from patch_gamma_markets import apply_gamma_markets_patch, verify_patch

    patch_applied = apply_gamma_markets_patch()
    if patch_applied:
        verify_patch()
    else:
        print("ERROR: Failed to apply gamma_market patch")
        sys.exit(1)
except ImportError as e:
    print(f"ERROR: Could not import patch module: {e}")
    print("Make sure patch_gamma_markets.py is in the same directory")
    sys.exit(1)

# Now import Nautilus
import redis
from dotenv import load_dotenv
from loguru import logger
from nautilus_trader.adapters.polymarket import (
    POLYMARKET,
    PolymarketDataClientConfig,
    PolymarketExecClientConfig,
)
from nautilus_trader.adapters.polymarket.factories import (
    PolymarketLiveDataClientFactory,
    PolymarketLiveExecClientFactory,
)
from nautilus_trader.config import (
    InstrumentProviderConfig,
    LiveDataEngineConfig,
    LiveExecEngineConfig,
    LiveRiskEngineConfig,
    LoggingConfig,
    TradingNodeConfig,
)
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.model.identifiers import ClientOrderId
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy

from core.strategy_brain.fusion_engine.signal_fusion import get_fusion_engine
from core.strategy_brain.signal_processors.base_processor import (
    SignalDirection,
    SignalStrength,
    SignalType,
    TradingSignal,
)
from core.strategy_brain.signal_processors.deribit_pcr_processor import (
    DeribitPCRProcessor,
)
from core.strategy_brain.signal_processors.divergence_processor import (
    PriceDivergenceProcessor,
)
from core.strategy_brain.signal_processors.orderbook_processor import (
    OrderBookImbalanceProcessor,
)
from core.strategy_brain.signal_processors.sentiment_processor import SentimentProcessor

# Import our phases
from core.strategy_brain.signal_processors.spike_detector import SpikeDetectionProcessor
from core.strategy_brain.signal_processors.tick_velocity_processor import (
    TickVelocityProcessor,
)
from execution.risk_engine import get_risk_engine
from feedback.learning_engine import get_learning_engine
from monitoring.grafana_exporter import get_grafana_exporter
from monitoring.performance_tracker import get_performance_tracker
from tv_market_select import (
    conviction_stake,
    entry_prob_and_price,
    fresh_quote,
    select_target_market,
)

load_dotenv()

# Install the secret-redaction safety net right after env vars are loaded, so no
# loguru line can leak a credential in the window before main() configures the
# file sink. Idempotent with the call inside log_setup.setup_file_logging.
from log_setup import enable_log_redaction

enable_log_redaction()
from patch_market_orders import apply_market_order_patch

patch_applied = apply_market_order_patch()
if patch_applied:
    logger.info("Market order patch applied successfully")
else:
    logger.warning("Market order patch failed - orders may be rejected")


# =============================================================================
# CONSTANTS
# =============================================================================
QUOTE_STABILITY_REQUIRED = 3  # Need only 3 valid ticks to be stable (faster startup)
QUOTE_MIN_SPREAD = 0.001  # Both bid AND ask must be at least this
MARKET_INTERVAL_SECONDS = 900  # 15-minute markets

# Single source of truth for the bet size, in USD. Drives BOTH the dry-run
# recorded amount and (via patch_market_orders reading the same env var) the
# real live order, so dry run and live never diverge. The risk-engine caps
# scale off the same env var. Change it in .env + restart — no code edit.
POSITION_SIZE_USD = Decimal(os.getenv("MARKET_BUY_USD", "1.00"))


@dataclass
class PaperTrade:
    """Track paper/simulation trades"""

    timestamp: datetime
    direction: str
    size_usd: float
    price: float
    signal_score: float
    signal_confidence: float
    outcome: str = "PENDING"

    def to_dict(self):
        return {
            "timestamp": self.timestamp.isoformat(),
            "direction": self.direction,
            "size_usd": self.size_usd,
            "price": self.price,
            "signal_score": self.signal_score,
            "signal_confidence": self.signal_confidence,
            "outcome": self.outcome,
        }


def init_redis():
    """Initialize Redis connection for simulation mode control."""
    try:
        redis_client = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 2)),
            decode_responses=True,
            socket_connect_timeout=5,
            socket_keepalive=True,
        )
        redis_client.ping()
        logger.info("Redis connection established")
        return redis_client
    except Exception as e:
        logger.warning(f"Redis connection failed: {e}")
        logger.warning("Simulation mode will be static (from .env)")
        return None


class IntegratedBTCStrategy(Strategy):
    """
    Integrated BTC Strategy - FIXED VERSION
    - Subscribes immediately at startup
    - Forces stability for first trade
    - Correct timing for market switching
    """

    def __init__(self, redis_client=None, enable_grafana=True, test_mode=False):
        super().__init__()

        self.bot_start_time = datetime.now(UTC)
        self.restart_after_minutes = 90

        # Nautilus
        self.instrument_id = None
        self.redis_client = redis_client
        self.current_simulation_mode = False

        # Store ALL BTC instruments
        self.all_btc_instruments: list[dict] = []
        self.current_instrument_index: int = -1
        self.next_switch_time: datetime | None = None

        # Quote-stability tracking
        self._stable_tick_count = 0
        self._market_stable = False
        self._last_instrument_switch = None

        # =========================================================================
        # FIX 1: Force first trade by setting last_trade_time to -1
        # =========================================================================
        self.last_trade_time = -1  # Force first trade immediately!
        self._waiting_for_market_open = (
            False  # True when waiting for a future market to open
        )
        self._last_bid_ask = (
            None  # (bid_decimal, ask_decimal) from last tick, for liquidity checks
        )
        # Per-instrument latest quote {instrument_id: (bid, ask, epoch_ts)}.
        # Lets a TradingView signal price the FRESH (N+1) market at rollover
        # instead of the expiring one — see tv_market_select / _ensure_next_subscribed.
        self._last_quote_by_instrument: dict = {}
        # Instruments we've already subscribed to (avoid duplicate subscriptions
        # when pre-subscribing the next market).
        self._subscribed_instruments: set = set()

        # Tick buffer: rolling 90s of ticks for TickVelocityProcessor
        from collections import deque

        self._tick_buffer: deque = deque(maxlen=500)  # ~500 ticks = well over 90s

        # YES token id for the current market (set in _load_all_btc_instruments)
        self._yes_token_id: str | None = None

        # Phase 4: Signal Processors
        self.spike_detector = SpikeDetectionProcessor(
            spike_threshold=0.05,  # FIXED: was 0.15 (too high for probabilities)
            lookback_periods=20,
        )
        self.sentiment_processor = SentimentProcessor(
            extreme_fear_threshold=25,
            extreme_greed_threshold=75,
        )
        self.divergence_processor = PriceDivergenceProcessor(
            divergence_threshold=0.05,
        )
        self.orderbook_processor = OrderBookImbalanceProcessor(
            imbalance_threshold=0.30,  # 30% skew to signal
            min_book_volume=50.0,  # ignore illiquid books
        )
        self.tick_velocity_processor = TickVelocityProcessor(
            velocity_threshold_60s=0.015,  # 1.5% move in 60s
            velocity_threshold_30s=0.010,  # 1.0% move in 30s
        )
        self.deribit_pcr_processor = DeribitPCRProcessor(
            bullish_pcr_threshold=1.20,
            bearish_pcr_threshold=0.70,
            max_days_to_expiry=2,
            cache_seconds=300,  # refresh every 5 min
        )

        # Phase 4: Signal Fusion — update weights for 6 processors
        self.fusion_engine = get_fusion_engine()
        # Rebalanced weights (must sum ≤ 1.0; higher = more influence)
        self.fusion_engine.set_weight(
            "OrderBookImbalance", 0.30
        )  # best real-time signal
        self.fusion_engine.set_weight("TickVelocity", 0.25)  # fast poly momentum
        self.fusion_engine.set_weight("PriceDivergence", 0.18)  # spot momentum
        self.fusion_engine.set_weight("SpikeDetection", 0.12)  # mean reversion
        self.fusion_engine.set_weight("DeribitPCR", 0.10)  # institutional sentiment
        self.fusion_engine.set_weight("SentimentAnalysis", 0.05)  # daily F&G (weak)

        # Phase 5: Risk Management
        self.risk_engine = get_risk_engine()

        # Phase 6: Performance Tracking
        self.performance_tracker = get_performance_tracker()

        # Phase 7: Learning Engine
        self.learning_engine = get_learning_engine()

        # Phase 6: Grafana (optional)
        if enable_grafana:
            self.grafana_exporter = get_grafana_exporter()
        else:
            self.grafana_exporter = None

        # Price history
        self.price_history = []
        self.max_history = 100

        # Paper trading tracker
        self.paper_trades: list[PaperTrade] = []

        self.test_mode = test_mode

        # TradingView webhook strategy (exclusive mode via Redis key
        # btc_trading:active_strategy — "fusion" is the default)
        self.active_strategy = "fusion"
        self._tv_confidence = float(os.getenv("TRADINGVIEW_SIGNAL_CONFIDENCE", "0.75"))
        self._tv_signal_ttl = float(os.getenv("TRADINGVIEW_SIGNAL_TTL_SECONDS", "30"))
        # Hybrid book-agreement gate + conviction sizing (shared math with the
        # backtest via tv_market_select.conviction_stake). Defaults are the
        # PnL-maximising config from `backtest tune` (floor 0.42, sizing neutral);
        # tune again and re-set these as data grows. Floor 0 + min_frac 1 = old
        # flat-stake behaviour.
        self._tv_min_book_prob = float(os.getenv("TV_MIN_BOOK_PROB", "0.42"))
        self._tv_size_full_prob = float(os.getenv("TV_SIZE_FULL_PROB", "0.55"))
        self._tv_size_min_frac = float(os.getenv("TV_SIZE_MIN_FRAC", "1.0"))

        if test_mode:
            logger.info("=" * 80)
            logger.info("  TEST MODE ACTIVE - Trading every minute!")
            logger.info("=" * 80)

        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY INITIALIZED - FIXED VERSION")
        logger.info("  Phase 4: Signal processors ready")
        logger.info("  Phase 5: Risk engine ready")
        logger.info("  Phase 6: Performance tracking ready")
        logger.info("  Phase 7: Learning engine ready")
        logger.info("  $1 per trade maximum")
        logger.info("=" * 80)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _seconds_to_next_15min_boundary(self) -> float:
        """Return seconds until the next 15-minute UTC boundary."""
        now_ts = datetime.now(UTC).timestamp()
        next_boundary = (
            math.floor(now_ts / MARKET_INTERVAL_SECONDS) + 1
        ) * MARKET_INTERVAL_SECONDS
        return next_boundary - now_ts

    def _is_quote_valid(self, bid, ask) -> bool:
        """Return True only when BOTH bid and ask are present and make sense."""
        if bid is None or ask is None:
            return False
        try:
            b = float(bid)
            a = float(ask)
        except (TypeError, ValueError):
            return False
        if b < QUOTE_MIN_SPREAD or a < QUOTE_MIN_SPREAD:
            return False
        if b > 0.999 or a > 0.999:
            return False
        return True

    def _reset_stability(self, reason: str = ""):
        """Mark the market as unstable and reset the counter."""
        if self._market_stable:
            logger.warning(f"Market stability RESET{' – ' + reason if reason else ''}")
        self._market_stable = False
        self._stable_tick_count = 0

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    async def check_simulation_mode(self) -> bool:
        """Check Redis for current simulation mode."""
        if not self.redis_client:
            return self.current_simulation_mode
        try:
            sim_mode = self.redis_client.get("btc_trading:simulation_mode")
            if sim_mode is not None:
                redis_simulation = sim_mode == "1"
                if redis_simulation != self.current_simulation_mode:
                    self.current_simulation_mode = redis_simulation
                    mode_text = "SIMULATION" if redis_simulation else "LIVE TRADING"
                    logger.warning(f"Trading mode changed to: {mode_text}")
                    if not redis_simulation:
                        logger.warning("LIVE TRADING ACTIVE - Real money at risk!")
                return redis_simulation
        except Exception as e:
            logger.warning(f"Failed to check Redis simulation mode: {e}")
        return self.current_simulation_mode

    def get_active_strategy(self) -> str:
        """Check Redis for the active strategy ('fusion' or 'tradingview')."""
        if not self.redis_client:
            return self.active_strategy
        try:
            value = self.redis_client.get("btc_trading:active_strategy")
            strategy = value if value in ("fusion", "tradingview") else "fusion"
            if strategy != self.active_strategy:
                logger.warning(f"Active strategy changed to: {strategy.upper()}")
                self.active_strategy = strategy
            return strategy
        except Exception as e:
            logger.warning(f"Failed to check Redis active strategy: {e}")
            return self.active_strategy

    def check_tv_dry_run(self) -> bool:
        """
        Check Redis for the TradingView dry-run flag.

        In dry run the full webhook pipeline runs (secret, TTL, dedup, risk,
        liquidity) but NO order is placed — not even a paper trade. The
        would-be trade is logged and appended to tv_dry_run_trades.json so
        the indicator can be validated against real market outcomes.
        """
        if not self.redis_client:
            return False
        try:
            return self.redis_client.get("btc_trading:tv_dry_run") == "1"
        except Exception as e:
            logger.warning(f"Failed to check Redis tv_dry_run: {e}")
            return False

    # ------------------------------------------------------------------
    # Strategy lifecycle
    # ------------------------------------------------------------------

    def on_start(self):
        """Called when strategy starts - LOAD ALL MARKETS AND SUBSCRIBE IMMEDIATELY"""
        logger.info("=" * 80)
        logger.info("INTEGRATED BTC STRATEGY STARTED - FIXED VERSION")
        logger.info("=" * 80)

        # =========================================================================
        # FIX 2: Load ALL BTC instruments at startup
        # =========================================================================
        self._load_all_btc_instruments()

        # =========================================================================
        # FIX 3: Force subscribe to current market IMMEDIATELY
        # =========================================================================
        if self.instrument_id:
            self.subscribe_quote_ticks(self.instrument_id)
            logger.info(f"✓ SUBSCRIBED to market: {self.instrument_id}")

            # Try to get current price from cache
            try:
                quote = self.cache.quote_tick(self.instrument_id)
                if quote and quote.bid_price and quote.ask_price:
                    current_price = (quote.bid_price + quote.ask_price) / 2
                    self.price_history.append(current_price)
                    logger.info(f"✓ Initial price: ${float(current_price):.4f}")
            except Exception as e:
                logger.debug(f"No initial price yet: {e}")

        # Generate synthetic history if needed
        if len(self.price_history) < 20:
            self._generate_synthetic_history(
                target_count=20, existing_count=len(self.price_history)
            )

        # =========================================================================
        # FIX 4: Start the timer loop (but don't rely on it for trading)
        # =========================================================================
        self.run_in_executor(self._start_timer_loop)

        # TradingView webhook consumer (signals queued by tradingview_webhook_receiver.py)
        if self.redis_client:
            self.run_in_executor(self._start_webhook_consumer)

        if self.grafana_exporter:
            import threading

            threading.Thread(target=self._start_grafana_sync, daemon=True).start()

        logger.info("=" * 80)
        logger.info("Strategy active - will trade every 15 minutes")
        logger.info(f"Price history: {len(self.price_history)} points")
        if len(self.price_history) >= 20:
            logger.info("✓ READY TO TRADE NOW!")
        else:
            logger.warning(f"⚠ Need more history ({len(self.price_history)}/20)")
        logger.info("=" * 80)

    def _generate_synthetic_history(
        self, target_count: int = 20, existing_count: int = 0
    ):
        """Generate synthetic price history for testing"""
        if self.price_history:
            base_price = self.price_history[-1]
        else:
            base_price = Decimal("0.5")
        needed = target_count - existing_count
        if needed <= 0:
            return
        for _ in range(needed):
            change = Decimal(str(random.uniform(-0.03, 0.03)))
            new_price = base_price * (Decimal("1.0") + change)
            new_price = max(Decimal("0.01"), min(Decimal("0.99"), new_price))
            self.price_history.append(new_price)
            base_price = new_price

    # ------------------------------------------------------------------
    # Load all BTC instruments at once
    # ------------------------------------------------------------------

    def _load_all_btc_instruments(self):
        """Load ALL BTC instruments from cache and sort by start time"""
        instruments = self.cache.instruments()
        logger.info(f"Loading ALL BTC instruments from {len(instruments)} total...")

        now = datetime.now(UTC)
        current_timestamp = int(now.timestamp())

        btc_instruments = []

        for instrument in instruments:
            try:
                if hasattr(instrument, "info") and instrument.info:
                    question = instrument.info.get("question", "").lower()
                    slug = instrument.info.get("market_slug", "").lower()

                    if ("btc" in question or "btc" in slug) and "15m" in slug:
                        try:
                            timestamp_part = slug.split("-")[-1]
                            market_timestamp = int(timestamp_part)

                            # The slug timestamp IS the market start time (Unix, no offset).
                            # end_date_iso is a DATE-only string (e.g. "2026-02-20"), NOT a datetime,
                            # so parsing it gives midnight UTC which is wrong for intraday markets.
                            # Always derive end_timestamp from the slug: start + 900s.
                            real_start_ts = market_timestamp
                            end_timestamp = (
                                market_timestamp + 900
                            )  # 15-min markets always
                            time_diff = real_start_ts - current_timestamp

                            # Only include markets that haven't ended yet
                            if end_timestamp > current_timestamp:
                                # Extract YES token ID for CLOB order book API.
                                # Nautilus instrument ID format:
                                #   {condition_id}-{token_id}.POLYMARKET
                                # The CLOB /book endpoint only accepts the token_id
                                # (the part after the dash, before .POLYMARKET).
                                raw_id = str(instrument.id)
                                # Strip .POLYMARKET suffix first
                                without_suffix = (
                                    raw_id.split(".")[0] if "." in raw_id else raw_id
                                )
                                # Then take the token_id after the condition_id dash
                                yes_token_id = (
                                    without_suffix.split("-")[-1]
                                    if "-" in without_suffix
                                    else without_suffix
                                )

                                btc_instruments.append(
                                    {
                                        "instrument": instrument,
                                        "slug": slug,
                                        "start_time": datetime.fromtimestamp(
                                            real_start_ts, tz=UTC
                                        ),
                                        "end_time": datetime.fromtimestamp(
                                            end_timestamp, tz=UTC
                                        ),
                                        "market_timestamp": market_timestamp,
                                        "end_timestamp": end_timestamp,
                                        "time_diff_minutes": time_diff / 60,
                                        "yes_token_id": yes_token_id,
                                    }
                                )
                        except (ValueError, IndexError):
                            continue
            except Exception:
                continue

        # Pair YES and NO tokens by slug.
        # Each Polymarket market has two tokens loaded as separate Nautilus instruments.
        # The first instrument found for a slug is stored as the primary (YES/UP).
        # The second instrument found for the same slug is the NO/DOWN token.
        seen_slugs = {}
        deduped = []
        for inst in btc_instruments:
            slug = inst["slug"]
            if slug not in seen_slugs:
                # First token seen = YES (UP)
                inst["yes_instrument_id"] = inst["instrument"].id
                inst["no_instrument_id"] = (
                    None  # will be filled when second token found
                )
                seen_slugs[slug] = inst
                deduped.append(inst)
            else:
                # Second token seen = NO (DOWN) — store it on the existing entry
                seen_slugs[slug]["no_instrument_id"] = inst["instrument"].id
        btc_instruments = deduped

        # Sort by start time (absolute timestamp, not time-of-day)
        btc_instruments.sort(key=lambda x: x["market_timestamp"])

        logger.info("=" * 80)
        logger.info(f"FOUND {len(btc_instruments)} BTC 15-MIN MARKETS:")
        for i, inst in enumerate(btc_instruments):
            # A market is ACTIVE if it has started AND not yet ended
            is_active = (
                inst["time_diff_minutes"] <= 0
                and inst["end_timestamp"] > current_timestamp
            )
            status = (
                "ACTIVE"
                if is_active
                else "FUTURE"
                if inst["time_diff_minutes"] > 0
                else "PAST"
            )
            logger.info(
                f"  [{i}] {inst['slug']}: {status} (starts at {inst['start_time'].strftime('%H:%M:%S')}, ends at {inst['end_time'].strftime('%H:%M:%S')})"
            )
        logger.info("=" * 80)

        self.all_btc_instruments = btc_instruments

        # Find current market and SUBSCRIBE IMMEDIATELY
        # FIXED: A market is current if it has STARTED and not yet ENDED (use end_time, not a hardcoded 15-min window)
        for i, inst in enumerate(btc_instruments):
            is_active = (
                inst["time_diff_minutes"] <= 0
                and inst["end_timestamp"] > current_timestamp
            )
            if is_active:
                self.current_instrument_index = i
                self.instrument_id = inst["instrument"].id
                self.next_switch_time = inst["end_time"]
                self._yes_token_id = inst.get("yes_token_id")
                self._yes_instrument_id = inst.get(
                    "yes_instrument_id", inst["instrument"].id
                )
                self._no_instrument_id = inst.get("no_instrument_id")
                logger.info(f"✓ CURRENT MARKET: {inst['slug']} (index {i})")
                logger.info(
                    f"  Next switch at: {self.next_switch_time.strftime('%H:%M:%S')}"
                )
                logger.info(
                    f"  YES token: {self._yes_token_id[:16]}…"
                    if self._yes_token_id
                    else "  YES token: unknown"
                )

                # =========================================================================
                # CRITICAL FIX: Subscribe immediately!
                # =========================================================================
                self.subscribe_quote_ticks(self.instrument_id)
                logger.info("  ✓ SUBSCRIBED to current market")
                break

        if self.current_instrument_index == -1 and btc_instruments:
            # No currently-active market — find the NEAREST upcoming one
            # (smallest positive time_diff_minutes = starts soonest)
            future_markets = [
                inst for inst in btc_instruments if inst["time_diff_minutes"] > 0
            ]
            if future_markets:
                nearest = min(future_markets, key=lambda x: x["time_diff_minutes"])
                nearest_idx = btc_instruments.index(nearest)
            else:
                # All markets are in the past — use the last one
                nearest = btc_instruments[-1]
                nearest_idx = len(btc_instruments) - 1

            self.current_instrument_index = nearest_idx
            inst = nearest
            self.instrument_id = inst["instrument"].id
            self._yes_token_id = inst.get("yes_token_id")
            self._yes_instrument_id = inst.get(
                "yes_instrument_id", inst["instrument"].id
            )
            self._no_instrument_id = inst.get("no_instrument_id")
            self.next_switch_time = inst["start_time"]  # switch_time = when it OPENS
            logger.info(
                f"⚠ NO CURRENT MARKET - WAITING FOR NEAREST FUTURE: {inst['slug']}"
            )
            logger.info(
                f"  Starts in {inst['time_diff_minutes']:.1f} min at {self.next_switch_time.strftime('%H:%M:%S')} UTC"
            )

            # Subscribe so we get ticks when it opens
            self.subscribe_quote_ticks(self.instrument_id)
            logger.info("  ✓ SUBSCRIBED to future market")
            # Block trading until the market actually opens (timer loop sets _market_open flag)
            self._waiting_for_market_open = True

    def _switch_to_next_market(self):
        """Switch to the next market in the pre-loaded list"""
        if not self.all_btc_instruments:
            logger.error("No instruments loaded!")
            return False

        next_index = self.current_instrument_index + 1
        if next_index >= len(self.all_btc_instruments):
            logger.warning("No more markets available - will restart bot")
            return False

        next_market = self.all_btc_instruments[next_index]
        now = datetime.now(UTC)

        # Check if next market is ready
        if now < next_market["start_time"]:
            logger.info(
                f"Waiting for next market at {next_market['start_time'].strftime('%H:%M:%S')}"
            )
            return False

        # Switch to next market
        self.current_instrument_index = next_index
        self.instrument_id = next_market["instrument"].id
        self.next_switch_time = next_market["end_time"]
        self._yes_token_id = next_market.get("yes_token_id")
        self._yes_instrument_id = next_market.get(
            "yes_instrument_id", next_market["instrument"].id
        )
        self._no_instrument_id = next_market.get("no_instrument_id")

        logger.info("=" * 80)
        logger.info(f"SWITCHING TO NEXT MARKET: {next_market['slug']}")
        logger.info(f"  Current time: {now.strftime('%H:%M:%S')}")
        logger.info(f"  Market ends at: {self.next_switch_time.strftime('%H:%M:%S')}")
        logger.info("=" * 80)

        # =========================================================================
        # FIX 5: Force stability for new market and reset trade timer correctly
        # =========================================================================
        self._stable_tick_count = QUOTE_STABILITY_REQUIRED  # Force stable immediately
        self._market_stable = True
        self._waiting_for_market_open = False  # Market is now active

        # Reset trade timer so we trade at the NEXT quote we receive
        # Use -1 so any interval will trigger (same as startup)
        self.last_trade_time = -1
        logger.info("  Trade timer reset — will trade on next tick")

        self.subscribe_quote_ticks(self.instrument_id)
        self._ensure_next_subscribed()
        return True

    def _ensure_next_subscribed(self):
        """Pre-subscribe the next market (N+1) so its book is warm before rollover.

        A TradingView alert fires at the bar close = window expiry; without a
        live quote for the freshly-opened window the signal would trade the
        expiring ~$0.99 book (no edge) or be discarded. Subscribing ahead keeps
        `_last_quote_by_instrument` fresh for N+1. Idempotent via
        `_subscribed_instruments`.
        """
        idx = self.current_instrument_index
        if idx < 0 or idx + 1 >= len(self.all_btc_instruments):
            return
        nxt = self.all_btc_instruments[idx + 1]
        inst_id = nxt["instrument"].id
        if inst_id in self._subscribed_instruments:
            return
        try:
            self.subscribe_quote_ticks(inst_id)
            self._subscribed_instruments.add(inst_id)
            logger.info(f"  ✓ Pre-subscribed NEXT market: {nxt['slug']}")
        except Exception as e:
            logger.warning(f"Could not pre-subscribe next market: {e}")

    # ------------------------------------------------------------------
    # Timer loop - SIMPLIFIED
    # ------------------------------------------------------------------

    def _start_timer_loop(self):
        """Start timer loop in executor"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._timer_loop())
        finally:
            loop.close()

    async def _timer_loop(self):
        """
        Timer loop: checks every 10 seconds if it's time to switch markets.
        Also handles the case where we're waiting for a future market to open.
        """
        while True:
            # --- auto-restart check ---
            uptime_minutes = (
                datetime.now(UTC) - self.bot_start_time
            ).total_seconds() / 60
            if uptime_minutes >= self.restart_after_minutes:
                logger.warning("AUTO-RESTART TIME - Loading fresh filters")
                import signal as _signal

                os.kill(os.getpid(), _signal.SIGTERM)
                return

            now = datetime.now(UTC)

            # Keep the next market (N+1) pre-subscribed so its book is warm
            # before the rollover boundary (cheap; idempotent).
            self._ensure_next_subscribed()

            if self.next_switch_time and now >= self.next_switch_time:
                if self._waiting_for_market_open:
                    # The future market we were waiting for has now opened
                    # Treat it like a market switch so trade timer resets
                    logger.info("=" * 80)
                    logger.info(
                        f"⏰ WAITING MARKET NOW OPEN: {now.strftime('%H:%M:%S')} UTC"
                    )
                    logger.info("=" * 80)
                    # Update next_switch_time to the market's END time
                    if (
                        self.current_instrument_index >= 0
                        and self.current_instrument_index
                        < len(self.all_btc_instruments)
                    ):
                        current_market = self.all_btc_instruments[
                            self.current_instrument_index
                        ]
                        self.next_switch_time = current_market["end_time"]
                        logger.info(
                            f"  Market ends at {self.next_switch_time.strftime('%H:%M:%S')} UTC"
                        )
                    self._waiting_for_market_open = False
                    self._market_stable = True
                    self._stable_tick_count = QUOTE_STABILITY_REQUIRED
                    self.last_trade_time = -1  # Trade immediately on next tick
                    logger.info("  ✓ MARKET OPEN — ready to trade on next tick")
                else:
                    # Normal market switch
                    self._switch_to_next_market()

            await asyncio.sleep(10)

    # ------------------------------------------------------------------
    # Quote tick handler - SIMPLIFIED
    # ------------------------------------------------------------------

    def on_quote_tick(self, tick: QuoteTick):
        """Handle quote tick - TRADE when market opens and at each 15-min boundary"""
        try:
            now = datetime.now(UTC)
            bid = tick.bid_price
            ask = tick.ask_price

            if bid is None or ask is None:
                return

            try:
                bid_decimal = bid.as_decimal()
                ask_decimal = ask.as_decimal()
            except:
                return

            # Cache the latest quote for EVERY subscribed instrument (current AND
            # the pre-subscribed next market). At rollover a TradingView signal
            # prices the fresh N+1 market from this cache instead of the expiring
            # one — see _handle_tradingview_signal / _ensure_next_subscribed.
            self._last_quote_by_instrument[tick.instrument_id] = (
                bid_decimal,
                ask_decimal,
                time.time(),
            )

            # Everything below is the fusion trading path — current market only.
            if self.instrument_id is None or tick.instrument_id != self.instrument_id:
                return

            # Always store price history
            mid_price = (bid_decimal + ask_decimal) / 2
            self.price_history.append(mid_price)
            if len(self.price_history) > self.max_history:
                self.price_history.pop(0)

            # Store latest bid/ask for liquidity check before order placement
            self._last_bid_ask = (bid_decimal, ask_decimal)

            # Tick buffer for TickVelocityProcessor (rolling 90s window)
            self._tick_buffer.append({"ts": now, "price": mid_price})

            # Stability gate
            if not self._market_stable:
                self._stable_tick_count += 1
                if self._stable_tick_count >= 1:
                    self._market_stable = True
                    logger.info("✓ Market STABLE immediately")
                else:
                    return

            # =========================================================================
            # FIXED TRADING LOGIC:
            #
            # We trade once per 15-min market interval.
            # Instead of checking wall-clock 15-min boundaries (which caused the 2-hour
            # wait), we use a simple counter keyed to the Polymarket market's OWN
            # start time.
            #
            # The market's start_time is stored in all_btc_instruments[current_index].
            # Within each market, we compute a "sub-interval" index:
            #   sub_interval = elapsed_seconds_since_market_open // 900
            # Trade ID = (market_start_timestamp, sub_interval)
            # This fires once at market open AND once after every 15 min within
            # the same market if it's a multi-interval market.
            #
            # If _waiting_for_market_open is True (started before market opens),
            # we block trading until the timer loop calls _switch_to_next_market.
            # =========================================================================

            # Block trading if waiting for a future market to open
            if self._waiting_for_market_open:
                return

            # Get current market info
            if (
                self.current_instrument_index < 0
                or self.current_instrument_index >= len(self.all_btc_instruments)
            ):
                return

            current_market = self.all_btc_instruments[self.current_instrument_index]
            market_start_ts = current_market[
                "market_timestamp"
            ]  # Slug timestamp = market start (Unix)

            # How many 15-min intervals have elapsed since this market opened?
            elapsed_secs = now.timestamp() - market_start_ts
            if elapsed_secs < 0:
                # Market hasn't started yet — block
                return

            sub_interval = int(elapsed_secs // MARKET_INTERVAL_SECONDS)

            # Unique trade key: (market_start_timestamp, sub_interval)
            trade_key = (market_start_ts, sub_interval)

            # =========================================================================
            # TRADE WINDOW: minutes 13–14 of each 15-min market (780–840 seconds in)
            #
            # WHY LATE IN THE MARKET:
            #   At 13 minutes in, the UP/DOWN result is nearly decided. The price IS
            #   the trend — if YES is at $0.78, BTC went up during this interval.
            #   We're not predicting anymore, we're reading a nearly-resolved outcome.
            #
            # WHY NOT EARLIER (the old 30–90s window):
            #   At 30 seconds in, nobody knows which way BTC will move. The signals
            #   have no edge. This is why we were losing at prices near $0.50.
            #
            # TREND FILTER (applied in _make_trading_decision):
            #   Price > 0.60 → clear UP trend → buy YES
            #   Price < 0.40 → clear DOWN trend → buy NO
            #   Price 0.40–0.60 → coin flip → SKIP (don't trade)
            #
            # Share count intuition:
            #   1.4 shares = price $0.71 → strong trend, win rate ~71%
            #   1.9 shares = price $0.53 → weak trend, near coin flip
            #   2.0+ shares = price $0.50 → pure coin flip, SKIP
            # =========================================================================
            seconds_into_sub_interval = elapsed_secs % MARKET_INTERVAL_SECONDS
            TRADE_WINDOW_START = 780  # 13 minutes in
            TRADE_WINDOW_END = 840  # 14 minutes in (60s window)

            if (
                TRADE_WINDOW_START <= seconds_into_sub_interval < TRADE_WINDOW_END
                and trade_key != self.last_trade_time
            ):
                self.last_trade_time = trade_key

                logger.info("=" * 80)
                logger.info(
                    f" LATE-WINDOW TRADE: {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
                logger.info(f"   Market: {current_market['slug']}")
                logger.info(
                    f"   Sub-interval #{sub_interval} ({seconds_into_sub_interval:.1f}s in = {seconds_into_sub_interval / 60:.1f} min)"
                )
                logger.info(
                    f"   Price: ${float(mid_price):,.4f} | Bid: ${float(bid_decimal):,.4f} | Ask: ${float(ask_decimal):,.4f}"
                )
                logger.info(
                    f"   Trend strength: {'STRONG ✓' if float(mid_price) > 0.60 or float(mid_price) < 0.40 else 'WEAK — may skip'}"
                )
                logger.info(f"   Price history: {len(self.price_history)} points")
                logger.info("=" * 80)

                self.run_in_executor(
                    lambda: self._make_trading_decision_sync(float(mid_price))
                )

        except Exception as e:
            logger.error(f"Error processing quote tick: {e}")

    # ------------------------------------------------------------------
    # Trading decision (unchanged)
    # ------------------------------------------------------------------

    def _make_trading_decision_sync(self, current_price):
        from decimal import Decimal

        price_decimal = Decimal(str(current_price))
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._make_trading_decision(price_decimal))
        finally:
            loop.close()

    def _make_trading_decision_sync(self, current_price):
        """Synchronous wrapper for trading decision (called from executor)."""
        # Convert float back to Decimal for processing
        from decimal import Decimal

        price_decimal = Decimal(str(current_price))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._make_trading_decision(price_decimal))
        finally:
            loop.close()

    # ------------------------------------------------------------------
    # TradingView webhook strategy
    # ------------------------------------------------------------------

    def _start_webhook_consumer(self):
        """
        Consume TradingView signals from Redis (runs in an executor thread).

        Signals are queued by tradingview_webhook_receiver.py (separate
        process). BLPOP wakes within milliseconds of a new signal, so
        "execute immediately" is met without polling.
        """
        redis_client = self.redis_client
        if redis_client is None:
            return
        logger.info(
            "TradingView webhook consumer started "
            "(BLPOP btc_trading:tradingview_signals)"
        )
        while True:
            try:
                item = redis_client.blpop("btc_trading:tradingview_signals", timeout=5)
                if item is None:
                    continue
                self._handle_tradingview_signal(item[1])
            except Exception as e:
                logger.error(f"Webhook consumer error: {e}")
                time.sleep(2)

    def _handle_tradingview_signal(self, raw: str):
        """Validate and execute one queued TradingView signal."""
        import json

        redis_client = self.redis_client
        if redis_client is None:
            return

        try:
            payload = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning(f"TV signal unparseable, dropped: {raw!r}")
            return

        signal = payload.get("signal")
        age = time.time() - float(payload.get("received_at", 0))

        # Strategy exclusivity: drain but ignore while fusion is active
        if self.get_active_strategy() != "tradingview":
            logger.info(f"TV signal {signal} IGNORED — fusion strategy active")
            return

        if age > self._tv_signal_ttl:
            logger.warning(
                f"TV signal {signal} DISCARDED — stale "
                f"({age:.1f}s > {self._tv_signal_ttl:.0f}s)"
            )
            return

        if signal not in ("UP", "DOWN"):
            logger.warning(f"TV signal invalid, dropped: {signal!r}")
            return

        if not self.all_btc_instruments:
            logger.warning(f"TV signal {signal} DISCARDED — no markets loaded")
            return

        # Pick the market by WALL CLOCK, not current_instrument_index. The alert
        # fires at the bar close = window expiry, so floor(now/900)*900 lands on
        # the freshly-opened N+1 window (~$0.50), never the expiring one (~$0.99).
        # Identical mapping to the backtest's attach_target_tokens.
        now_ts = time.time()
        target_market = select_target_market(self.all_btc_instruments, now_ts)
        if target_market is None:
            target_ws = (
                int(now_ts) // MARKET_INTERVAL_SECONDS
            ) * MARKET_INTERVAL_SECONDS
            logger.warning(
                f"TV signal {signal} DISCARDED — no market loaded for current "
                f"15m window (start={target_ws})"
            )
            return

        # Price the FRESH market from its own (pre-subscribed) book. No fresh
        # quote => discard rather than fall back to the expiring window.
        target_instrument_id = target_market["instrument"].id
        quote = fresh_quote(
            self._last_quote_by_instrument,
            target_instrument_id,
            now_ts,
            max_age_s=self._tv_signal_ttl,
        )
        if quote is None:
            logger.warning(
                f"TV signal {signal} DISCARDED — no fresh quote for target market "
                f"{target_market['slug']} (book not warm yet)"
            )
            return
        bid_d, ask_d = quote

        # Hybrid book-agreement gate + conviction sizing. p_side is the BOUGHT
        # side's own implied probability (UP=YES mid, DOWN=1-YES mid) and
        # entry_price is that side's price — which also fixes the bug where DOWN
        # recorded the YES mid (~0.59) instead of the NO price (~0.41). The point:
        # stop entering when the book already prices our side as a deep underdog
        # (every historical loss sat below the floor). stake==0 => gate skip.
        p_side, entry_price = entry_prob_and_price(signal, float(bid_d), float(ask_d))
        stake = conviction_stake(
            p_side,
            float(POSITION_SIZE_USD),
            self._tv_min_book_prob,
            self._tv_size_full_prob,
            self._tv_size_min_frac,
        )
        if stake <= 0.0:
            logger.warning(
                f"TV signal {signal} DISCARDED — book disagrees "
                f"(p_side={p_side:.3f} < floor {self._tv_min_book_prob:.2f})"
            )
            return

        # One trade per 15-min market. Dedup key lives in Redis so the
        # 90-min auto-restart can't cause a double trade in the same market.
        market_start_ts = target_market["market_timestamp"]
        dedup_value = f"{int(market_start_ts)}:0"
        try:
            already_traded = redis_client.get("btc_trading:tv_last_traded_market")
        except Exception as e:
            logger.error(
                f"TV signal {signal} DISCARDED — Redis dedup check failed: {e}"
            )
            return
        if already_traded == dedup_value:
            logger.info(
                f"TV signal {signal} IGNORED — already traded market {dedup_value}"
            )
            return

        # Claim the market before executing (consumer is single-threaded)
        redis_client.set("btc_trading:tv_last_traded_market", dedup_value, ex=3600)

        logger.info("=" * 80)
        logger.info(
            f" TRADINGVIEW SIGNAL TRADE: {signal} | age {age:.1f}s | "
            f"p_side {p_side:.3f} | price ${entry_price:.4f} | stake ${stake:.2f} | "
            f"market {target_market['slug']}"
        )
        logger.info("=" * 80)

        self._execute_webhook_trade_sync(
            signal, entry_price, stake, target_market, (bid_d, ask_d)
        )

    def _execute_webhook_trade_sync(
        self,
        signal: str,
        current_price: float,
        stake_usd: float,
        target_market: dict,
        bid_ask: tuple,
    ):
        """Synchronous wrapper (consumer thread owns its own event loop)."""
        price_decimal = Decimal(str(current_price))
        stake_decimal = Decimal(str(stake_usd))
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(
                self._execute_webhook_trade(
                    signal, price_decimal, stake_decimal, target_market, bid_ask
                )
            )
        finally:
            loop.close()

    async def _execute_webhook_trade(
        self,
        signal: str,
        current_price: Decimal,
        stake_usd: Decimal,
        target_market: dict,
        bid_ask: tuple,
    ):
        """
        Execute a TradingView signal directly — no fusion, no trend filter.

        The TradingView indicator owns the entry decision; this path only
        applies the risk engine, the liquidity guard, and the sim/live gate
        (identical to the tail of _make_trading_decision).

        `target_market` is the FRESH (N+1) market chosen by wall clock and
        `bid_ask` its live quote — so the order, liquidity guard, and token
        ids all bind to that market, not whatever `current_instrument_index`
        happens to point at (the rollover fix).

        Dry run (btc_trading:tv_dry_run = "1"): runs the EXACT live order
        path — token resolution, instrument cache, qty/precision, order
        construction — and diverges at a single point: submit_order is not
        called. 100% fidelity with live except the submission itself.
        """
        market_slug = target_market.get("slug", "")
        yes_instrument_id = (
            target_market.get("yes_instrument_id") or target_market["instrument"].id
        )
        no_instrument_id = target_market.get("no_instrument_id")
        is_simulation = await self.check_simulation_mode()
        tv_dry_run = self.check_tv_dry_run()
        if tv_dry_run:
            logger.info("Mode: DRY RUN (live order path, submission skipped)")
        else:
            logger.info(f"Mode: {'SIMULATION' if is_simulation else 'LIVE TRADING'}")

        # UP = buy YES token ("long"), DOWN = buy NO token ("short")
        direction = "long" if signal == "UP" else "short"

        tv_signal = TradingSignal(
            timestamp=datetime.now(UTC),
            source="TradingViewWebhook",
            signal_type=SignalType.MOMENTUM,
            direction=SignalDirection.BULLISH
            if signal == "UP"
            else SignalDirection.BEARISH,
            strength=SignalStrength.STRONG,
            confidence=self._tv_confidence,
            current_price=current_price,
        )

        is_valid, error = self.risk_engine.validate_new_position(
            size=stake_usd,
            direction=direction,
            current_price=current_price,
        )
        if not is_valid:
            logger.warning(f"Risk engine blocked TradingView trade: {error}")
            return

        logger.info(
            f"Position size: ${float(stake_usd):.2f} "
            f"(conviction-scaled, cap MARKET_BUY_USD ${float(POSITION_SIZE_USD):.2f}) | "
            f"Direction: {direction.upper()}"
        )

        # Liquidity guard — same thresholds as the fusion path, but no retry
        # semantics (a webhook signal fires once; we don't re-arm the window).
        # Uses the TARGET market's quote, not self._last_bid_ask (current market).
        last_bid, last_ask = bid_ask
        MIN_LIQUIDITY = Decimal("0.02")
        if direction == "long" and last_ask <= MIN_LIQUIDITY:
            logger.warning(
                f"⚠ No liquidity for BUY: ask=${float(last_ask):.4f} — "
                "skipping TradingView trade"
            )
            return
        if direction == "short" and last_bid <= MIN_LIQUIDITY:
            logger.warning(
                f"⚠ No liquidity for SELL: bid=${float(last_bid):.4f} — "
                "skipping TradingView trade"
            )
            return

        if tv_dry_run:
            # Dry run takes precedence over sim/live: rehearse the live path
            await self._place_real_order(
                tv_signal,
                stake_usd,
                current_price,
                direction,
                dry_run=True,
                market_slug=market_slug,
                yes_instrument_id=yes_instrument_id,
                no_instrument_id=no_instrument_id,
            )
        elif is_simulation:
            await self._record_paper_trade(
                tv_signal, stake_usd, current_price, direction
            )
        else:
            await self._place_real_order(
                tv_signal,
                stake_usd,
                current_price,
                direction,
                market_slug=market_slug,
                yes_instrument_id=yes_instrument_id,
                no_instrument_id=no_instrument_id,
            )

    async def _fetch_market_context(self, current_price: Decimal) -> dict:
        """
        Fetch REAL external data to populate signal processor metadata.

        Returns a dict with:
          - sentiment_score (float 0-100): live Fear & Greed index, or None
          - spot_price (float): live BTC-USD from Coinbase, or None
          - deviation (float): polymarket price vs SMA-20 (always computed)
          - momentum (float): 5-period rate of change (always computed)
          - volatility (float): price std-dev over last 20 ticks (always computed)
        """
        current_price_float = float(current_price)

        # --- Always-available stats from local price_history ---
        recent_prices = [float(p) for p in self.price_history[-20:]]
        sma_20 = sum(recent_prices) / len(recent_prices)
        deviation = (current_price_float - sma_20) / sma_20
        momentum = (
            (current_price_float - float(self.price_history[-5]))
            / float(self.price_history[-5])
            if len(self.price_history) >= 5
            else 0.0
        )
        variance = sum((p - sma_20) ** 2 for p in recent_prices) / len(recent_prices)
        volatility = math.sqrt(variance)

        metadata = {
            "deviation": deviation,
            "momentum": momentum,
            "volatility": volatility,
            # Tick buffer for TickVelocityProcessor
            "tick_buffer": list(self._tick_buffer),
            # YES token id for OrderBookImbalanceProcessor
            "yes_token_id": self._yes_token_id,
        }

        # --- Real sentiment: Fear & Greed Index via NewsSocialDataSource ---
        try:
            from data_sources.news_social.adapter import NewsSocialDataSource

            news_source = NewsSocialDataSource()
            await news_source.connect()
            fg = await news_source.get_fear_greed_index()
            await news_source.disconnect()
            if fg and "value" in fg:
                metadata["sentiment_score"] = float(fg["value"])
                metadata["sentiment_classification"] = fg.get("classification", "")
                logger.info(
                    f"Fear & Greed: {metadata['sentiment_score']:.0f} "
                    f"({metadata['sentiment_classification']})"
                )
            else:
                logger.warning(
                    "Fear & Greed fetch returned no data — sentiment processor skipped"
                )
        except Exception as e:
            logger.warning(
                f"Could not fetch Fear & Greed index: {e} — sentiment processor skipped"
            )

        # --- Real spot price: Coinbase BTC-USD REST API ---
        try:
            from data_sources.coinbase.adapter import CoinbaseDataSource

            coinbase = CoinbaseDataSource()
            await coinbase.connect()
            spot = await coinbase.get_current_price()
            await coinbase.disconnect()
            if spot:
                metadata["spot_price"] = float(spot)
                logger.info(f"Coinbase spot price: ${float(spot):,.2f}")
            else:
                logger.warning(
                    "Coinbase price fetch returned None — divergence processor skipped"
                )
        except Exception as e:
            logger.warning(
                f"Could not fetch Coinbase spot price: {e} — divergence processor skipped"
            )

        logger.info(
            f"Market context — deviation={deviation:.2%}, "
            f"momentum={momentum:.2%}, volatility={volatility:.4f}, "
            f"sentiment={'%.0f' % metadata['sentiment_score'] if 'sentiment_score' in metadata else 'N/A'}, "
            f"spot=${'%.2f' % metadata['spot_price'] if 'spot_price' in metadata else 'N/A'}"
        )
        return metadata

    async def _make_trading_decision(self, current_price: Decimal):
        """
        Make trading decision using our 7-phase system.

        Position size is fixed at POSITION_SIZE_USD (the MARKET_BUY_USD env var,
        default $1) — no variable sizing, no risk-engine calculation needed. The
        risk engine is still used to check that we don't already have too many
        open positions.
        """
        # --- Strategy exclusivity: skip fusion path when TradingView is active ---
        if self.get_active_strategy() != "fusion":
            logger.info("Fusion decision SKIPPED — TradingView strategy active")
            return

        # --- Mode check ---
        is_simulation = await self.check_simulation_mode()
        logger.info(f"Mode: {'SIMULATION' if is_simulation else 'LIVE TRADING'}")

        # --- Minimum history guard ---
        if len(self.price_history) < 20:
            logger.warning(f"Not enough price history ({len(self.price_history)}/20)")
            return

        logger.info(f"Current price: ${float(current_price):,.4f}")

        # --- Phase 4a: Build real metadata for processors ---
        metadata = await self._fetch_market_context(current_price)

        # --- Phase 4b: Run all three signal processors ---
        signals = self._process_signals(current_price, metadata)

        if not signals:
            logger.info("No signals generated — no trade this interval")
            return

        logger.info(f"Generated {len(signals)} signal(s):")
        for sig in signals:
            logger.info(
                f"  [{sig.source}] {sig.direction.value}: "
                f"score={sig.score:.1f}, confidence={sig.confidence:.2%}"
            )

        # --- Phase 4c: Fuse signals into one consensus ---
        # min_score lowered to 40 because the TREND FILTER (price at min 11-13)
        # is now the primary decision maker. Fusion is informational context,
        # not the trade gate. The trend gate below is the real filter.
        fused = self.fusion_engine.fuse_signals(signals, min_signals=1, min_score=40.0)
        if not fused:
            logger.info("Fusion produced no actionable signal — no trade this interval")
            return

        logger.info(
            f"FUSED SIGNAL: {fused.direction.value} "
            f"(score={fused.score:.1f}, confidence={fused.confidence:.2%})"
        )

        # --- Phase 5: Position size is the fixed POSITION_SIZE_USD (module
        # constant from MARKET_BUY_USD) ---

        # =========================================================================
        # TREND FILTER — replaces signal-based direction at the late trade window
        #
        # At minute 13, the Polymarket price IS the market's verdict on BTC direction.
        # We ignore what the signal processors say and simply follow the price:
        #
        #   price > 0.60 → market says UP with >60% confidence → buy YES
        #   price < 0.40 → market says DOWN with >60% confidence → buy NO
        #   price 0.40–0.60 → too close to call → SKIP (this is where we were losing)
        #
        # This directly addresses the observation that trades at 1.9–2.0+ shares
        # (price near $0.50) almost always lose, while trades at 1.4 shares
        # (price ~$0.71) mostly win.
        # =========================================================================
        TREND_UP_THRESHOLD = 0.60  # price above this → buy YES (UP)
        TREND_DOWN_THRESHOLD = 0.40  # price below this → buy NO (DOWN)

        price_float = float(current_price)

        if price_float > TREND_UP_THRESHOLD:
            direction = "long"
            trend_confidence = price_float  # e.g. 0.72 = 72% confident UP
            logger.info(f" TREND: UP ({price_float:.2%} YES probability) → buying YES")
        elif price_float < TREND_DOWN_THRESHOLD:
            direction = "short"
            trend_confidence = 1.0 - price_float  # e.g. 0.31 price = 69% confident DOWN
            logger.info(
                f" TREND: DOWN ({price_float:.2%} YES probability = {1 - price_float:.2%} NO) → buying NO"
            )
        else:
            logger.info(
                f"⏭ TREND: NEUTRAL ({price_float:.2%}) — price too close to 0.50, SKIPPING trade "
                f"(coin flip territory: {TREND_DOWN_THRESHOLD:.0%}–{TREND_UP_THRESHOLD:.0%})"
            )
            return

        # Risk engine: only check position-count / exposure limits (no sizing math)
        is_valid, error = self.risk_engine.validate_new_position(
            size=POSITION_SIZE_USD,
            direction=direction,
            current_price=current_price,
        )
        if not is_valid:
            logger.warning(f"Risk engine blocked trade: {error}")
            return

        logger.info(
            f"Position size: ${float(POSITION_SIZE_USD):.2f} (MARKET_BUY_USD) | "
            f"Direction: {direction.upper()}"
        )

        # --- Liquidity guard: don't place if market has no real depth ---
        # The current bid/ask come from the last processed quote tick.
        # If ask <= 0.02 or bid <= 0.02, the orderbook is essentially empty
        # and a FAK (IOC market) order will be rejected immediately.
        last_tick = getattr(self, "_last_bid_ask", None)
        if last_tick:
            last_bid, last_ask = last_tick
            MIN_LIQUIDITY = Decimal("0.02")
            if direction == "long" and last_ask <= MIN_LIQUIDITY:
                logger.warning(
                    f"⚠ No liquidity for BUY: ask=${float(last_ask):.4f} ≤ {float(MIN_LIQUIDITY):.2f} — skipping trade, will retry next tick"
                )
                self.last_trade_time = -1  # Allow retry next tick
                return
            if direction == "short" and last_bid <= MIN_LIQUIDITY:
                logger.warning(
                    f"⚠ No liquidity for SELL: bid=${float(last_bid):.4f} ≤ {float(MIN_LIQUIDITY):.2f} — skipping trade, will retry next tick"
                )
                self.last_trade_time = -1  # Allow retry next tick
                return

        # --- Phase 5 / 6: Execute ---
        if is_simulation:
            await self._record_paper_trade(
                fused, POSITION_SIZE_USD, current_price, direction
            )
        else:
            await self._place_real_order(
                fused, POSITION_SIZE_USD, current_price, direction
            )

    async def _record_paper_trade(
        self, signal, position_size, current_price, direction
    ):
        exit_delta = timedelta(minutes=1) if self.test_mode else timedelta(minutes=15)
        exit_time = datetime.now(UTC) + exit_delta

        if "BULLISH" in str(signal.direction):
            movement = random.uniform(-0.02, 0.08)
        else:
            movement = random.uniform(-0.08, 0.02)

        exit_price = current_price * (Decimal("1.0") + Decimal(str(movement)))
        exit_price = max(Decimal("0.01"), min(Decimal("0.99"), exit_price))

        if direction == "long":
            pnl = position_size * (exit_price - current_price) / current_price
        else:
            pnl = position_size * (current_price - exit_price) / current_price

        outcome = "WIN" if pnl > 0 else "LOSS"
        paper_trade = PaperTrade(
            timestamp=datetime.now(UTC),
            direction=direction.upper(),
            size_usd=float(position_size),
            price=float(current_price),
            signal_score=signal.score,
            signal_confidence=signal.confidence,
            outcome=outcome,
        )
        self.paper_trades.append(paper_trade)

        self.performance_tracker.record_trade(
            trade_id=f"paper_{int(datetime.now().timestamp())}",
            direction=direction,
            entry_price=current_price,
            exit_price=exit_price,
            size=position_size,
            entry_time=datetime.now(UTC),
            exit_time=exit_time,
            signal_score=signal.score,
            signal_confidence=signal.confidence,
            metadata={
                "simulated": True,
                "num_signals": signal.num_signals
                if hasattr(signal, "num_signals")
                else 1,
                "fusion_score": signal.score,
            },
        )

        if hasattr(self, "grafana_exporter") and self.grafana_exporter:
            self.grafana_exporter.increment_trade_counter(won=(pnl > 0))
            self.grafana_exporter.record_trade_duration(exit_delta.total_seconds())

        logger.info("=" * 80)
        logger.info("[SIMULATION] PAPER TRADE RECORDED")
        logger.info(f"  Direction: {direction.upper()}")
        logger.info(f"  Size: ${float(position_size):.2f}")
        logger.info(f"  Entry Price: ${float(current_price):,.4f}")
        logger.info(f"  Simulated Exit: ${float(exit_price):,.4f}")
        logger.info(f"  Simulated P&L: ${float(pnl):+.2f} ({movement * 100:+.2f}%)")
        logger.info(f"  Outcome: {outcome}")
        logger.info(f"  Total Paper Trades: {len(self.paper_trades)}")
        logger.info("=" * 80)

        self._save_paper_trades()

    def _save_paper_trades(self):
        import json

        try:
            trades_data = [t.to_dict() for t in self.paper_trades]
            with open("paper_trades.json", "w") as f:
                json.dump(trades_data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save paper trades: {e}")

    # ------------------------------------------------------------------
    # Real order (unchanged)
    # ------------------------------------------------------------------

    async def _place_real_order(
        self,
        signal,
        position_size,
        current_price,
        direction,
        dry_run: bool = False,
        market_slug: str = "",
        yes_instrument_id=None,
        no_instrument_id=None,
    ):
        """
        Place a real order. With dry_run=True the ENTIRE path runs identically
        (token resolution, instrument cache, qty/precision, order construction)
        and diverges at exactly one point: submit_order is not called.

        `yes_instrument_id`/`no_instrument_id` override the current-market token
        ids — the webhook path passes the TARGET (N+1) market's ids so the order
        binds to the fresh window. They default to the current market's ids
        (`self._yes_instrument_id`/`self._no_instrument_id`) for the fusion path.
        """
        yes_instrument_id = yes_instrument_id or getattr(
            self, "_yes_instrument_id", self.instrument_id
        )
        no_instrument_id = (
            no_instrument_id
            if no_instrument_id is not None
            else getattr(self, "_no_instrument_id", None)
        )
        if not yes_instrument_id:
            logger.error("No instrument available")
            return

        try:
            # instrument is fetched below after determining YES vs NO token

            logger.info("=" * 80)
            if dry_run:
                logger.info("DRY RUN - LIVE ORDER PATH (submission will be skipped)")
            else:
                logger.info("LIVE MODE - PLACING REAL ORDER!")
            logger.info("=" * 80)

            # On Polymarket, both UP and DOWN are BUY orders.
            # Bullish = buy YES token (self._yes_instrument_id)
            # Bearish = buy NO token  (self._no_instrument_id)
            # There is NO sell — you always buy whichever side you want.
            side = OrderSide.BUY

            if direction == "long":
                trade_instrument_id = yes_instrument_id
                trade_label = "YES (UP)"
            else:
                if no_instrument_id is None:
                    logger.warning(
                        "NO token instrument not found for this market — "
                        "cannot bet DOWN. Skipping trade."
                    )
                    return
                trade_instrument_id = no_instrument_id
                trade_label = "NO (DOWN)"

            instrument = self.cache.instrument(trade_instrument_id)
            if not instrument:
                logger.error(f"Instrument not in cache: {trade_instrument_id}")
                return

            logger.info(f"Buying {trade_label} token: {trade_instrument_id}")

            trade_price = float(current_price)
            max_usd_amount = float(position_size)

            precision = instrument.size_precision

            # Always BUY — the market-order patch converts this to a USD amount.
            # Pass dummy qty=5 (minimum) so Nautilus risk engine doesn't deny it.
            min_qty_val = float(getattr(instrument, "min_quantity", None) or 5.0)
            token_qty = max(min_qty_val, 5.0)
            token_qty = round(token_qty, precision)
            logger.info(
                f"BUY {trade_label}: dummy qty={token_qty:.6f} "
                f"(patch converts to ${max_usd_amount:.2f} USD)"
            )

            qty = Quantity(token_qty, precision=precision)
            timestamp_ms = int(time.time() * 1000)
            unique_id = f"BTC-15MIN-${max_usd_amount:.0f}-{timestamp_ms}"

            # Carry the intended USD on the order so the market-order patch spends
            # exactly this (conviction-scaled) amount, not the static env knob —
            # otherwise live would ignore sizing while dry run records it, breaking
            # the dry-run==live invariant. Fusion passes MARKET_BUY_USD so its tag
            # equals the env default (unchanged behaviour).
            order = self.order_factory.market(
                instrument_id=trade_instrument_id,
                order_side=side,
                quantity=qty,
                client_order_id=ClientOrderId(unique_id),
                quote_quantity=False,
                time_in_force=TimeInForce.IOC,
                tags=[f"tv_usd={max_usd_amount:.4f}"],
            )

            # SINGLE DIVERGENCE POINT between dry run and live: the order was
            # built and validated identically; dry run only skips submission.
            if dry_run:
                logger.info("DRY RUN - ORDER BUILT AND VALIDATED, NOT SUBMITTED")
                logger.info(f"  Order ID: {unique_id}")
                logger.info(f"  Direction: {trade_label}")
                logger.info("  Side: BUY")
                logger.info(f"  Token Quantity: {token_qty:.6f}")
                logger.info(f"  Estimated Cost: ~${max_usd_amount:.2f}")
                logger.info(f"  Price: ${trade_price:.4f}")
                logger.info("=" * 80)
                self._record_tv_dry_run_trade(
                    signal=signal,
                    direction=direction,
                    trade_label=trade_label,
                    price=trade_price,
                    usd_amount=max_usd_amount,
                    token_qty=token_qty,
                    order_id=unique_id,
                    instrument_id=str(trade_instrument_id),
                    market_slug=market_slug,
                )
                return

            self.submit_order(order)

            logger.info("REAL ORDER SUBMITTED!")
            logger.info(f"  Order ID: {unique_id}")
            logger.info(f"  Direction: {trade_label}")
            logger.info("  Side: BUY")
            logger.info(f"  Token Quantity: {token_qty:.6f}")
            logger.info(f"  Estimated Cost: ~${max_usd_amount:.2f}")
            logger.info(f"  Price: ${trade_price:.4f}")
            logger.info("=" * 80)

            self._track_order_event("placed")

        except Exception as e:
            logger.error(f"Error placing real order: {e}")
            import traceback

            traceback.print_exc()
            if not dry_run:
                self._track_order_event("rejected")

    def _record_tv_dry_run_trade(
        self,
        signal,
        direction: str,
        trade_label: str,
        price: float,
        usd_amount: float,
        token_qty: float,
        order_id: str,
        instrument_id: str,
        market_slug: str,
    ):
        """Append a dry-run trade record to tv_dry_run_trades.json."""
        import json

        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "source": getattr(signal, "source", "TradingViewWebhook"),
            "signal_direction": str(getattr(signal, "direction", "")),
            "signal_confidence": getattr(signal, "confidence", None),
            "direction": direction,
            "trade_label": trade_label,
            "price": price,
            "usd_amount": usd_amount,
            "token_qty": token_qty,
            "order_id": order_id,
            "instrument_id": instrument_id,
            "market_slug": market_slug,
        }
        try:
            trades = []
            if os.path.exists("tv_dry_run_trades.json"):
                with open("tv_dry_run_trades.json") as f:
                    trades = json.load(f)
            trades.append(record)
            with open("tv_dry_run_trades.json", "w") as f:
                json.dump(trades, f, indent=2)
            logger.info(f"Dry-run trade recorded ({len(trades)} total)")
        except Exception as e:
            logger.error(f"Failed to record dry-run trade: {e}")

    # ------------------------------------------------------------------
    # Signal processing
    # ------------------------------------------------------------------

    def _process_signals(self, current_price, metadata=None):
        signals = []
        if metadata is None:
            metadata = {}

        processed_metadata = {}
        for key, value in metadata.items():
            if isinstance(value, float):
                processed_metadata[key] = Decimal(str(value))
            else:
                processed_metadata[key] = value

        spike_signal = self.spike_detector.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if spike_signal:
            signals.append(spike_signal)

        if "sentiment_score" in processed_metadata:
            sentiment_signal = self.sentiment_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if sentiment_signal:
                signals.append(sentiment_signal)

        if "spot_price" in processed_metadata:
            divergence_signal = self.divergence_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if divergence_signal:
                signals.append(divergence_signal)

        # --- Order Book Imbalance (real-time Polymarket CLOB depth) ---
        if processed_metadata.get("yes_token_id"):
            ob_signal = self.orderbook_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if ob_signal:
                signals.append(ob_signal)

        # --- Tick Velocity (last 60s of Polymarket probability movement) ---
        if processed_metadata.get("tick_buffer"):
            tv_signal = self.tick_velocity_processor.process(
                current_price=current_price,
                historical_prices=self.price_history,
                metadata=processed_metadata,
            )
            if tv_signal:
                signals.append(tv_signal)

        # --- Deribit Put/Call Ratio (institutional options sentiment) ---
        pcr_signal = self.deribit_pcr_processor.process(
            current_price=current_price,
            historical_prices=self.price_history,
            metadata=processed_metadata,
        )
        if pcr_signal:
            signals.append(pcr_signal)

        return signals

    # ------------------------------------------------------------------
    # Order events
    # ------------------------------------------------------------------

    def _track_order_event(self, event_type: str) -> None:
        """
        Safely track an order event on the performance tracker.

        PerformanceTracker does not expose `increment_order_counter`, so we
        use whichever method is actually available, or fall back to a no-op.
        Supported event_type values: "placed", "filled", "rejected".
        """
        try:
            pt = self.performance_tracker
            # Try the method that actually exists first
            if hasattr(pt, "record_order_event"):
                pt.record_order_event(event_type)
            elif hasattr(pt, "increment_counter"):
                pt.increment_counter(event_type)
            elif hasattr(pt, "increment_order_counter"):
                pt.increment_order_counter(event_type)
            else:
                # No suitable method found – log and carry on
                logger.debug(
                    f"PerformanceTracker has no order-counter method; "
                    f"ignoring event '{event_type}'"
                )
        except Exception as e:
            logger.warning(f"Failed to track order event '{event_type}': {e}")

    def on_order_filled(self, event):
        logger.info("=" * 80)
        logger.info("ORDER FILLED!")
        logger.info(f"  Order: {event.client_order_id}")
        logger.info(f"  Fill Price: ${float(event.last_px):.4f}")
        logger.info(f"  Quantity: {float(event.last_qty):.6f}")
        logger.info("=" * 80)
        self._track_order_event("filled")

    def on_order_denied(self, event):
        logger.error("=" * 80)
        logger.error("ORDER DENIED!")
        logger.error(f"  Order: {event.client_order_id}")
        logger.error(f"  Reason: {event.reason}")
        logger.error("=" * 80)
        self._track_order_event("rejected")

    def on_order_rejected(self, event):
        """Handle order rejection — reset trade timer so we can retry next tick."""
        reason = str(getattr(event, "reason", ""))
        reason_lower = reason.lower()
        if (
            "no orders found" in reason_lower
            or "fak" in reason_lower
            or "no match" in reason_lower
        ):
            logger.warning(
                f"⚠ FAK rejected (no liquidity) — resetting timer to retry next tick\n"
                f"  Reason: {reason}"
            )
            self.last_trade_time = -1  # Allow retry on next quote tick
        else:
            logger.warning(f"Order rejected: {reason}")

    # ------------------------------------------------------------------
    # Grafana / stop
    # ------------------------------------------------------------------

    def _start_grafana_sync(self):
        import asyncio

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self.grafana_exporter.start())
            logger.info("Grafana metrics started on port 8000")
        except Exception as e:
            logger.error(f"Failed to start Grafana: {e}")

    def on_stop(self):
        logger.info("Integrated BTC strategy stopped")
        logger.info(f"Total paper trades recorded: {len(self.paper_trades)}")
        if self.grafana_exporter:
            import asyncio

            try:
                loop = asyncio.new_event_loop()
                loop.run_until_complete(self.grafana_exporter.stop())
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_integrated_bot(
    simulation: bool = False, enable_grafana: bool = True, test_mode: bool = False
):
    """Run the integrated BTC 15-min trading bot - LOADS ALL BTC MARKETS FOR THE DAY"""

    print("=" * 80)
    print("INTEGRATED POLYMARKET BTC 15-MIN TRADING BOT")
    print("Nautilus + 7-Phase System + Redis Control")
    print("=" * 80)

    redis_client = init_redis()

    if redis_client:
        try:
            # ALWAYS overwrite Redis with the current session mode.
            # This prevents a stale value from a previous --live run
            # silently overriding --test-mode or --simulation runs.
            mode_value = "1" if simulation else "0"
            redis_client.set("btc_trading:simulation_mode", mode_value)
            mode_label = "SIMULATION" if simulation else "LIVE"
            logger.info(f"Redis simulation_mode forced to: {mode_label} ({mode_value})")
        except Exception as e:
            logger.warning(f"Could not set Redis simulation mode: {e}")

    print("\nConfiguration:")
    print(f"  Initial Mode: {'SIMULATION' if simulation else 'LIVE TRADING'}")
    print(f"  Redis Control: {'Enabled' if redis_client else 'Disabled'}")
    print(f"  Grafana: {'Enabled' if enable_grafana else 'Disabled'}")
    print(f"  Max Trade Size: ${os.getenv('MARKET_BUY_USD', '1.00')}")
    print(f"  Quote stability gate: {QUOTE_STABILITY_REQUIRED} valid ticks")
    print()

    now = datetime.now(UTC)

    # =========================================================================
    # Slug timestamps ARE standard Unix timestamps (no offset) aligned to
    # 15-min boundaries. Generate slugs for current + next 24 hours.
    # =========================================================================
    now = datetime.now(UTC)
    unix_interval_start = (int(now.timestamp()) // 900) * 900  # current 15-min boundary

    btc_slugs = []
    for i in range(
        -1, 97
    ):  # include 1 prior interval (in case we're just after boundary)
        timestamp = unix_interval_start + (i * 900)
        btc_slugs.append(f"btc-updown-15m-{timestamp}")

    filters = {
        "active": True,
        "closed": False,
        "archived": False,
        "slug": tuple(btc_slugs),
        "limit": 100,
    }

    logger.info("=" * 80)
    logger.info("LOADING BTC 15-MIN MARKETS BY SLUG")
    logger.info(f"  Interval start: {unix_interval_start} | Count: {len(btc_slugs)}")
    logger.info(f"  First: {btc_slugs[0]}  Last: {btc_slugs[-1]}")
    logger.info("=" * 80)

    instrument_cfg = InstrumentProviderConfig(
        load_all=True,
        filters=filters,
        use_gamma_markets=True,
    )

    poly_data_cfg = PolymarketDataClientConfig(
        private_key=os.getenv("POLYMARKET_PK"),
        api_key=os.getenv("POLYMARKET_API_KEY"),
        api_secret=os.getenv("POLYMARKET_API_SECRET"),
        passphrase=os.getenv("POLYMARKET_PASSPHRASE"),
        signature_type=1,
        instrument_provider=instrument_cfg,
    )

    poly_exec_cfg = PolymarketExecClientConfig(
        private_key=os.getenv("POLYMARKET_PK"),
        api_key=os.getenv("POLYMARKET_API_KEY"),
        api_secret=os.getenv("POLYMARKET_API_SECRET"),
        passphrase=os.getenv("POLYMARKET_PASSPHRASE"),
        signature_type=1,
        instrument_provider=instrument_cfg,
    )

    config = TradingNodeConfig(
        environment="live",
        trader_id="BTC-15MIN-INTEGRATED-001",
        logging=LoggingConfig(
            log_level="INFO",
            log_directory="./logs/nautilus",
        ),
        data_engine=LiveDataEngineConfig(qsize=6000),
        exec_engine=LiveExecEngineConfig(qsize=6000),
        risk_engine=LiveRiskEngineConfig(bypass=simulation),
        data_clients={POLYMARKET: poly_data_cfg},
        exec_clients={POLYMARKET: poly_exec_cfg},
    )

    strategy = IntegratedBTCStrategy(
        redis_client=redis_client,
        enable_grafana=enable_grafana,
        test_mode=test_mode,
    )

    print("\nBuilding Nautilus node...")
    node = TradingNode(config=config)
    node.add_data_client_factory(POLYMARKET, PolymarketLiveDataClientFactory)
    node.add_exec_client_factory(POLYMARKET, PolymarketLiveExecClientFactory)
    node.trader.add_strategy(strategy)
    node.build()
    logger.info("Nautilus node built successfully")

    print()
    print("=" * 80)
    print("BOT STARTING")
    print("=" * 80)

    try:
        node.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        node.dispose()
        logger.info("Bot stopped")


def main():
    # Tee loguru output to a rotating file so each run is auditable after the
    # fact (the stack launches each component in its own console window with no
    # file sink). Operational log only — NOT backtest.db (recorder's domain).
    from log_setup import setup_file_logging

    setup_file_logging("bot.log")
    logger.info("File logging enabled -> logs/bot.log")

    import argparse

    parser = argparse.ArgumentParser(description="Integrated BTC 15-Min Trading Bot")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run in LIVE mode (real money at risk!). Default is simulation.",
    )
    parser.add_argument(
        "--no-grafana", action="store_true", help="Disable Grafana metrics"
    )
    parser.add_argument(
        "--test-mode",
        action="store_true",
        help="Run in TEST MODE (trade every minute for faster testing)",
    )

    args = parser.parse_args()
    enable_grafana = not args.no_grafana
    test_mode = args.test_mode

    # --test-mode ALWAYS forces simulation even if --live is also passed
    if args.test_mode:
        simulation = True
    else:
        simulation = not args.live

    if not simulation:
        logger.warning("=" * 80)
        logger.warning("LIVE TRADING MODE — REAL MONEY AT RISK!")
        logger.warning("=" * 80)
    else:
        logger.info("=" * 80)
        logger.info(
            f"SIMULATION MODE — {'TEST MODE (fast clock)' if test_mode else 'paper trading only'}"
        )
        logger.info("No real orders will be placed.")
        logger.info("=" * 80)

    run_integrated_bot(
        simulation=simulation, enable_grafana=enable_grafana, test_mode=test_mode
    )


if __name__ == "__main__":
    main()
