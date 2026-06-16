---
type: doc
name: testing-strategy
description: Test frameworks, patterns, coverage requirements, and quality gates
category: testing
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Testing Strategy

There is no pytest suite. Each major phase of the pipeline has a standalone, runnable test script that exercises
its own module(s) end-to-end (often against real or stubbed external services). Quality is otherwise maintained
via `ruff` (lint/format) and `pyright` (type checking). New phases or modules should follow the same pattern: a
`test_*.py` script with a `main()`/`run_all_tests()` entry point that can be run directly with `uv run python`.

## Test Types

- **Phase scripts** (the only test type in this repo): one script per pipeline phase, run directly with
  `uv run python <path>`. Examples:
  - [data_sources/test.py](../../data_sources/test.py) - `test_gamma_api`
  - [core/ingestion/test_ingestion.py](../../core/ingestion/test_ingestion.py)
  - [core/nautilus_core/test_nautilus.py](../../core/nautilus_core/test_nautilus.py)
  - [core/strategy_brain/test_strategy.py](../../core/strategy_brain/test_strategy.py)
  - [execution/test_execution.py](../../execution/test_execution.py) - `test_risk_engine`,
    `test_execution_engine`, `test_polymarket_client`, `run_all_tests`, `test`
  - [test_tradingview_webhook.py](../../test_tradingview_webhook.py) - `test_parse_alert`,
    `test_validate_secret`, `test_staleness`, `test_direction_mapping`, `test_redis_resilience`,
    `test_redis_roundtrip`, `test_http_end_to_end`, uses `StubRedis` to avoid a real Redis dependency
- **Integration-style checks**: `execution/test_execution.py::run_all_tests` and
  `test_tradingview_webhook.py::test_http_end_to_end` exercise multiple components together (e.g. a real HTTP
  request against `WebhookHandler`).
- **No E2E harness against live Polymarket** - live-path validation is done via `--test-mode` (simulated trades)
  and `dryrun on` (full live order path with `submit_order` skipped).
- **Opt-in integration script**: [test_redis_resilience_integration.py](../../test_redis_resilience_integration.py)
  drives the real `redis_resilience.ensure_client` through a full Redis down→up cycle. For safety it spins up an
  **isolated** `redis-server` on port 6399 inside WSL and kills/restarts *that* - it never touches the production
  Redis on `:6379`. Not part of the commit gate (it needs WSL); it skips cleanly (exit 0) when WSL/redis-server is
  unavailable. Run: `uv run python test_redis_resilience_integration.py`.

## Running Tests

- Run all phase tests individually:
  ```
  uv run python data_sources/test.py
  uv run python core/ingestion/test_ingestion.py
  uv run python core/nautilus_core/test_nautilus.py
  uv run python core/strategy_brain/test_strategy.py
  uv run python execution/test_execution.py
  uv run python test_tradingview_webhook.py
  ```
- There is no watch mode or coverage tooling configured.
- For end-to-end behavior validation: `uv run python 15m_bot_runner.py --test-mode` (simulated trades every
  minute) or `uv run python redis_control.py dryrun on` + live run (full order path, no `submit_order`).

## Quality Gates

- `uv run ruff check .` - lint, must pass
- `uv run ruff format .` - formatting, must be applied
- `uv run pyright` - type checking, must pass
- The relevant phase test script(s) for any touched module must run successfully
- There is no `scripts/ci_gate.py` - the gate is exactly: ruff + pyright + the standalone test scripts
- No formal coverage threshold; correctness is validated by running the phase scripts and, for execution/risk
  changes, by running in `--test-mode` or with `dryrun on` before enabling live trading

## Troubleshooting

- `test_tradingview_webhook.py` does not require a running Redis for most tests: `test_http_end_to_end` always
  uses `StubRedis` ([test_tradingview_webhook.py:153](../../test_tradingview_webhook.py)), and only
  `test_redis_roundtrip` talks to real Redis — it skips gracefully (`[SKIP] Redis not available`) when Redis
  is down.
- `execution/test_execution.py` may attempt real Polymarket CLOB calls via `PolymarketClient` - ensure `.env`
  credentials are present and prefer running it in a context where live order submission is safe (sim mode).
- `core/nautilus_core/test_nautilus.py` and `core/strategy_brain/test_strategy.py` depend on the startup patches
  (`patch_gamma_markets.py`, `patch_market_orders.py`) being importable - run them with `uv run python` from the
  repo root so the patch modules resolve correctly.

## Related Resources

- [development-workflow.md](development-workflow.md)
