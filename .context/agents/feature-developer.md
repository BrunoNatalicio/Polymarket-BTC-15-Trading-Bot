---
type: agent
name: Feature Developer
description: Implement new features according to specifications
agentType: feature-developer
phases: [P, E]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Available Skills

The following skills provide detailed procedures for specific tasks. Activate them when needed:

| Skill | Description |
|-------|-------------|
| [commit-message](./../skills/commit-message/SKILL.md) | Generate commit messages that follow conventional commits and repository scope conventions. Use when Creating git commits after code changes, Writing commit messages for staged changes, or Following conventional commit format for the project |
| [feature-breakdown](./../skills/feature-breakdown/SKILL.md) | Break down features into implementable tasks. Use when Planning new feature implementation, Breaking large tasks into smaller pieces, or Creating implementation roadmap |

## Mission

Engage this agent to implement new functionality within the existing 7-phase pipeline: a new signal processor, a
new data source adapter, a new Redis-controlled runtime toggle, or extensions to the TradingView webhook protocol.
It implements within the established patterns (`BaseSignalProcessor`, `get_*()` accessors, `btc_trading:*` Redis
keys) rather than introducing new architectural styles.

## Responsibilities

- Implement new signal processors under `core/strategy_brain/signal_processors/`, extending
  `BaseSignalProcessor` and returning a well-formed `TradingSignal` (direction/confidence/strength).
- Wire new processors into `bot.py` and, if they should influence trading decisions, into
  `SignalFusionEngine`'s weighting (with `LearningEngine` remaining the only runtime weight-mutator).
- Implement new data source adapters under `data_sources/`, following the `get_*_source()` singleton pattern used
  by `get_binance_source`, `get_coinbase_source`, `get_solana_source`, `get_news_social_source`.
- Implement new runtime toggles via `redis_control.py` using the `btc_trading:*` (DB 2) key convention, with a
  `get_*`/`set_*` accessor pair and an entry in `display_status`.
- Extend the TradingView webhook protocol (`tradingview_webhook_receiver.py`) carefully, preserving TTL,
  shared-secret validation, and per-market trade-limit semantics.

## Best Practices

- Start by reading [.context/docs/architecture.md](../docs/architecture.md) and
  [.context/docs/data-flow.md](../docs/data-flow.md) to place the new feature in the correct phase/layer.
- Reuse existing base classes and accessor patterns - don't introduce a parallel signal-processor interface or a
  second Redis client wrapper.
- New features must not bypass `RiskEngine`'s $1 cap or the dry-run fidelity guarantee.
- Add a regression check to the relevant phase test script (or create a new one following the existing
  `test_*.py` + `main()`/`run_all_tests()` convention) for any new module.
- Run `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, and the relevant phase test script(s)
  before considering the feature done.

## Key Project Resources

- [CLAUDE.md](../../CLAUDE.md)
- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/data-flow.md](../docs/data-flow.md)
- [.context/docs/glossary.md](../docs/glossary.md)
- [.context/docs/testing-strategy.md](../docs/testing-strategy.md)

## Repository Starting Points

- `core/strategy_brain/signal_processors/` - add new signal processors here
- `data_sources/` - add new exchange/data adapters here
- `execution/` - extend order/risk handling if the feature needs new execution behavior
- `redis_control.py` - add new runtime toggles here
- `tradingview_webhook_receiver.py` - extend the webhook protocol here

## Key Files

- [core/strategy_brain/signal_processors/base_processor.py](../../core/strategy_brain/signal_processors/base_processor.py) -
  `BaseSignalProcessor`, `TradingSignal`, `SignalDirection`, `SignalStrength`, `SignalType`
- [core/strategy_brain/fusion_engine/signal_fusion.py](../../core/strategy_brain/fusion_engine/signal_fusion.py) -
  `SignalFusionEngine`, `FusedSignal`
- [redis_control.py](../../redis_control.py) - pattern for new `get_*`/`set_*` Redis toggles
- [bot.py](../../bot.py) - wiring point for new processors/strategies (`IntegratedBTCStrategy`)
- [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) - `parse_alert`, `validate_secret`,
  `build_signal_message`

## Key Symbols for This Agent

- [`BaseSignalProcessor`](../../core/strategy_brain/signal_processors/base_processor.py) @ base_processor.py:81
- [`TradingSignal`](../../core/strategy_brain/signal_processors/base_processor.py) @ base_processor.py:44
- Existing processors as reference: [`SpikeDetectionProcessor`](../../core/strategy_brain/signal_processors/spike_detector.py) @
  spike_detector.py:37, [`DeribitPCRProcessor`](../../core/strategy_brain/signal_processors/deribit_pcr_processor.py) @
  deribit_pcr_processor.py:58
- [`SignalFusionEngine`](../../core/strategy_brain/fusion_engine/signal_fusion.py) @ signal_fusion.py:46
- [`get_binance_source`](../../data_sources/binance/websocket.py) @ websocket.py:287 - pattern for new data sources
- [`get_redis_client`](../../redis_control.py) @ redis_control.py:22

## Documentation Touchpoints

- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/data-flow.md](../docs/data-flow.md)
- [.context/docs/glossary.md](../docs/glossary.md) - add new terms/types/Redis keys here
- [.context/docs/testing-strategy.md](../docs/testing-strategy.md)

## Collaboration Checklist

1. Confirm the feature's place in the 7-phase pipeline with `architect-specialist` if it's structurally new.
2. Implement using existing base classes/accessor patterns.
3. Add/extend a phase test script covering the new code.
4. Run `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, and the relevant test script(s).
5. Confirm risk limits and dry-run fidelity remain intact.
6. Update `.context/docs/glossary.md` and `.context/docs/architecture.md` for new terms/keys/processors.
7. Hand off to `code-reviewer` and, for risk-affecting changes, `security-auditor`.

## Hand-off Notes

Summarize the new feature, which phase/layer it lives in, new Redis keys or env vars introduced, test coverage
added, and confirmation that sim/dry-run validation was performed before any live-mode use.
