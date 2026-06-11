---
type: doc
name: project-overview
description: High-level overview of the project, its purpose, and key components
category: overview
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Project Overview

This project is an automated trading bot that trades Polymarket's BTC "up/down" prediction markets on
15-minute windows. It combines real-time market-microstructure signals (price spikes, cross-exchange
divergence, sentiment, order book imbalance, tick velocity, Deribit put/call ratio) into a single fused trading
decision, executes orders against Polymarket's CLOB with strict risk limits, and supports an alternative
TradingView-alert-driven strategy. It exists to let its operator run a small, risk-capped automated strategy
against Polymarket's short-duration crypto markets, with full sim/live/dry-run control at runtime.

## Codebase Reference

> **Semantic Snapshot**: Use `context({ action: "getMap", section: "all" })` for generated stack, architecture
> layers, key files, and dependency hotspots.

## Quick Facts

- Root: `C:\desenvolvendo\developmentbot_polymarket`
- Primary language: Python 3.13 (pinned via `.python-version`), managed with `uv`
- Entry points: [bot.py](../../bot.py) (strategy), [15m_bot_runner.py](../../15m_bot_runner.py) (supervisor),
  [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) (webhook server)
- Semantic snapshot: `context({ action: "getMap", section: "all" })`

## Entry Points

- [bot.py](../../bot.py) - `IntegratedBTCStrategy` (NautilusTrader `Strategy` subclass), applies startup patches
  before importing NautilusTrader
- [15m_bot_runner.py](../../15m_bot_runner.py) - `run_bot()` auto-restart wrapper for `bot.py`
- [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) - `main()`, `WebhookHandler`, standalone
  HTTP server on port 8001
- [redis_control.py](../../redis_control.py) - `main()`, runtime control CLI (sim/live, strategy, dry run)
- [monitoring/grafana_exporter.py](../../monitoring/grafana_exporter.py) - `get_grafana_exporter()`, Prometheus
  `/metrics` on port 8000
- [view_paper_trades.py](../../view_paper_trades.py) - `main()`, paper trade history viewer

## Key Exports

- `IntegratedBTCStrategy` ([bot.py:149](../../bot.py)) - the main strategy class wiring all phases together
- `BaseSignalProcessor` / `TradingSignal` ([core/strategy_brain/signal_processors/base_processor.py](../../core/strategy_brain/signal_processors/base_processor.py)) -
  shared interface for all signal processors
- `SignalFusionEngine` / `FusedSignal` / `get_fusion_engine` ([core/strategy_brain/fusion_engine/signal_fusion.py](../../core/strategy_brain/fusion_engine/signal_fusion.py)) -
  weighted-voting fusion engine
- `RiskEngine` / `get_risk_engine` ([execution/risk_engine.py](../../execution/risk_engine.py)) - position sizing
  and the $1 risk cap
- `ExecutionEngine` / `get_execution_engine` ([execution/execution_engine.py](../../execution/execution_engine.py)) -
  order lifecycle management
- `PolymarketClient` / `get_polymarket_client` ([execution/polymarket_client.py](../../execution/polymarket_client.py)) -
  Polymarket CLOB API wrapper
- `PolymarketBTCIntegration` / `get_polymarket_integration` ([execution/nautilus_polymarket_integration.py](../../execution/nautilus_polymarket_integration.py)) -
  bridges NautilusTrader events to execution
- `LearningEngine` / `get_learning_engine` ([feedback/learning_engine.py](../../feedback/learning_engine.py)) -
  adjusts fusion weights from trade outcomes
- `apply_gamma_markets_patch` / `apply_market_order_patch` - startup monkey patches required before
  NautilusTrader import

## File Structure & Code Organization

- `bot.py`, `15m_bot_runner.py`, `redis_control.py`, `tradingview_webhook_receiver.py` - top-level entry/control
  scripts
- `patch_gamma_markets.py`, `patch_market_orders.py` - required startup monkey patches (do not remove or reorder)
- `data_sources/` - Binance, Coinbase, Solana, news/social adapters
- `core/ingestion/` - data validation, websocket management, rate limiting, unified adapter
- `core/nautilus_core/` - NautilusTrader instrument definitions, custom data provider, event dispatcher
- `core/strategy_brain/` - signal processors, fusion engine, BTC 15-min strategy
- `execution/` - risk engine, execution engine, Polymarket CLOB client, Nautilus<->Polymarket bridge
- `monitoring/` - performance tracker, Grafana Prometheus exporter
- `feedback/` - learning engine (fusion weight adjustment)
- `grafana/` - dashboard JSON and import script
- Test scripts: `data_sources/test.py`, `core/ingestion/test_ingestion.py`, `core/nautilus_core/test_nautilus.py`,
  `core/strategy_brain/test_strategy.py`, `execution/test_execution.py`, `test_tradingview_webhook.py`

## Technology Stack Summary

Python 3.13, managed with `uv` (dependency resolution and virtualenv). NautilusTrader is the trading engine
framework. Redis (DB 2, hosted in WSL - see [[redis-runs-in-wsl]]) is the runtime control plane. Linting/formatting
via `ruff`, type checking via `pyright`. There is no pytest suite - tests are standalone scripts run directly with
`uv run python`. Prometheus + Grafana provide monitoring.

## Development Tools Overview

See [tooling.md](tooling.md) for the full command reference (`uv sync`, `ruff check`/`format`, `pyright`, phase
test scripts, bot run modes, and `redis_control.py`).

## Getting Started Checklist

1. Run `uv sync` to install dependencies (and `uv pip install -r requirements.txt` for runtime deps).
2. Copy `.env.example` to `.env` and fill in `POLYMARKET_PK`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`,
   `POLYMARKET_PASSPHRASE`.
3. Confirm Redis is reachable at `localhost:6379` DB 2 (runs in WSL on this machine).
4. Run `uv run python 15m_bot_runner.py --test-mode` to see simulated trades.
5. Read [architecture.md](architecture.md) and [data-flow.md](data-flow.md) to understand the fusion vs.
   TradingView strategy split.
6. Review [development-workflow.md](development-workflow.md) for lint/typecheck/test commands.

## Next Steps

This is a personal/solo trading project (real money is at stake in live mode). Any change to risk limits,
execution paths, or the TradingView dry-run path should be treated as high-risk and validated in sim/dry-run
before going live.

## Related Resources

- [architecture.md](architecture.md)
- [development-workflow.md](development-workflow.md)
- [tooling.md](tooling.md)
