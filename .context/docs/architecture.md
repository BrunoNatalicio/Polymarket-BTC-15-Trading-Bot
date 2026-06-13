---
type: doc
name: architecture
description: System architecture, layers, patterns, and design decisions
category: architecture
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Architecture Notes

This is a single-process Python trading bot for Polymarket BTC 15-minute up/down markets. It is built as a 7-phase
pipeline (data sources -> ingestion -> NautilusTrader -> signal processors -> fusion -> risk -> execution ->
monitoring -> learning), all driven from a single NautilusTrader `Strategy` subclass in [bot.py](../../bot.py).
The design favors a monolithic strategy object that owns references to every phase's manager/engine, rather than
separate services, because the bot must make a single coherent trading decision every cycle and all phases share
state (current market, simulation mode, active strategy).

## System Architecture Overview

`15m_bot_runner.py` is the supervisor: it launches `bot.py` as a subprocess and restarts it every ~90 minutes (and
on crash). `bot.py` applies two monkey patches ([patch_gamma_markets.py](../../patch_gamma_markets.py),
[patch_market_orders.py](../../patch_market_orders.py)) before importing NautilusTrader, then constructs
`IntegratedBTCStrategy`, which is registered with the NautilusTrader engine via
[core/nautilus_core/data_engine/engine_wrapper.py](../../core/nautilus_core/data_engine/engine_wrapper.py).

Two control planes run alongside the strategy:
- **Redis (DB 2)** is polled for runtime mode switches (`btc_trading:simulation_mode`,
  `btc_trading:active_strategy`, `btc_trading:tv_dry_run`), set via [redis_control.py](../../redis_control.py).
- **TradingView webhook receiver** ([tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py)) is a
  separate stdlib `http.server` process on port 8001 that pushes validated alerts onto a Redis list, consumed by a
  background thread inside `bot.py`.

On each cycle the strategy either runs the fusion path (combine signals -> fuse -> risk-check -> execute) or, if
`active_strategy == "tradingview"`, skips fusion and acts only on webhook signals.

## Architectural Layers

- **Data sources** (`data_sources/`): exchange/market data adapters - `binance/`, `coinbase/`, `solana/`,
  `news_social/`. Each exposes a `get_*_source()` singleton accessor.
- **Ingestion** (`core/ingestion/`): `adapters/unified_adapter.py` normalizes feeds, `validators/data_validator.py`
  validates ticks, `managers/websocket_manager.py` and `managers/rate_limiter.py` manage connections.
- **NautilusTrader core** (`core/nautilus_core/`): `instruments/btc_instruments.py` defines synthetic instruments,
  `providers/custom_data_provider.py` and `data_engine/engine_wrapper.py` wire custom data into the Nautilus engine,
  `event_dispatcher/dispatcher.py` is an internal pub/sub bus.
- **Strategy brain** (`core/strategy_brain/`): `signal_processors/` (one `BaseSignalProcessor` subclass per signal),
  `fusion_engine/signal_fusion.py` (weighted voting -> `FusedSignal`), `strategies/btc_15min_strategy.py`.
- **Execution** (`execution/`): `risk_engine.py` (sizing, `MARKET_BUY_USD` cap, stop-loss/take-profit),
  `execution_engine.py` (order lifecycle), `polymarket_client.py` (CLOB API wrapper),
  `nautilus_polymarket_integration.py` (bridges Nautilus events to execution + market slug resolution).
- **Monitoring** (`monitoring/`): `performance_tracker.py` (trade outcomes), `grafana_exporter.py` (Prometheus
  `/metrics` on port 8000).
- **Feedback** (`feedback/learning_engine.py`): adjusts `SignalFusionEngine` weights from closed-trade performance.
- **Top-level entry/control**: [bot.py](../../bot.py), [15m_bot_runner.py](../../15m_bot_runner.py),
  [redis_control.py](../../redis_control.py), [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py).

> Use `context({ action: "getMap", section: "all" })` for generated architecture and dependency summaries.

## Detected Design Patterns

