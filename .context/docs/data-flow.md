---
type: doc
name: data-flow
description: How data moves through the system and external integrations
category: data-flow
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Data Flow & Integrations

Market and trade data enters through `data_sources/` adapters (Binance/Coinbase websockets, Solana RPC, news/social
sentiment, Deribit), is normalized and validated by `core/ingestion/`, then fed into NautilusTrader via
`core/nautilus_core/providers/custom_data_provider.py`. The strategy (`bot.py`) consumes this data each cycle,
runs it through the signal processors and fusion engine, and - if a `FusedSignal` is actionable - passes it to the
risk engine and execution engine, which place orders on Polymarket via `execution/polymarket_client.py`. Trade
outcomes flow back into `monitoring/performance_tracker.py` and `feedback/learning_engine.py`, which periodically
adjusts fusion weights, closing the loop.

A second, parallel data path exists for TradingView: alerts arrive over HTTP at
`tradingview_webhook_receiver.py`, are pushed onto a Redis list, and consumed by `bot.py` directly - bypassing
ingestion, signal processors, and fusion entirely.

## Module Dependencies

- **bot.py** -> `core.strategy_brain.signal_processors.*`, `core.strategy_brain.fusion_engine.signal_fusion`,
  `execution.risk_engine`, `execution.execution_engine`, `execution.nautilus_polymarket_integration`,
  `core.nautilus_core.*`, `monitoring.performance_tracker`, `monitoring.grafana_exporter`,
  `feedback.learning_engine`, `redis_control` (mode polling), `patch_gamma_markets`, `patch_market_orders`
