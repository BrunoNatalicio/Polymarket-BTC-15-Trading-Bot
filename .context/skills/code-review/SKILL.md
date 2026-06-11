---
type: skill
name: Code Review
description: Review code quality, patterns, and best practices. Use when Reviewing code changes for quality, Checking adherence to coding standards, or Identifying potential bugs or issues
skillSlug: code-review
phases: [R, V]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Run `uv run ruff check .`, `uv run ruff format .`, and `uv run pyright` first - style and typing issues should
   be caught mechanically, not in review commentary.
2. Identify which of the 7 pipeline phases the change touches and confirm the corresponding phase test script
   still passes (`data_sources/test.py`, `core/ingestion/test_ingestion.py`, `core/nautilus_core/test_nautilus.py`,
   `core/strategy_brain/test_strategy.py`, `execution/test_execution.py`, `test_tradingview_webhook.py`).
3. If `bot.py` is touched, verify the startup patch import order (`patch_gamma_markets.py`,
   `patch_market_orders.py` before NautilusTrader imports) is unchanged.
4. If `execution/risk_engine.py` or any order-submission path is touched, verify the $1 cap (`RiskLimits`) and
   SL/TP enforcement are not weakened or bypassed.
5. If the TradingView webhook path is touched, verify `validate_secret` still runs first, the 30s TTL
   (`TRADINGVIEW_SIGNAL_TTL_SECONDS`) and per-market dedup (`btc_trading:tv_last_traded_market`) are intact, and
   the dry-run path (`tv_dry_run`) still never calls `submit_order`.
6. Check that any new/changed `btc_trading:*` Redis key or `.env` variable is documented in
   [.context/docs/glossary.md](../../docs/glossary.md) and [CLAUDE.md](../../../CLAUDE.md).
7. Leave feedback grouped by severity (blocking vs. suggestion) with file:line references.

## Examples

**Blocking feedback (risk bypass):**
```
execution/risk_engine.py:120 - This new code path calls execution_engine.submit_order()
directly without going through RiskEngine.check_position_size(). This bypasses the $1 cap.
Blocking until routed through RiskEngine.
```

**Blocking feedback (dry-run fidelity):**
```
bot.py:_place_real_order - New `if dry_run and signal.confidence < 0.8: return` branch runs
BEFORE the existing dry_run check at the bottom of the function. This makes dry-run diverge
from live for low-confidence signals (live would still place the order). Move this check so
both paths share it, or remove it.
```

**Suggestion:**
```
core/strategy_brain/signal_processors/tick_velocity_processor.py:88 - This confidence
calculation duplicates the pattern in spike_detector.py:64. Not blocking, but could be
extracted if a third processor needs it.
```

## Quality Bar

- Treat any change to `execution/risk_engine.py`, `_execute_webhook_trade`, or `_place_real_order` as
  high-severity by default - these guard real money.
- Treat any change that adds a branch before `submit_order` in the live order path as a dry-run-fidelity risk
  requiring explicit verification.
- Confirm `btc_trading:*` Redis key names and `.env` variable names are preserved exactly (runtime contract with
  `redis_control.py`).
- Don't block on style/formatting that `ruff format` would fix automatically - run it instead.
- Praise good patterns (e.g. correctly extending `BaseSignalProcessor`) so contributors know what "right" looks
  like in this codebase.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
