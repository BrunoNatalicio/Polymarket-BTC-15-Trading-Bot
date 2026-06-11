---
type: doc
name: development-workflow
description: Day-to-day engineering processes, branching, and contribution guidelines
category: workflow
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Development Workflow

This is a single-maintainer Python trading bot project. Changes are typically made directly against `main`,
verified locally with `uv run ruff check`, `uv run ruff format`, `uv run pyright`, and the standalone phase test
scripts, then run in `--test-mode` (simulated trades) before being deployed to live trading via
`15m_bot_runner.py --live`. Because the bot trades real money, any change touching `execution/`, `bot.py`'s
patches, or the TradingView webhook path should be validated in sim/dry-run mode first.

## Branching & Releases

- Trunk-based: work happens on `main`; short-lived feature branches are used for larger changes.
- No formal release tags - "release" means restarting `15m_bot_runner.py` (or the supervised process) with the
  updated `main` checked out.
- Runtime behavior (sim/live, active strategy, dry run) is switched via Redis (`redis_control.py`) rather than via
  branches/deploys, so most behavioral toggles don't require a release at all.

## Local Development

- Install dependencies: `uv sync` (and `uv pip install -r requirements.txt` for runtime deps)
- Lint: `uv run ruff check .`
- Format: `uv run ruff format .`
- Type check: `uv run pyright`
- Run phase tests:
  ```
  uv run python data_sources/test.py
  uv run python core/ingestion/test_ingestion.py
  uv run python core/nautilus_core/test_nautilus.py
  uv run python core/strategy_brain/test_strategy.py
  uv run python execution/test_execution.py
  uv run python test_tradingview_webhook.py
  ```
- Run bot in test mode (simulated trades every minute): `uv run python 15m_bot_runner.py --test-mode`
- Run bot live (real money): `uv run python 15m_bot_runner.py --live`
- Switch sim/live and strategy at runtime:
  ```
  uv run python redis_control.py sim|live
  uv run python redis_control.py strategy fusion|tradingview
  uv run python redis_control.py dryrun on|off
  ```
- Start the TradingView webhook receiver (separate process, port 8001):
  `uv run python tradingview_webhook_receiver.py`
- View paper trade history: `uv run python view_paper_trades.py`

## Code Review Expectations

There is no formal PR review process - this is a solo project. Before considering a change done:
- Run `uv run ruff check .`, `uv run ruff format .`, and `uv run pyright` (the gate is ruff + pyright + the
  standalone test scripts; there is no `scripts/ci_gate.py`).
- Run the relevant phase test script(s) for any touched module.
- For changes to `bot.py`'s startup patches (`patch_gamma_markets.py`, `patch_market_orders.py`), confirm the
  import order before NautilusTrader import is preserved - this is load-bearing per [CLAUDE.md](../../CLAUDE.md).
- For changes to the TradingView webhook path, confirm dry-run fidelity is preserved: `_place_real_order(dry_run=True)`
  must follow the exact same code path as live, with `submit_order` as the only skipped call.
- For risk/execution changes, test in `--test-mode` and/or with `dryrun on` before enabling live trading.

## Onboarding Tasks

New contributors should start by reading [CLAUDE.md](../../CLAUDE.md) and [architecture.md](architecture.md), then:
1. Run `uv sync` and copy `.env.example` to `.env`, filling in the Polymarket credentials.
2. Confirm Redis is reachable on `localhost:6379` DB 2 (it runs in WSL on this machine - see
   [[redis-runs-in-wsl]] - not a Windows service or Docker container).
3. Run `uv run python 15m_bot_runner.py --test-mode` and watch simulated trades.
4. Try `redis_control.py status`/`sim`/`live`/`strategy`/`dryrun` to understand the runtime control plane.
5. Read [data-flow.md](data-flow.md) for how the fusion and TradingView paths differ.

## Related Resources

- [testing-strategy.md](testing-strategy.md)
- [tooling.md](tooling.md)
