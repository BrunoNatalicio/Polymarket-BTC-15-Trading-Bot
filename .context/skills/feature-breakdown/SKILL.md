---
type: skill
name: Feature Breakdown
description: Break down features into implementable tasks. Use when Planning new feature implementation, Breaking large tasks into smaller pieces, or Creating implementation roadmap
skillSlug: feature-breakdown
phases: [P]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Map the feature onto the 7-phase pipeline (data sources -> ingestion -> NautilusTrader -> signal processors ->
   fusion -> risk -> execution -> monitoring -> learning) to identify which phase(s) it belongs to.
2. For a new signal processor: (a) create the processor extending `BaseSignalProcessor` in
   `core/strategy_brain/signal_processors/`, (b) wire it into `bot.py`'s active processor list, (c) add a weight
   in `SignalFusionEngine`, (d) extend `core/strategy_brain/test_strategy.py`.
3. For a new data source: (a) add an adapter under `data_sources/`, (b) wire it into ingestion/validators,
   (c) extend `data_sources/test.py`.
4. For any feature touching order placement or sizing: (a) confirm the $1 cap (`RiskLimits`) and SL/TP still
   apply, (b) extend `execution/test_execution.py`.
5. For any feature touching the TradingView path: confirm whether it needs to preserve the 30s TTL, per-market
   dedup, and dry-run fidelity - these are usually non-negotiable constraints, not separate tasks.
6. Order tasks so that the phase test script for each touched phase passes before moving to the next phase.
7. Flag any task that would change a `btc_trading:*` Redis key or `.env` variable name - these are part of the
   runtime contract with `redis_control.py` and the operator's existing setup.

## Examples

**Feature breakdown: add a funding-rate signal processor**
```
## Feature: Funding Rate Signal Processor

### Task 1: Implement FundingRateProcessor
- Extend BaseSignalProcessor in core/strategy_brain/signal_processors/funding_rate_processor.py
- Output TradingSignal with direction/confidence/strength based on perp funding rate skew
- Acceptance: unit-level check in core/strategy_brain/test_strategy.py passes

### Task 2: Wire into bot.py and SignalFusionEngine
- Add to active processor list in bot.py
- Add a weight (e.g. 10%) in SignalFusionEngine, rebalancing existing "others" weights
- Acceptance: core/strategy_brain/test_strategy.py shows FusedSignal incorporating the new signal

### Task 3: Document
- Add processor to CLAUDE.md "Active processors" list and .context/docs/architecture.md

### Dependencies:
Task 2 requires Task 1; Task 3 requires Task 2.
```

## Quality Bar

- Every task should map to a concrete file/symbol in this repo, not a generic "add service layer" abstraction -
  there is no service/DI layer here.
- Tasks touching `execution/` or the TradingView webhook path must include a sub-task for re-running the
  relevant phase test script.
- Don't propose tasks that introduce new frameworks (web framework, ORM, pytest) unless the user explicitly
  asks for that migration.
- Flag any task that changes `btc_trading:*` Redis keys, `.env` variable names, or the startup patch import
  order as higher-risk and call it out separately.
- Keep fusion-weight rebalancing as an explicit task whenever a new signal processor is added - weights must
  still sum sensibly per [.context/docs/architecture.md](../../docs/architecture.md).

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
