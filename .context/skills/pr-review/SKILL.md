---
type: skill
name: Pr Review
description: Review pull requests against team standards and best practices. Use when Reviewing a pull request before merge, Providing feedback on proposed changes, or Validating PR meets project standards
skillSlug: pr-review
phases: [R, V]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Read the PR description for which pipeline phase(s) (data sources, ingestion, NautilusTrader, signal
   processors, fusion, risk, execution, monitoring, learning) or which standalone surface (TradingView webhook,
   Redis control plane, monitoring/Grafana) it touches.
2. Confirm `uv run ruff check .`, `uv run ruff format .`, and `uv run pyright` were run (or run them) - this is
   the project's quality gate, there is no CI pipeline beyond these plus the phase test scripts.
3. Confirm the relevant phase test script(s) were run and pass: `data_sources/test.py`,
   `core/ingestion/test_ingestion.py`, `core/nautilus_core/test_nautilus.py`,
   `core/strategy_brain/test_strategy.py`, `execution/test_execution.py`, `test_tradingview_webhook.py`.
4. If `bot.py` is touched, verify the startup patch order (`patch_gamma_markets.py`, `patch_market_orders.py`
   before NautilusTrader imports) is unchanged - check the diff doesn't reorder these imports.
5. If `execution/risk_engine.py`, `_execute_webhook_trade`, or `_place_real_order` are touched, verify the $1
   cap, SL/TP, and dry-run fidelity are preserved - request changes if any check appears bypassable.
6. If new `.env` variables or `btc_trading:*` Redis keys are introduced, confirm they're documented in
   [CLAUDE.md](../../../CLAUDE.md) / [.context/docs/glossary.md](../../docs/glossary.md) and (for secrets) added
   to `.env.example` without real values.
7. Approve, request changes, or comment with file:line references.

## Examples

**Approval comment:**
```
Ruff/pyright clean, execution/test_execution.py passes. The new SL/TP adjustment in
RiskEngine.check_position_size correctly still enforces the $1 cap (verified the cap
check runs before the new trailing-stop logic). Approved.
```

**Request changes:**
```
A few items before merge:

1. patch_gamma_markets.py / patch_market_orders.py imports were moved below the
   NautilusTrader import in bot.py - this needs to stay above per CLAUDE.md.
2. New btc_trading:tv_max_slippage Redis key isn't documented in glossary.md.
3. test_tradingview_webhook.py wasn't updated for the new payload field - please add
   a case to test_parse_alert.

Please address and I'll re-review.
```

## Quality Bar

- Treat the patch import order in `bot.py`, the $1 risk cap, and dry-run fidelity as non-negotiable invariants -
  any PR that weakens them should be blocked regardless of how small the diff looks.
- Don't require a pytest suite or CI workflow - this repo deliberately uses standalone phase scripts and a local
  quality gate (ruff + pyright + phase tests).
- Confirm fusion weight changes (Spike 40% / Divergence 30% / Sentiment 20% / others 10%) are intentional and
  explained, since `LearningEngine` is normally the only thing that mutates them at runtime.
- Verify TradingView webhook changes preserve `validate_secret`, the 30s TTL, and per-market dedup.
- Keep feedback specific to file:line and grounded in what the diff actually changes.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
