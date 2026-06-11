---
type: doc
name: tooling
description: Scripts, IDE settings, automation, and developer productivity tips
category: tooling
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Tooling & Productivity Guide

This project uses `uv` for Python dependency management and a small set of standalone scripts (no task runner,
no CI gate script). Most day-to-day productivity comes from the `redis_control.py` runtime control CLI, which
lets you change bot behavior without restarting or redeploying.

## Required Tooling

- **Python 3.13** - pinned via `.python-version`
- **uv** - dependency management and virtualenv; `uv sync` installs dev deps, `uv pip install -r requirements.txt`
  installs runtime deps
- **ruff** - linting (`uv run ruff check .`) and formatting (`uv run ruff format .`)
- **pyright** - type checking (`uv run pyright`)
- **Redis** - localhost:6379, DB 2; runs in WSL on this machine, not as a Windows service or Docker container
  (see [[redis-runs-in-wsl]])
- **NautilusTrader** - trading engine framework (imported by `bot.py` after the startup patches are applied)
- **A public tunnel** (cloudflared or ngrok) - required to expose
  [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) (port 8001) to TradingView

## Recommended Automation

- Lint + format + type-check before any commit:
  ```
  uv run ruff check .
  uv run ruff format .
  uv run pyright
  ```
- Run the relevant phase test script(s) for whatever you changed (see
  [testing-strategy.md](testing-strategy.md)).
- Use `redis_control.py` instead of editing config/redeploying for runtime toggles:
  ```
  uv run python redis_control.py sim|live
  uv run python redis_control.py strategy fusion|tradingview
  uv run python redis_control.py dryrun on|off
  uv run python redis_control.py status   # display_status
  ```
- `uv run python view_paper_trades.py` - quick way to inspect simulated trade history
  (`load_paper_trades`/`display_paper_trades`) without a database client.
- `grafana/import_dashboard.py` automates importing `grafana/dashboard.json` into a Grafana instance via a
  service-account token.

## IDE / Editor Setup

- Configure your editor's Python interpreter to the `uv`-managed virtualenv so `pyright` and `ruff` use the same
  environment as `uv run`.
- Enable ruff's formatter-on-save and pyright/Pylance for inline type errors - both are part of the quality gate.

## Productivity Tips

- When iterating on signal processors or fusion weights, run
  `uv run python core/strategy_brain/test_strategy.py` and/or `uv run python 15m_bot_runner.py --test-mode`
  rather than enabling live mode.
- When iterating on the TradingView path, use `uv run python redis_control.py dryrun on` so trades run the full
  live order path (writing to `tv_dry_run_trades.json`) without risking real funds.
- `monitoring/grafana_exporter.py` exposes `/metrics` on port 8000 by default; pass `--no-grafana` to
  `15m_bot_runner.py`/`bot.py` if you don't need the metrics server during local iteration.

## Related Resources

- [development-workflow.md](development-workflow.md)
