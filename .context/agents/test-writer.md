---
type: agent
name: Test Writer
description: Write comprehensive unit and integration tests
agentType: test-writer
phases: [E, V]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Available Skills

The following skills provide detailed procedures for specific tasks. Activate them when needed:

| Skill | Description |
|-------|-------------|
| [test-generation](./../skills/test-generation/SKILL.md) | Generate comprehensive test cases for code. Use when Writing tests for new functionality, Adding tests for bug fixes (regression tests), or Improving test coverage for existing code |

## Mission

This repository has no pytest suite - "tests" are seven standalone phase scripts (one per pipeline phase) plus
`test_tradingview_webhook.py`, each run directly with `uv run python <script>.py`. Engage this agent when adding
new test coverage for a signal processor, data source, execution path, or webhook behavior, or when extending an
existing phase script after a feature change.

## Responsibilities

- Extend the relevant phase script when new functionality is added to that phase:
  `data_sources/test.py`, `core/ingestion/test_ingestion.py`, `core/nautilus_core/test_nautilus.py`,
  `core/strategy_brain/test_strategy.py`, `execution/test_execution.py`, `test_tradingview_webhook.py`.
- When adding a new signal processor, add a corresponding check to `core/strategy_brain/test_strategy.py` that
  exercises its `BaseSignalProcessor` interface (`TradingSignal` with `direction`, `confidence`, `strength`).
- When changing `execution/risk_engine.py` or `execution/execution_engine.py`, extend
  `execution/test_execution.py` to cover the new behavior, including the $1 cap and SL/TP enforcement.
- When changing the TradingView webhook flow, extend `test_tradingview_webhook.py` to cover
  `validate_secret`, TTL expiry (`TRADINGVIEW_SIGNAL_TTL_SECONDS`), per-market dedup
  (`btc_trading:tv_last_traded_market`), and dry-run fidelity (`_place_real_order(dry_run=True)` never calling
  `submit_order`).
- Keep each phase script runnable standalone and self-reporting (clear pass/fail output) - there is no shared
  pytest fixture/conftest layer to rely on.

## Best Practices

- Follow the existing style of each phase script (plain `python` script with print-based assertions/output) rather
  than introducing `pytest` or a new test framework, unless the user explicitly asks for a framework migration.
- For anything touching real money paths (`execution/`, webhook live-order path), prefer testing against
  `--test-mode` / simulation mode (`btc_trading:simulation_mode`) or dry-run (`btc_trading:tv_dry_run`) rather than
  live credentials.
- When testing Redis-dependent code, remember Redis runs in WSL at `localhost:6379` DB 2 - tests assume it's
  reachable, not mocked (see [[redis-runs-in-wsl]]).
- Never weaken the dry-run fidelity guarantee to make it "easier to test" - the dry-run path must remain the full
  live order path minus `submit_order`.
- Preserve the startup patch import order (`patch_gamma_markets.py`, `patch_market_orders.py` before
  NautilusTrader) in any test that imports `bot.py`.

## Key Project Resources

- [CLAUDE.md](../../CLAUDE.md) - lists all phase test commands
- [.context/docs/testing-strategy.md](../docs/testing-strategy.md) - primary reference for this agent
- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/glossary.md](../docs/glossary.md)

## Repository Starting Points

- `data_sources/test.py` - Phase 1 (data sources)
- `core/ingestion/test_ingestion.py` - Phase 2 (ingestion)
- `core/nautilus_core/test_nautilus.py` - Phase 3 (NautilusTrader integration)
- `core/strategy_brain/test_strategy.py` - Phase 4 (signal processors + fusion)
- `execution/test_execution.py` - Phase 6 (execution + risk)
- `test_tradingview_webhook.py` - TradingView webhook strategy

## Key Files

- [core/strategy_brain/signal_processors/base_processor.py](../../core/strategy_brain/signal_processors/base_processor.py) -
  `BaseSignalProcessor`, `TradingSignal` - the interface new processor tests must exercise
- [core/strategy_brain/fusion_engine/signal_fusion.py](../../core/strategy_brain/fusion_engine/signal_fusion.py) -
  `SignalFusionEngine`, `FusedSignal`
- [execution/risk_engine.py](../../execution/risk_engine.py) - `RiskLimits`, `RiskEngine`
- [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) - `validate_secret`, `parse_alert`,
  `WebhookHandler`
- [redis_control.py](../../redis_control.py) - `get_tv_dry_run`, `get_active_strategy`, `get_simulation_mode`

## Key Symbols for This Agent

- [`BaseSignalProcessor`](../../core/strategy_brain/signal_processors/base_processor.py) @ base_processor.py:81
- [`TradingSignal`](../../core/strategy_brain/signal_processors/base_processor.py) @ base_processor.py:20
- [`SignalFusionEngine`](../../core/strategy_brain/fusion_engine/signal_fusion.py) @ signal_fusion.py:46
- [`RiskEngine`](../../execution/risk_engine.py) @ risk_engine.py:52
- [`validate_secret`](../../tradingview_webhook_receiver.py) @ tradingview_webhook_receiver.py:61
- [`get_tv_dry_run`](../../redis_control.py) @ redis_control.py:87

## Documentation Touchpoints

- [.context/docs/testing-strategy.md](../docs/testing-strategy.md)
- [.context/docs/glossary.md](../docs/glossary.md)

## Collaboration Checklist

1. Identify which phase script(s) cover the changed code; extend rather than create a new file unless the change
   is genuinely a new phase.
2. Match the existing script's style (plain script, print-based pass/fail) - no new test framework.
3. For execution/risk/webhook changes, cover the $1 cap, SL/TP, TTL, dedup, and dry-run fidelity explicitly.
4. Run the extended script(s) with `uv run python <script>.py` and confirm clean output.
5. Run `uv run ruff check .`, `uv run ruff format .`, and `uv run pyright` after adding test code.

## Hand-off Notes

List which phase script(s) were extended, what new behavior is covered, and the output of running them. Flag any
behavior that could not be tested without live credentials or live Redis/WSL access.