| Pattern | Confidence | Locations | Description |
|---------|------------|-----------|-------------|
| Singleton accessor | 90% | `get_redis_client`, `get_polymarket_client`, `get_fusion_engine`, `get_execution_engine`, `get_risk_engine`, `get_performance_tracker`, `get_learning_engine`, `get_*_source` | Module-level `get_*()` functions lazily construct and cache a single shared instance per process |
| Strategy / pluggable processor | 85% | `BaseSignalProcessor` subclasses in `core/strategy_brain/signal_processors/` | Each signal source implements a common interface returning a `TradingSignal`, fused by `SignalFusionEngine` |
| Monkey patch / adapter shim | 80% | [patch_gamma_markets.py](../../patch_gamma_markets.py), [patch_market_orders.py](../../patch_market_orders.py) | Patches third-party NautilusTrader/Polymarket adapter behavior at import time before the engine starts |
| Producer/consumer queue | 75% | `tradingview_webhook_receiver.py` (RPUSH) -> `bot.py` `_start_webhook_consumer` (BLPOP) | Decouples the webhook HTTP process from the trading process via a Redis list |
| Runtime feature flag via Redis | 70% | `btc_trading:simulation_mode`, `btc_trading:active_strategy`, `btc_trading:tv_dry_run` | Behavior toggled at runtime without redeploying, polled each cycle |

## Entry Points

- [bot.py](../../bot.py) - main strategy process (`IntegratedBTCStrategy`, applies startup patches)
- [15m_bot_runner.py](../../15m_bot_runner.py) - auto-restart supervisor for `bot.py`
- [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) - standalone webhook HTTP server (port 8001)
- [redis_control.py](../../redis_control.py) - CLI for runtime mode/strategy/dry-run switches
- [grafana_exporter.py](../../monitoring/grafana_exporter.py) - Prometheus metrics server (port 8000)
- [view_paper_trades.py](../../view_paper_trades.py) - CLI to inspect paper trade history

## Public API

| Symbol | Type | Location |
|--------|------|----------|
| `IntegratedBTCStrategy` | class | [bot.py:149](../../bot.py) |
| `apply_gamma_markets_patch` | function | [patch_gamma_markets.py:13](../../patch_gamma_markets.py) |
| `apply_market_order_patch` | function | [patch_market_orders.py:26](../../patch_market_orders.py) |
| `BaseSignalProcessor` | class | [core/strategy_brain/signal_processors/base_processor.py:81](../../core/strategy_brain/signal_processors/base_processor.py) |
| `TradingSignal` | class | [core/strategy_brain/signal_processors/base_processor.py:44](../../core/strategy_brain/signal_processors/base_processor.py) |
| `SignalFusionEngine` / `get_fusion_engine` | class/function | [core/strategy_brain/fusion_engine/signal_fusion.py:46,221](../../core/strategy_brain/fusion_engine/signal_fusion.py) |
| `FusedSignal` | class | [core/strategy_brain/fusion_engine/signal_fusion.py:23](../../core/strategy_brain/fusion_engine/signal_fusion.py) |
| `RiskEngine` / `get_risk_engine` | class/function | [execution/risk_engine.py:52,464](../../execution/risk_engine.py) |
| `ExecutionEngine` / `get_execution_engine` | class/function | [execution/execution_engine.py:83,536](../../execution/execution_engine.py) |
| `PolymarketClient` / `get_polymarket_client` | class/function | [execution/polymarket_client.py:19,478](../../execution/polymarket_client.py) |
| `PolymarketBTCIntegration` / `get_polymarket_integration` | class/function | [execution/nautilus_polymarket_integration.py:83,520](../../execution/nautilus_polymarket_integration.py) |
| `LearningEngine` / `get_learning_engine` | class/function | [feedback/learning_engine.py:37,333](../../feedback/learning_engine.py) |
| `PerformanceTracker` / `get_performance_tracker` | class/function | [monitoring/performance_tracker.py:69,454](../../monitoring/performance_tracker.py) |
| `GrafanaMetricsExporter` / `get_grafana_exporter` | class/function | [monitoring/grafana_exporter.py:168,408](../../monitoring/grafana_exporter.py) |
| `EventDispatcher` / `get_event_dispatcher` | class/function | [core/nautilus_core/event_dispatcher/dispatcher.py:40,245](../../core/nautilus_core/event_dispatcher/dispatcher.py) |
| `InstrumentRegistry` / `get_instrument_registry` | class/function | [core/nautilus_core/instruments/btc_instruments.py:124,174](../../core/nautilus_core/instruments/btc_instruments.py) |

