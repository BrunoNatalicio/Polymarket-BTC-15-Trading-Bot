---
type: doc
name: glossary
description: Project terminology, type definitions, domain entities, and business rules
category: glossary
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Glossary & Domain Concepts

The bot trades binary "up/down" prediction markets on Polymarket tied to the price of BTC over rolling
15-minute windows. Domain concepts span three areas: market-data signal processing (spikes, divergence, sentiment,
order book imbalance, tick velocity, put/call ratio), signal fusion/decision-making (`FusedSignal`, weighted
voting), and order execution on Polymarket's CLOB (YES/NO tokens, position sizing, sim/live/dry-run modes).

## Type Definitions

- [`TradingSignal`](../../core/strategy_brain/signal_processors/base_processor.py:44) - output of every signal
  processor: `direction`, `confidence`, `strength`
- [`FusedSignal`](../../core/strategy_brain/fusion_engine/signal_fusion.py:23) - combined signal from
  `SignalFusionEngine` with `score` and `confidence`
- [`Order`](../../execution/execution_engine.py:52) - represents an order through its lifecycle
- [`PositionRisk`](../../execution/risk_engine.py:37) - per-position risk state used by `RiskEngine`
- [`RiskLimits`](../../execution/risk_engine.py:25) - configured risk thresholds (incl. max-$1 cap)
- [`Trade`](../../monitoring/performance_tracker.py:16) / [`PerformanceMetrics`](../../monitoring/performance_tracker.py:34) -
  recorded trade outcome and aggregated metrics
- [`SignalPerformance`](../../feedback/learning_engine.py:22) - per-signal performance used to re-weight fusion
- [`PaperTrade`](../../bot.py:106) - simulated trade record used in `--test-mode`
- [`Event`](../../core/nautilus_core/event_dispatcher/dispatcher.py:31) - internal event passed through
  `EventDispatcher`

## Enumerations

- [`SignalType`](../../core/strategy_brain/signal_processors/base_processor.py:14)
- [`SignalStrength`](../../core/strategy_brain/signal_processors/base_processor.py:26)
- [`SignalDirection`](../../core/strategy_brain/signal_processors/base_processor.py:35) - `BULLISH` / `BEARISH` / `NEUTRAL`
- [`RiskLevel`](../../execution/risk_engine.py:15)
- [`OrderType`](../../execution/execution_engine.py:23)
- [`OrderStatus`](../../execution/execution_engine.py:32) - pending -> filled/cancelled
- [`OrderSide`](../../execution/execution_engine.py:44)
- [`EventType`](../../core/nautilus_core/event_dispatcher/dispatcher.py:15)
- [`ConnectionState`](../../core/ingestion/managers/websocket_manager.py:14)
- [`ValidationRule`](../../core/ingestion/validators/data_validator.py:15) /
  [`ValidationResult`](../../core/ingestion/validators/data_validator.py:26)

## Core Terms

- **Fusion path** - the default strategy (`btc_trading:active_strategy == "fusion"`): combines all signal
  processors via `SignalFusionEngine` into a `FusedSignal`.
- **TradingView path** - alternative strategy where TradingView alerts are the sole trade trigger, bypassing
  fusion entirely (`_make_trading_decision` in `bot.py`).
- **Actionable signal** - a `FusedSignal` with `score >= 60` and `confidence >= 0.6`.
- **Strong signal** - a `FusedSignal` with `score >= 70`.
- **Sim mode** - `btc_trading:simulation_mode` Redis key (DB 2); orders are paper trades, not sent to Polymarket.
  Default when the key is absent.
- **Live mode** - real orders sent via `execution/polymarket_client.py`.
- **Dry run** - `btc_trading:tv_dry_run` Redis key, TradingView-only; runs the full live order path with
  `submit_order` skipped, recorded to `tv_dry_run_trades.json`. Takes precedence over sim/live for webhook trades.
- **YES / NO token** - the two outcome tokens of a Polymarket binary market. `UP` signal -> buy YES ("long");
  `DOWN` signal -> buy NO ("short").
- **15m market slug** - identifier for the active 15-minute BTC up/down market, resolved by
  `current_btc_15m_slug` / `get_next_btc_15m_markets` in `execution/nautilus_polymarket_integration.py`.
- **Rollover / N+1 window** - the boundary at `:00/:15/:30/:45` where one 15m window expires and the next opens.
  A TradingView alert fires at the bar close, so the webhook path targets the freshly-opened **N+1** window
  (`floor(now/900)*900`, via `tv_market_select.select_target_market`) instead of the expiring one (~$0.99).
- **Taker / maker** - a **taker** removes liquidity (market order, what the bot sends); a **maker** posts a
  resting limit order. On 15m/5m crypto only takers pay a fee; makers are free and earn rebates.
- **Taker fee** - `fee = C × feeRate × p × (1 − p)`, where `C` = shares traded (`stake / p`) and crypto
  `feeRate = 0.07`; charged in shares on a buy;
  peaks near $0.50, negligible at the extremes. Gasless/Builder do not waive it. Modeled in the backtest
  (`matching.simulate_market_buy`, `bot_trades.evaluate_bot_trades`); see backtest-validation.md §4.
- **Signal weight** - per-processor contribution to the fused score. `SignalFusionEngine` class defaults are
  Spike 40%, Divergence 30%, Sentiment 20%, others 10%, but `bot.py` overrides them at startup (OrderBook 30%,
  TickVelocity 25%, Divergence 18%, Spike 12%, DeribitPCR 10%, Sentiment 5%); adjusted at runtime only by
  `LearningEngine`.

## Acronyms & Abbreviations

- **PCR** - Put/Call Ratio, sourced from Deribit (`DeribitPCRProcessor`).
- **CLOB** - Central Limit Order Book, Polymarket's order matching API (`execution/polymarket_client.py`).
- **TV** - TradingView (webhook alert source).
- **TTL** - Time To Live; `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) for webhook signal staleness.
- **SL/TP** - Stop-Loss / Take-Profit, enforced by `RiskEngine`.
- **RPC** - Remote Procedure Call, used for Solana on-chain data (`data_sources/solana/rpc.py`).
- **WS** - WebSocket, used for Binance/Coinbase real-time price feeds.

## Domain Rules & Invariants

- Maximum position size is capped at $1 (`RiskLimits` in `execution/risk_engine.py`) - a hard safety limit, not
  a tunable default.
- Only one of `"fusion"` or `"tradingview"` can be the active strategy at a time
  (`btc_trading:active_strategy`); never both.
- TradingView signals older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) must be discarded.
- At most one trade per 15-minute market is allowed via the TradingView path
  (`btc_trading:tv_last_traded_market`), and this constraint must survive the bot's ~90-minute auto-restart.
- Dry-run trades must follow the exact same code path as live trades except for the final `submit_order` call -
  this 100% fidelity guarantee is a hard requirement and must never be weakened with earlier branches.
- `LearningEngine` is the *only* component allowed to mutate `SignalFusionEngine` weights at runtime.

## Related Resources

- [project-overview.md](project-overview.md)
