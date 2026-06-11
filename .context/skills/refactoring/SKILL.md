---
type: skill
name: Refactoring
description: Refactor code safely with a step-by-step approach. Use when Improving code structure without changing behavior, Reducing code duplication, or Simplifying complex logic
skillSlug: refactoring
phases: [E]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Identify the relevant phase test script(s) for the area being refactored and run them first to establish a
   passing baseline (e.g. `uv run python core/strategy_brain/test_strategy.py` before touching
   `core/strategy_brain/signal_processors/`).
2. Make one type of change at a time - e.g. extracting shared confidence-scoring logic from two signal
   processors is one refactor; renaming a `get_*()` accessor across modules is a separate one.
3. Preserve the `BaseSignalProcessor` interface (`TradingSignal` with `direction`/`confidence`/`strength`),
   all `btc_trading:*` Redis key names, all `.env` variable names, and the `get_*()` singleton-accessor names
   (`get_execution_engine`, `get_risk_engine`, `get_polymarket_client`, etc.) - these are runtime contracts.
4. If refactoring `bot.py`, do not reorder the `patch_gamma_markets.py` / `patch_market_orders.py` imports
   relative to NautilusTrader imports.
5. Re-run the same phase test script(s) after the change and confirm output is unchanged.
6. Run `uv run ruff check .`, `uv run ruff format .`, and `uv run pyright`.
7. If a bug is discovered mid-refactor, stop refactoring and hand off to bug-fixing first - don't mix a fix and
   a refactor in the same step.

## Examples

**Extracting shared confidence scaling (signal processors):**
```python
# Before: duplicated in spike_detector.py and tick_velocity_processor.py
confidence = min(1.0, max(0.0, raw_score / threshold))

# After: extracted into base_processor.py as a shared helper
class BaseSignalProcessor:
    def _scale_confidence(self, raw_score: float, threshold: float) -> float:
        return min(1.0, max(0.0, raw_score / threshold))
```

## Quality Bar

- Never refactor and change behavior in the same step - if the phase test script's output changes, you changed
  behavior, not just structure.
- Don't introduce new abstractions "for the future" - three similar signal processors sharing a small helper is
  fine; a generic processor framework is not, unless explicitly requested.
- Keep `execution/`, `data_sources/`, and `core/nautilus_core/` `get_*()` accessor names stable - other modules
  import them by name.
- Small, reviewable steps - prefer several small refactor commits over one large one.
- Confirm the relevant phase test script(s) pass with unchanged output before and after.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
