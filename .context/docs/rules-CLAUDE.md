---
source: CLAUDE.md
type: generic
---

---
trigger: always_on
---

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CI/CD Merge Gate Protocol (CRITICAL)

**MANDATORY**: Este repositório usa um hook Git pre-commit fail-closed (Maestro Harness
em `.githooks/pre-commit`). Siga `.context/docs/ci-policy.md`.
1. NUNCA usar `git commit --no-verify` ou `git merge --no-verify`. Absolutamente proibido.
2. Todo `git commit` roda: dotcontext export-rules + sync + reverse-sync e
   `python .agent/scripts/checklist.py .`. O commit ABORTA se houver erro de lint,
   security, teste ou contexto.
3. Se o commit falhar, foi porque o código quebrou. NÃO repita às cegas — leia o output
   do hook, corrija e tente de novo.
4. Para reativar o hook: `git config core.hooksPath .githooks`.
5. Fonte da verdade deste arquivo: `.context/docs/rules-CLAUDE.md` (o hook propaga para
   `CLAUDE.md` via `export-rules`; edits diretos no `CLAUDE.md` voltam via `reverse-sync`).

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

# Run the full pre-commit audit manually (same gate the hook runs)
uv run python .agent/scripts/checklist.py .
```

Note: runtime dependencies live in `requirements.txt` (`uv pip install -r requirements.txt`); `pyproject.toml` only declares the dev group (ruff, pyright, pytest, pip-audit). The commit gate is the Maestro Harness pre-commit hook (`.githooks/pre-commit` → `.agent/scripts/checklist.py`): security scan + ruff + scoped pyright + the hermetic test script. There is no pytest suite — tests are standalone scripts.

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
- `SpikeDetectionProcessor` — mean-reversion + velocity on the **Polymarket UP-probability** series (not Binance/Coinbase)
- `PriceDivergenceProcessor` — Polymarket UP probability vs BTC spot momentum (extreme-prob fade + momentum mispricing)
- `SentimentProcessor` — Fear & Greed index + social sentiment
- `OrderBookImbalanceProcessor` — bid/ask depth imbalance
- `TickVelocityProcessor` — trade arrival rate anomalies
- `DeribitPCRProcessor` — Deribit put/call ratio

`SignalFusionEngine` (`core/strategy_brain/fusion_engine/signal_fusion.py`) combines signals by weighted voting into a `FusedSignal`. The engine's constructor defaults (Spike 40 / Divergence 30 / Sentiment 20) are **overridden at runtime** in `on_start` (OrderBookImbalance 0.30, TickVelocity 0.25, PriceDivergence 0.18, SpikeDetection 0.12, DeribitPCR 0.10, Sentiment 0.05). **Important:** the deployed fusion strategy does **not** trade the fused *direction* — it uses the fusion only as an *activity gate* (trades if `fuse_signals` returns non-None at minute ~13) and then **follows the Polymarket price** (a TREND FILTER: UP-mid > 0.60 → buy YES, < 0.40 → buy NO, 0.40–0.60 → skip). The `is_actionable` (`score≥60 & conf≥0.6`) / `is_strong` (`≥70`) properties exist but do not gate the trade. See [.context/docs/fusion-strategy.md](.context/docs/fusion-strategy.md) for the full mechanics.

### Learning feedback loop

`LearningEngine` (`feedback/learning_engine.py`) reads closed trade outcomes from `PerformanceTracker` and adjusts `SignalFusionEngine` weights. This runs periodically and is the only component that mutates fusion weights at runtime.

### Runtime mode switching

Redis key `btc_trading:simulation_mode` (DB 2) controls whether orders are real or paper. The bot polls this key; `redis_control.py` sets it. Simulation mode is the default when the key is absent.

### TradingView webhook strategy

An alternative strategy where TradingView alerts are the only trade trigger. Redis key `btc_trading:active_strategy` (`"fusion"` | `"tradingview"`, default `"fusion"`) selects which strategy is active — never both. When `"tradingview"` is active, `_make_trading_decision` returns early and the fusion path is skipped.

Flow: TradingView alert → tunnel (cloudflared/ngrok) → `tradingview_webhook_receiver.py` (separate process, stdlib `http.server` on port 8001, validates a shared secret from the JSON body) → `RPUSH btc_trading:tradingview_signals` → `bot.py` consumer thread (`_start_webhook_consumer`, BLPOP) → `_execute_webhook_trade` (risk check + liquidity guard + sim/live gate, bypassing fusion entirely).

Rules: signals older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) are discarded; max 1 trade per 15-minute market via Redis key `btc_trading:tv_last_traded_market` (survives the 90-min auto-restart); `UP` buys the YES token ("long"), `DOWN` buys the NO token ("short").

Market selection is by **wall clock**, not `current_instrument_index`. The alert fires at the bar close (`:00/:15/:30/:45`) — exactly when a 15m window expires — so `_handle_tradingview_signal` maps `floor(now/900)*900` to the freshly-opened **N+1** window (identical to the backtest's `attach_target_tokens`) and prices it from that market's own book, never the expiring ~$0.99 one. The next market is pre-subscribed (`_ensure_next_subscribed`) and every subscribed instrument's latest quote is cached (`_last_quote_by_instrument`) so the N+1 book is warm at the boundary; with no fresh quote the signal is discarded rather than trading the expiring window. Pure, dependency-free selection logic lives in `tv_market_select.py` (`select_target_market`, `fresh_quote`); the target market's token ids are passed explicitly into `_place_real_order`.

Webhook orders are market orders (**taker**), so on the 15m/5m crypto markets they pay the Polymarket taker fee (`fee = C × feeRate × p × (1 − p)`, where `C` = shares traded (`stake/p`), crypto `feeRate = 0.07`, charged in shares — peaks near $0.50, negligible at the extremes). Gasless and the Builder program do **not** waive it; only maker (resting limit) orders are fee-free. The backtest models the fee (see `.context/docs/backtest-validation.md`).

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
| `execution/risk_engine.py` | Position sizing, `MARKET_BUY_USD` position cap, stop-loss/take-profit enforcement |
| `execution/polymarket_client.py` | Polymarket CLOB API wrapper |
| `execution/nautilus_polymarket_integration.py` | Bridges NautilusTrader events to the execution engine |
| `patch_gamma_markets.py` | Monkey-patch applied at startup — do not remove |
| `patch_market_orders.py` | Monkey-patch applied at startup — do not remove |
| `tradingview_webhook_receiver.py` | Standalone HTTP receiver for TradingView alerts → Redis queue |
| `redis_control.py` | Runtime control CLI: sim/live mode and active strategy |
| `.githooks/pre-commit` | Maestro Harness fail-closed commit gate — do not bypass |
| `.agent/scripts/checklist.py` | Audit orchestrator run by the pre-commit hook |

## Environment

Copy `.env.example` to `.env` before running. Required keys: `POLYMARKET_PK`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`. Redis defaults to `localhost:6379` DB 2.

Python version is pinned in `.python-version` (3.13). Use `uv` — there is no `scripts/` test runner entry point; tests are standalone scripts, not pytest suites.