- **core/strategy_brain/signal_processors/** -> `core.strategy_brain.signal_processors.base_processor`,
  `data_sources.*` (for spike/divergence/sentiment/PCR inputs), `core.ingestion.*`
- **core/strategy_brain/fusion_engine/signal_fusion** -> `core.strategy_brain.signal_processors.base_processor`
  (consumes `TradingSignal`, produces `FusedSignal`)
- **execution/nautilus_polymarket_integration** -> `execution.execution_engine`, `execution.polymarket_client`,
  `execution.risk_engine`, `core.nautilus_core.instruments.btc_instruments`
- **execution/execution_engine** -> `execution.polymarket_client`, `execution.risk_engine`
- **core/ingestion/adapters/unified_adapter** -> `core.ingestion.validators.data_validator`,
  `core.ingestion.managers.websocket_manager`, `core.ingestion.managers.rate_limiter`, `data_sources.*`
- **core/nautilus_core/data_engine/engine_wrapper** -> `core.nautilus_core.providers.custom_data_provider`,
  `core.nautilus_core.instruments.btc_instruments`, `core.nautilus_core.event_dispatcher.dispatcher`
- **feedback/learning_engine** -> `monitoring.performance_tracker`, `core.strategy_brain.fusion_engine.signal_fusion`
- **tradingview_webhook_receiver.py** -> Redis only (no imports of `core`/`execution` - kept fully decoupled)
- **redis_control.py** -> Redis only (shared `btc_trading:*` keys with `bot.py`)

## Service Layer

- [`SignalFusionEngine` / `get_fusion_engine`](../../core/strategy_brain/fusion_engine/signal_fusion.py) - weighted
  voting across signal processors, produces `FusedSignal`
- [`RiskEngine` / `get_risk_engine`](../../execution/risk_engine.py) - position sizing, $1 cap,
  stop-loss/take-profit checks
- [`ExecutionEngine` / `get_execution_engine`](../../execution/execution_engine.py) - order lifecycle
  (pending -> filled/cancelled)
- [`PolymarketClient` / `get_polymarket_client`](../../execution/polymarket_client.py) - Polymarket CLOB API wrapper
- [`PolymarketBTCIntegration` / `get_polymarket_integration`](../../execution/nautilus_polymarket_integration.py) -
  bridges NautilusTrader events to the execution engine, resolves the active 15m market slug
- [`LearningEngine` / `get_learning_engine`](../../feedback/learning_engine.py) - reads closed-trade outcomes,
  adjusts fusion weights
- [`PerformanceTracker` / `get_performance_tracker`](../../monitoring/performance_tracker.py) - records trades and
  computes performance metrics
- [`GrafanaMetricsExporter` / `get_grafana_exporter`](../../monitoring/grafana_exporter.py) - exposes Prometheus
  `/metrics` on port 8000
- [`EventDispatcher` / `get_event_dispatcher`](../../core/nautilus_core/event_dispatcher/dispatcher.py) - internal
  pub/sub bus for Nautilus core events

## High-level Flow

**Fusion path (default, `active_strategy == "fusion"`):**

```
data_sources/* (Binance, Coinbase, Solana, news/social, Deribit)
  -> core/ingestion (unified_adapter -> data_validator -> websocket_manager/rate_limiter)
  -> core/nautilus_core (custom_data_provider -> engine_wrapper -> NautilusTrader engine)
  -> bot.py (IntegratedBTCStrategy, on each cycle)
  -> core/strategy_brain/signal_processors/* (each emits a TradingSignal)
  -> core/strategy_brain/fusion_engine/signal_fusion (weighted vote -> FusedSignal)
  -> if score >= 60 and confidence >= 0.6 (actionable):
       -> execution/risk_engine (sizing, $1 cap, SL/TP)
       -> execution/nautilus_polymarket_integration -> execution/execution_engine
       -> execution/polymarket_client (sim or live, per btc_trading:simulation_mode)
  -> monitoring/performance_tracker (records outcome)
  -> feedback/learning_engine (periodically re-weights fusion engine)
```

**TradingView path (`active_strategy == "tradingview"`):**

```
TradingView alert -> tunnel (cloudflared/ngrok)
  -> tradingview_webhook_receiver.py (validates shared secret, builds signal message)
  -> RPUSH btc_trading:tradingview_signals (Redis DB 2)
  -> bot.py _start_webhook_consumer (BLPOP, separate thread)
  -> _execute_webhook_trade
       -> discard if signal age > TRADINGVIEW_SIGNAL_TTL_SECONDS (30s)
       -> enforce max 1 trade per 15m market (btc_trading:tv_last_traded_market)
       -> risk check + liquidity guard
       -> _place_real_order(dry_run=...) per btc_trading:tv_dry_run / btc_trading:simulation_mode
            - dry run: full live order path, submit_order skipped, appended to tv_dry_run_trades.json
            - sim/live: real order via execution/polymarket_client
```

## Internal Movement

- **Within-process**: `bot.py` holds direct references to all engines/managers (no internal RPC); calls are
  synchronous method calls within the strategy's `on_*` callback handlers and the periodic decision loop.
- **Cross-process**: only `tradingview_webhook_receiver.py` <-> `bot.py`, via Redis lists/keys in DB 2
  (`btc_trading:tradingview_signals`, `btc_trading:simulation_mode`, `btc_trading:active_strategy`,
  `btc_trading:tv_dry_run`, `btc_trading:tv_last_traded_market`).
- **Event bus**: `core/nautilus_core/event_dispatcher/dispatcher.py` provides an internal pub/sub `EventDispatcher`
  used to decouple Nautilus core data-engine events from consumers within `core/nautilus_core`.

## External Integrations

- **Polymarket CLOB API** (`execution/polymarket_client.py`) - order placement/cancellation; auth via
  `POLYMARKET_PK` + API key/secret/passphrase; sim mode short-circuits before any call is made.
- **Polymarket Gamma API** - market discovery; array-parameter and time-window bugs patched at startup by
  `patch_gamma_markets.py`.
- **Binance WebSocket** (`data_sources/binance/websocket.py`) - real-time spot price ticks.
- **Coinbase** (`data_sources/coinbase/adapter.py`) - cross-exchange price for divergence detection.
- **Deribit** (`core/strategy_brain/signal_processors/deribit_pcr_processor.py`) - put/call ratio.
- **Solana RPC** (`data_sources/solana/rpc.py`) - on-chain data feed.
- **News/social sentiment** (`data_sources/news_social/adapter.py`) - Fear & Greed index + social sentiment.
- **Redis** (localhost:6379, DB 2, hosted in WSL - see [[redis-runs-in-wsl]]) - control-plane keys and the
  TradingView signal queue.
- **TradingView** - webhook alerts via a public tunnel to `tradingview_webhook_receiver.py` (port 8001).
- **Grafana** (`grafana/import_dashboard.py`, `grafana/dashboard.json`) - dashboard provisioning, scrapes the
  Prometheus exporter.

## Observability & Failure Modes

- `monitoring/grafana_exporter.py` exposes `/metrics` on port 8000 (Prometheus format); `--no-grafana` disables it.
- `monitoring/performance_tracker.py` records every trade (`Trade`, `PerformanceMetrics`) for the learning loop and
  for `view_paper_trades.py`.
- TradingView signals carry a timestamp and are dropped if older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) -
  a compensating action against tunnel/queue latency.
- If Redis is unreachable, mode polling (`redis_control`) and the webhook consumer (`BLPOP`) both fail; the bot
  falls back to default simulation mode and the `"fusion"` strategy when keys are absent.
- `15m_bot_runner.py` restarts `bot.py` on crash/every ~90 minutes; `btc_trading:tv_last_traded_market` is the only
  piece of state that must survive this restart, so it lives in Redis rather than in-process memory.

## Related Resources

- [architecture.md](architecture.md)
