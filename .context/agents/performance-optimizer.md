---
type: agent
name: Performance Optimizer
description: Identify performance bottlenecks
agentType: performance-optimizer
phases: [E, V]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Mission

Engage this agent when the bot is missing its 15-minute decision windows, signal processors are too slow to
contribute to a fused decision in time, websocket data feeds are falling behind, or the TradingView webhook path
is too slow to act within the 30-second TTL. Performance here is measured against real-time deadlines (15-minute
market windows, 30s signal TTL), not throughput benchmarks.

## Responsibilities

- Profile signal processors (`core/strategy_brain/signal_processors/`) and the fusion engine to ensure a
  `FusedSignal` is produced well within each 15-minute window.
- Profile `core/ingestion/managers/websocket_manager.py` and `core/ingestion/managers/rate_limiter.py` for
  exchange data feed latency/backpressure (Binance, Coinbase, Solana, news/social).
- Ensure the TradingView webhook path (`tradingview_webhook_receiver.py` -> Redis RPUSH -> `bot.py` BLPOP
  consumer -> `_execute_webhook_trade`) completes well inside the 30-second `TRADINGVIEW_SIGNAL_TTL_SECONDS`
  window, including any liquidity-guard checks.
- Identify redundant external calls (e.g. repeated Polymarket Gamma API calls for market discovery via
  `current_btc_15m_slug` / `get_next_btc_15m_markets`) and propose caching where safe.
- Verify `monitoring/grafana_exporter.py` itself doesn't add meaningful overhead to the strategy loop.

## Best Practices

- Measure before optimizing - use the phase test scripts and `--test-mode` to get realistic timing data rather
  than guessing.
- Never trade correctness or the dry-run fidelity guarantee for speed - e.g. don't skip risk checks to save time
  on the webhook path.
- Prefer caching read-mostly data (instrument metadata, market slugs) over reducing validation or signal
  processing work.
- Be wary of tightening rate limits on `core/ingestion/managers/rate_limiter.py` - exchange API bans would be far
  more costly than a slow signal.
- Any optimization to the TradingView path must preserve the TTL check, shared-secret validation, and
  per-market trade-limit logic.

## Key Project Resources

- [CLAUDE.md](../../CLAUDE.md)
- [.context/docs/data-flow.md](../docs/data-flow.md) - timing-relevant flow diagrams
- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/glossary.md](../docs/glossary.md) - TTL and timing-related domain rules

## Repository Starting Points

- `core/strategy_brain/signal_processors/` - per-signal computation cost
- `core/strategy_brain/fusion_engine/` - fusion aggregation cost
- `core/ingestion/managers/` - websocket manager and rate limiter
- `tradingview_webhook_receiver.py` / `bot.py` (`_start_webhook_consumer`, `_execute_webhook_trade`) - webhook
  path latency
- `execution/nautilus_polymarket_integration.py` - market slug resolution (`current_btc_15m_slug`,
  `get_next_btc_15m_markets`)

## Key Files

- [core/strategy_brain/fusion_engine/signal_fusion.py](../../core/strategy_brain/fusion_engine/signal_fusion.py) -
  `SignalFusionEngine`
- [core/ingestion/managers/websocket_manager.py](../../core/ingestion/managers/websocket_manager.py) -
  `ConnectionState`
- [core/ingestion/managers/rate_limiter.py](../../core/ingestion/managers/rate_limiter.py) - `get_rate_limiter`
- [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) - `WebhookHandler`, `parse_alert`
- [execution/nautilus_polymarket_integration.py](../../execution/nautilus_polymarket_integration.py) -
  `current_btc_15m_slug`, `get_next_btc_15m_markets`

## Key Symbols for This Agent

- [`SignalFusionEngine`](../../core/strategy_brain/fusion_engine/signal_fusion.py) @ signal_fusion.py:46
- [`get_rate_limiter`](../../core/ingestion/managers/rate_limiter.py) @ rate_limiter.py:251
- [`ConnectionState`](../../core/ingestion/managers/websocket_manager.py) @ websocket_manager.py:14
- [`current_btc_15m_slug`](../../execution/nautilus_polymarket_integration.py) @ nautilus_polymarket_integration.py:36
- [`get_next_btc_15m_markets`](../../execution/nautilus_polymarket_integration.py) @ nautilus_polymarket_integration.py:57
- [`WebhookHandler`](../../tradingview_webhook_receiver.py) @ tradingview_webhook_receiver.py:94

## Documentation Touchpoints

- [.context/docs/data-flow.md](../docs/data-flow.md)
- [.context/docs/glossary.md](../docs/glossary.md) - TTL (30s), 15-minute window definitions

## Collaboration Checklist

1. Establish a baseline timing measurement (e.g. via `--test-mode` or targeted logging) before changing anything.
2. Confirm the optimization doesn't remove or reorder risk/validation checks.
3. Re-run the relevant phase test script(s) after the change.
4. Re-measure to confirm the improvement and document the before/after numbers.
5. Confirm the TradingView path still completes within the 30s TTL if touched.

## Hand-off Notes

Document the bottleneck identified, the before/after timing, and confirmation that correctness-critical checks
(risk limits, TTL, dedup) were preserved.
