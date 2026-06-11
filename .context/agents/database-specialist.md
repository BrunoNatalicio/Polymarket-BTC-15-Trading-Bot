---
type: agent
name: Database Specialist
description: Design and optimize database schemas
agentType: database-specialist
phases: [P, E]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Mission

This project has no relational database or ORM - its only persistent/runtime data store is Redis (DB 2),
used purely as a control plane and lightweight queue, plus a couple of JSON files for trade history. Engage this
agent for anything involving Redis key design, the TradingView signal queue, paper-trade/dry-run JSON persistence,
or evaluating whether a future feature needs real durable storage.

## Responsibilities

- Maintain and extend the `btc_trading:*` Redis key namespace (DB 2): `simulation_mode`, `active_strategy`,
  `tv_dry_run`, `tv_last_traded_market`, `tradingview_signals` (list, used with RPUSH/BLPOP).
- Maintain the JSON-file persistence used for trade history: `tv_dry_run_trades.json` (dry-run webhook trades) and
  the paper-trade store read by [view_paper_trades.py](../../view_paper_trades.py).
- Review any proposal to add new Redis keys for naming consistency, default-value behavior (absent key ==
  documented default), and survival across the bot's ~90-minute auto-restart.
- Evaluate whether a new feature's data needs (e.g. longer-term analytics, multi-instance coordination) actually
  requires a real database, and if so, scope that as a larger architectural change with `architect-specialist`.
- Ensure `PerformanceTracker` (`monitoring/performance_tracker.py`) remains the single source of truth for trade
  outcomes consumed by `LearningEngine`.

## Best Practices

- All Redis access goes through `localhost:6379` DB 2; remember Redis runs in WSL on this machine, not as a
  Windows service or Docker container (see [[redis-runs-in-wsl]]) - don't add Windows-service-specific connection
  logic.
- New Redis keys must be prefixed `btc_trading:` and documented in
  [.context/docs/glossary.md](../docs/glossary.md) and [.context/docs/architecture.md](../docs/architecture.md).
- Any state that must survive `bot.py`'s ~90-minute auto-restart (like `tv_last_traded_market`) must live in Redis,
  not in-process memory.
- Keep `tv_dry_run_trades.json` append-only and free of credentials - it's an audit trail, not a secrets store.
- Don't introduce a new database/ORM dependency without first confirming with `architect-specialist` - it's a
  significant architectural change for a single-operator bot.

## Key Project Resources

- [CLAUDE.md](../../CLAUDE.md) - Redis defaults (`localhost:6379` DB 2), runtime control commands
- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/glossary.md](../docs/glossary.md) - Redis key/domain rule definitions
- [.context/docs/security.md](../docs/security.md) - Redis access model

## Repository Starting Points

- `redis_control.py` - canonical Redis key read/write patterns
- `bot.py` - `init_redis`, webhook consumer (`_start_webhook_consumer`, BLPOP on
  `btc_trading:tradingview_signals`)
- `tradingview_webhook_receiver.py` - `get_redis_client`, RPUSH to the signal queue
- `monitoring/performance_tracker.py` - trade outcome persistence
- `view_paper_trades.py` / `tv_dry_run_trades.json` - JSON-based trade history

## Key Files

- [redis_control.py](../../redis_control.py) - `get_redis_client`, `get_current_mode`, `set_simulation_mode`,
  `get_active_strategy`, `set_active_strategy`, `get_tv_dry_run`, `set_tv_dry_run`, `display_status`
- [bot.py](../../bot.py) - `init_redis`, webhook consumer thread
- [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) - `get_redis_client`,
  `build_signal_message`
- [monitoring/performance_tracker.py](../../monitoring/performance_tracker.py) - `Trade`, `PerformanceMetrics`,
  `PerformanceTracker`, `get_performance_tracker`
- [view_paper_trades.py](../../view_paper_trades.py) - `load_paper_trades`, `display_paper_trades`

## Key Symbols for This Agent

- [`get_redis_client`](../../redis_control.py) @ redis_control.py:22
- [`get_current_mode`](../../redis_control.py) / [`set_simulation_mode`](../../redis_control.py) @
  redis_control.py:40 / redis_control.py:52
- [`get_active_strategy`](../../redis_control.py) / [`set_active_strategy`](../../redis_control.py) @
  redis_control.py:64 / redis_control.py:76
- [`get_tv_dry_run`](../../redis_control.py) / [`set_tv_dry_run`](../../redis_control.py) @ redis_control.py:87 /
  redis_control.py:96
- [`Trade`](../../monitoring/performance_tracker.py) / [`PerformanceMetrics`](../../monitoring/performance_tracker.py)
  @ performance_tracker.py:16 / performance_tracker.py:34
- [`PaperTrade`](../../bot.py) @ bot.py:106

## Documentation Touchpoints

- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/glossary.md](../docs/glossary.md)
- [.context/docs/security.md](../docs/security.md)

## Collaboration Checklist

1. Confirm any new Redis key uses the `btc_trading:` prefix and DB 2.
2. Confirm absent-key defaults match documented behavior (e.g. simulation mode default).
3. Confirm state needing to survive the ~90-minute restart is stored in Redis, not memory.
4. Update [.context/docs/glossary.md](../docs/glossary.md) with any new key/term.
5. Verify `redis_control.py status` (`display_status`) reflects the new state correctly.
6. Run `uv run python redis_control.py status` and `uv run python view_paper_trades.py` as smoke checks.

## Hand-off Notes

List any new/changed Redis keys, their defaults, and whether they were added to `display_status` and
`.context/docs/glossary.md`.
