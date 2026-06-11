# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Install dependencies (uses uv)
uv sync

# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run pyright

# Run all phase tests individually
uv run python data_sources/test.py
uv run python core/ingestion/test_ingestion.py
uv run python core/nautilus_core/test_nautilus.py
uv run python core/strategy_brain/test_strategy.py
uv run python execution/test_execution.py
uv run python test_tradingview_webhook.py

# Run bot (test mode — simulated trades every minute)
uv run python 15m_bot_runner.py --test-mode

# Run bot (live trading — real money)
uv run python 15m_bot_runner.py --live

# Switch sim/live mode at runtime via Redis
uv run python redis_control.py sim
uv run python redis_control.py live

# Switch active strategy at runtime via Redis (fusion is the default)
uv run python redis_control.py strategy fusion
uv run python redis_control.py strategy tradingview

# TradingView dry run (full live order path, submit_order skipped)
uv run python redis_control.py dryrun on
uv run python redis_control.py dryrun off

# Start the TradingView webhook receiver (separate process, port 8001)
uv run python tradingview_webhook_receiver.py

# View paper trade history
uv run python view_paper_trades.py
```

Note: runtime dependencies live in `requirements.txt` (`uv pip install -r requirements.txt`); `pyproject.toml` only declares the dev group (ruff, pyright, pytest, pip-audit). There is no `scripts/ci_gate.py` — the gate is ruff + pyright + the standalone test scripts.

## Architecture

The bot is a 7-phase pipeline. Data flows linearly: **data sources → ingestion → NautilusTrader → signal processors → fusion → risk → execution → monitoring → learning**.

### Startup patches (critical)

`bot.py` must apply two monkey-patches **before** importing NautilusTrader:

1. `patch_gamma_markets.py` — fixes array parameter handling in the Polymarket Gamma API adapter and forces market filtering by time window.
2. `patch_market_orders.py` — patches NautilusTrader's market order submission to conform to Polymarket's CLOB API.

Both patches are applied at module load time in `bot.py`. If either fails, the process exits immediately. Do not refactor their import order.

### Signal pipeline

All signal processors live in `core/strategy_brain/signal_processors/` and extend `BaseSignalProcessor` (`base_processor.py`). Each processor outputs a `TradingSignal` with `direction` (BULLISH/BEARISH/NEUTRAL), `confidence` (0–1), and `strength`.

Active processors wired into `bot.py`:
- `SpikeDetectionProcessor` — detects price spikes from Binance/Coinbase divergence
- `PriceDivergenceProcessor` — cross-exchange price divergence
- `SentimentProcessor` — Fear & Greed index + social sentiment
- `OrderBookImbalanceProcessor` — bid/ask depth imbalance
- `TickVelocityProcessor` — trade arrival rate anomalies
- `DeribitPCRProcessor` — Deribit put/call ratio

`SignalFusionEngine` (`core/strategy_brain/fusion_engine/signal_fusion.py`) combines all signals using weighted voting. Default weights: Spike 40%, Divergence 30%, Sentiment 20%, others 10%. A `FusedSignal` is **actionable** when `score >= 60` and `confidence >= 0.6`, **strong** when `score >= 70`.

### Learning feedback loop

`LearningEngine` (`feedback/learning_engine.py`) reads closed trade outcomes from `PerformanceTracker` and adjusts `SignalFusionEngine` weights. This runs periodically and is the only component that mutates fusion weights at runtime.

### Runtime mode switching

Redis key `btc_trading:simulation_mode` (DB 2) controls whether orders are real or paper. The bot polls this key; `redis_control.py` sets it. Simulation mode is the default when the key is absent.

### TradingView webhook strategy

An alternative strategy where TradingView alerts are the only trade trigger. Redis key `btc_trading:active_strategy` (`"fusion"` | `"tradingview"`, default `"fusion"`) selects which strategy is active — never both. When `"tradingview"` is active, `_make_trading_decision` returns early and the fusion path is skipped.

Flow: TradingView alert → tunnel (cloudflared/ngrok) → `tradingview_webhook_receiver.py` (separate process, stdlib `http.server` on port 8001, validates a shared secret from the JSON body) → `RPUSH btc_trading:tradingview_signals` → `bot.py` consumer thread (`_start_webhook_consumer`, BLPOP) → `_execute_webhook_trade` (risk check + liquidity guard + sim/live gate, bypassing fusion entirely).

Rules: signals older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) are discarded; max 1 trade per 15-minute market via Redis key `btc_trading:tv_last_traded_market` (survives the 90-min auto-restart); `UP` buys the YES token ("long"), `DOWN` buys the NO token ("short").

Dry run: Redis key `btc_trading:tv_dry_run` = "1" (set via `redis_control.py dryrun on|off`). Webhook trades run the FULL live order path in `_place_real_order(dry_run=True)` — the only divergence from live is that `submit_order` is not called (this 100% fidelity is a hard requirement; never add earlier branches). Would-be trades are appended to `tv_dry_run_trades.json`. Dry run takes precedence over sim/live for webhook trades and does not affect the fusion path.

The receiver is deliberately a separate process — `15m_bot_runner.py` restarts `bot.py` periodically and the tunnel must keep a stable target. Don't fold it into `bot.py`.

### Monitoring

`grafana_exporter.py` exposes a Prometheus `/metrics` endpoint (default port 8000). The pre-built Grafana dashboard is in `grafana/dashboard.json`. Pass `--no-grafana` to skip starting the metrics server.

## Key files

| File | Purpose |
|------|---------|
| `bot.py` | Main strategy — NautilusTrader `Strategy` subclass, integrates all phases |
| `15m_bot_runner.py` | Auto-restart wrapper that re-launches `bot.py` on exit |
| `execution/execution_engine.py` | Order lifecycle management (pending → filled/cancelled) |
| `execution/risk_engine.py` | Position sizing, max-$1 cap, stop-loss/take-profit enforcement |
| `execution/polymarket_client.py` | Polymarket CLOB API wrapper |
| `execution/nautilus_polymarket_integration.py` | Bridges NautilusTrader events to the execution engine |
| `patch_gamma_markets.py` | Monkey-patch applied at startup — do not remove |
| `patch_market_orders.py` | Monkey-patch applied at startup — do not remove |
| `tradingview_webhook_receiver.py` | Standalone HTTP receiver for TradingView alerts → Redis queue |
| `redis_control.py` | Runtime control CLI: sim/live mode and active strategy |

## Environment

Copy `.env.example` to `.env` before running. Required keys: `POLYMARKET_PK`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`. Redis defaults to `localhost:6379` DB 2.

Python version is pinned in `.python-version` (3.13). Use `uv` — there is no `scripts/` test runner entry point; tests are standalone scripts, not pytest suites.
