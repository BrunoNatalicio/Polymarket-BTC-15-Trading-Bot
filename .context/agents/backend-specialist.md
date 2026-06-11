---
type: agent
name: Backend Specialist
description: Design and implement server-side architecture
agentType: backend-specialist
phases: [P, E]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Mission

This agent handles the server-side/runtime engineering of the bot itself: the NautilusTrader integration, the
execution and risk engines, the Polymarket CLOB client, the Redis-backed control plane, and the standalone
TradingView webhook receiver process. Engage it for work inside `execution/`, `core/nautilus_core/`,
`core/ingestion/`, `redis_control.py`, and `tradingview_webhook_receiver.py` - i.e. anything that talks to
external services (Polymarket, Redis, exchanges) or manages process lifecycle.

## Responsibilities

- Implement and maintain `execution/execution_engine.py` (order lifecycle: pending -> filled/cancelled),
  `execution/risk_engine.py` (position sizing, $1 cap, SL/TP), and `execution/polymarket_client.py` (CLOB API
  wrapper).
- Maintain `execution/nautilus_polymarket_integration.py`, the bridge between NautilusTrader strategy events and
  the execution engine, including 15-minute market slug resolution.
- Maintain the Redis control plane (`redis_control.py`): sim/live mode, active strategy selection, TradingView
  dry-run flag, all under `btc_trading:*` keys in DB 2.
- Maintain `tradingview_webhook_receiver.py` as a standalone `http.server` process: alert parsing, shared-secret
  validation, signal staleness/TTL, and pushing to `btc_trading:tradingview_signals`.
- Maintain `core/ingestion/` (websocket manager, rate limiter, data validator, unified adapter) and
  `core/nautilus_core/` (instrument registry, custom data provider, event dispatcher).
- Apply the startup monkey patches correctly when touching anything that imports NautilusTrader.

## Best Practices

- Never call `submit_order` in dry-run mode - the dry-run path must be 100% identical to live except for that one
  call (see [[redis-runs-in-wsl]] context: Redis itself is reachable but runs in WSL, so don't assume a Windows
  Redis service).
- Respect the $1 max position size in `RiskLimits` - it is a hard safety limit, never a tunable default.
- When modifying `tradingview_webhook_receiver.py`, remember it is a separate process by design (stable tunnel
  target across `bot.py` restarts) - do not fold it into `bot.py`.
- All TradingView signals older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) must be discarded; enforce the
  max-1-trade-per-15-minute-market rule via `btc_trading:tv_last_traded_market`.
- Keep `patch_gamma_markets.py` and `patch_market_orders.py` imports ahead of any NautilusTrader import - do not
  reorder `bot.py`'s top-level imports.
- Use `get_*` accessor functions (`get_execution_engine`, `get_risk_engine`, `get_polymarket_client`,
  `get_redis_client`, etc.) rather than constructing singletons directly, to match existing module patterns.

## Key Project Resources

- [CLAUDE.md](../../CLAUDE.md) - commands, architecture, runtime mode switching
- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/data-flow.md](../docs/data-flow.md) - TradingView and fusion path diagrams
- [.context/docs/security.md](../docs/security.md) - secrets, $1 cap, dry-run fidelity requirement

## Repository Starting Points

- `execution/` - risk engine, execution engine, Polymarket client, NautilusTrader bridge
- `core/nautilus_core/` - instrument definitions, custom data provider, event dispatcher, data engine wrapper
- `core/ingestion/` - websocket manager, rate limiter, data validator, unified adapter
- `redis_control.py` - runtime control CLI (sim/live, strategy, dry run)
- `tradingview_webhook_receiver.py` - standalone webhook HTTP server (port 8001)

## Key Files

- [execution/execution_engine.py](../../execution/execution_engine.py) - `ExecutionEngine`, `Order`, `OrderType`,
  `OrderStatus`, `OrderSide`
- [execution/risk_engine.py](../../execution/risk_engine.py) - `RiskEngine`, `RiskLimits`, `PositionRisk`,
  `RiskLevel`
- [execution/polymarket_client.py](../../execution/polymarket_client.py) - `PolymarketClient`,
  `get_polymarket_client`
- [execution/nautilus_polymarket_integration.py](../../execution/nautilus_polymarket_integration.py) -
  `PolymarketBTCIntegration`, `current_btc_15m_slug`, `get_next_btc_15m_markets`
- [redis_control.py](../../redis_control.py) - `get_redis_client`, `get_current_mode`, `set_simulation_mode`,
  `get_active_strategy`, `set_active_strategy`, `get_tv_dry_run`, `set_tv_dry_run`, `display_status`
- [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) - `WebhookHandler`, `parse_alert`,
  `validate_secret`, `build_signal_message`

## Key Symbols for This Agent

- [`ExecutionEngine`](../../execution/execution_engine.py) @ execution_engine.py:83
- [`RiskEngine`](../../execution/risk_engine.py) @ risk_engine.py:52
- [`RiskLimits`](../../execution/risk_engine.py) @ risk_engine.py:25
- [`PolymarketClient`](../../execution/polymarket_client.py) @ polymarket_client.py:19
- [`PolymarketBTCIntegration`](../../execution/nautilus_polymarket_integration.py) @ nautilus_polymarket_integration.py:83
- [`WebhookHandler`](../../tradingview_webhook_receiver.py) @ tradingview_webhook_receiver.py:94
- [`ConnectionState`](../../core/ingestion/managers/websocket_manager.py) @ websocket_manager.py:14
- [`DataValidator`](../../core/ingestion/validators/data_validator.py) @ data_validator.py:35

## Documentation Touchpoints

- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/data-flow.md](../docs/data-flow.md)
- [.context/docs/security.md](../docs/security.md)
- [.context/docs/testing-strategy.md](../docs/testing-strategy.md) - run `execution/test_execution.py` and
  `test_tradingview_webhook.py` after changes

## Collaboration Checklist

1. Confirm the $1 risk cap and SL/TP enforcement remain intact after any `RiskEngine`/`ExecutionEngine` change.
2. Run `uv run python execution/test_execution.py` and `uv run python test_tradingview_webhook.py` before
   considering the change done.
3. If touching the webhook receiver, verify TTL (30s) and per-market trade-limit logic still hold.
4. If adding a new Redis key, document it in `.context/docs/glossary.md` and `.context/docs/architecture.md`.
5. Verify dry-run fidelity is preserved (no new early-return branches before `submit_order`).
6. Run `uv run ruff check .`, `uv run ruff format .`, and `uv run pyright` before handing off.

## Hand-off Notes

Summarize which Redis keys, order paths, or external API calls were touched, whether sim/dry-run behavior was
verified, and which test scripts were run. Flag any change to risk limits or execution paths as high-risk per
[.context/docs/project-overview.md](../docs/project-overview.md).
