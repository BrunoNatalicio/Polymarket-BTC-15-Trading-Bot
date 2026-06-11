---
type: skill
name: Test Generation
description: Generate comprehensive test cases for code. Use when Writing tests for new functionality, Adding tests for bug fixes (regression tests), or Improving test coverage for existing code
skillSlug: test-generation
phases: [E, V]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

This repository has no pytest suite - tests are standalone scripts run with `uv run python <script>.py`, one per
pipeline phase plus `test_tradingview_webhook.py`. Always extend the matching existing script rather than
creating a new test framework or file.

1. Identify the phase script that covers the changed code:
   `data_sources/test.py`, `core/ingestion/test_ingestion.py`, `core/nautilus_core/test_nautilus.py`,
   `core/strategy_brain/test_strategy.py`, `execution/test_execution.py`, `test_tradingview_webhook.py`.
2. For a new/changed signal processor, add a check in `core/strategy_brain/test_strategy.py` that instantiates
   it, feeds representative data, and asserts the returned `TradingSignal` has valid `direction`
   (BULLISH/BEARISH/NEUTRAL), `confidence` in [0,1], and `strength`.
3. For execution/risk changes, extend `execution/test_execution.py`'s `test_risk_engine` /
   `test_execution_engine` to cover the new behavior, including the $1 cap and SL/TP.
4. For TradingView webhook changes, extend `test_tradingview_webhook.py` - add cases to `test_parse_alert`,
   `test_validate_secret`, `test_staleness`, `test_direction_mapping`, or `test_redis_roundtrip` as appropriate.
5. Match the existing style: plain functions named `test_*` that print pass/fail and return a bool, aggregated
   by a `run_all_tests`/`main`-style entrypoint - not `assert`-based pytest fixtures.
6. Run the extended script with `uv run python <script>.py` and confirm clean output before considering the task
   done.

## Examples

**Adding a case to test_tradingview_webhook.py (new payload field):**
```python
def test_parse_alert():
    # existing cases...

    # New: alert with optional "confidence" field should parse and default sanely
    alert = {"secret": TEST_SECRET, "direction": "UP", "market": "btc-15m-x", "confidence": 0.9}
    parsed = parse_alert(json.dumps(alert).encode())
    assert parsed is not None
    assert parsed["confidence"] == 0.9
    print("test_parse_alert: confidence field OK")
```

**Adding a signal processor check to core/strategy_brain/test_strategy.py:**
```python
def test_funding_rate_processor():
    processor = FundingRateProcessor()
    signal = processor.process(sample_funding_data)
    assert signal.direction in (SignalDirection.BULLISH, SignalDirection.BEARISH, SignalDirection.NEUTRAL)
    assert 0.0 <= signal.confidence <= 1.0
    print("test_funding_rate_processor: OK")
```

## Quality Bar

- Extend an existing phase script - don't introduce pytest, unittest, or a new test runner.
- For anything touching real-money paths, test against `--test-mode` / simulation mode or `tv_dry_run`, never
  live credentials.
- Tests involving Redis assume it's reachable at `localhost:6379` DB 2 in WSL (see [[redis-runs-in-wsl]]) - not
  mocked.
- Never write a test that weakens the dry-run fidelity guarantee to make it "easier to test" - the dry-run path
  must remain the full live path minus `submit_order`.
- New tests must produce clear pass/fail output consistent with the existing script's reporting style.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