## Internal System Boundaries

- **Strategy process vs. webhook process**: `bot.py` and `tradingview_webhook_receiver.py` are separate OS
  processes communicating only via Redis (`btc_trading:tradingview_signals` list). The receiver must stay a
  separate process because `15m_bot_runner.py` restarts `bot.py` periodically while the public tunnel
  (cloudflared/ngrok) needs a stable target.
- **Fusion vs. TradingView strategy**: mutually exclusive at runtime, gated by `btc_trading:active_strategy`
  in `_make_trading_decision` (`bot.py`). Never both active simultaneously.
- **Sim vs. live execution**: gated by `btc_trading:simulation_mode` (Redis DB 2), checked before any order
  reaches `execution/polymarket_client.py`.

## External Service Dependencies

- **Polymarket CLOB API** - order placement/cancellation via `execution/polymarket_client.py`; requires
  `POLYMARKET_PK`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`.
- **Polymarket Gamma API** - market discovery/metadata, patched by `patch_gamma_markets.py` for array params and
  time-window filtering.
- **Binance / Coinbase** - spot price feeds via `data_sources/binance/websocket.py` and
  `data_sources/coinbase/adapter.py`.
- **Deribit** - put/call ratio for `DeribitPCRProcessor`.
- **Solana RPC** - on-chain data via `data_sources/solana/rpc.py`.
- **News/social sentiment** - `data_sources/news_social/adapter.py` (Fear & Greed index, social sentiment).
- **Redis (localhost:6379, DB 2)** - runtime control plane and TradingView signal queue; runs in WSL, no
  Windows service/Docker (see [[redis-runs-in-wsl]]).
- **Grafana** - dashboard import via `grafana/import_dashboard.py`, scraping the Prometheus exporter.
- **TradingView** - alerts delivered over a public tunnel (cloudflared/ngrok) to `tradingview_webhook_receiver.py`.

## Key Decisions & Trade-offs

- **Monolithic strategy object** over microservices: a single trading decision per cycle needs synchronous access
  to all signal/risk/execution state; splitting into services would add latency and consistency risk for a
  15-minute-window market.
- **Monkey patches applied at import time** rather than forking NautilusTrader/Polymarket adapters: keeps the bot
  on upstream releases while fixing two specific incompatibilities (`patch_gamma_markets.py`,
  `patch_market_orders.py`). Import order in `bot.py` is load-bearing and must not be refactored.
- **Redis as the control plane** instead of config files or redeploys: sim/live, active strategy, and dry-run can
  be flipped instantly while the bot is running.
- **Webhook receiver as a separate process**: avoids losing the public tunnel endpoint across the bot's periodic
  restarts.
- **Dry run reuses the full live order path** (`_place_real_order(dry_run=True)`) with `submit_order` as the only
  skipped call, to guarantee 100% fidelity between dry-run and live behavior for TradingView trades.

## Risks & Constraints

- Max position size capped at `MARKET_BUY_USD` (default $1, currently $3) by `execution/risk_engine.py` (`RiskLimits`); the daily-loss and exposure limits scale with it.
- TradingView signals older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) are discarded - the webhook path is
  latency-sensitive.
- Max 1 trade per 15-minute market (`btc_trading:tv_last_traded_market`), persisted across the 90-minute
  auto-restart.
- The bot depends on Redis being reachable (WSL-hosted); if Redis is down, runtime mode polling and the
  TradingView queue both fail.

## Top Directories Snapshot

- `core/` - NautilusTrader integration, strategy brain, ingestion (~40 files)
- `execution/` - order/risk/CLOB integration (~6 files)
- `data_sources/` - exchange/chain/social adapters (~10 files)
- `monitoring/` - performance tracking and Grafana exporter (~3 files)
- `feedback/` - learning engine (~1-2 files)
- `grafana/` - dashboard JSON + import script (~2 files)
- Top-level - `bot.py`, `15m_bot_runner.py`, `redis_control.py`, `tradingview_webhook_receiver.py`,
  patch modules, view/test scripts

## Related Resources

- [project-overview.md](project-overview.md)
- [data-flow.md](data-flow.md)
- [../../CLAUDE.md](../../CLAUDE.md)
