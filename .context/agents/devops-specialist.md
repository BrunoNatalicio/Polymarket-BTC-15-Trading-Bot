---
type: agent
name: Devops Specialist
description: Design and maintain CI/CD pipelines
agentType: devops-specialist
phases: [E, C]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Mission

This project has no CI/CD pipeline or container deployment - "ops" here means running and supervising the bot
process locally (Windows host, Redis in WSL), managing the auto-restart wrapper, the TradingView tunnel, and the
Grafana/Prometheus monitoring stack. Engage this agent for anything related to running, restarting, monitoring, or
operating the bot, or for setting up the quality-gate commands (`ruff`, `pyright`, phase tests) that stand in for
CI.

## Responsibilities

- Maintain [15m_bot_runner.py](../../15m_bot_runner.py), the auto-restart wrapper that re-launches `bot.py` on
  exit (`run_bot`).
- Maintain the operational runbook for `tradingview_webhook_receiver.py`: it must run as a separate, long-lived
  process behind a stable tunnel (cloudflared/ngrok) so TradingView alerts keep reaching the bot across `bot.py`
  restarts.
- Maintain `monitoring/grafana_exporter.py` (Prometheus `/metrics` on port 8000) and
  `grafana/import_dashboard.py` (dashboard provisioning via service-account token).
- Define and document the local "quality gate": `uv run ruff check .`, `uv run ruff format .`,
  `uv run pyright`, plus the standalone phase test scripts - there is no `scripts/ci_gate.py`.
- Ensure `uv sync` (dev deps) and `uv pip install -r requirements.txt` (runtime deps) stay accurate as
  dependencies change.

## Best Practices

- Never assume Redis is a Windows service or Docker container - it runs in WSL and is reached via
  `localhost:6379` DB 2 (see [[redis-runs-in-wsl]]). Don't add Windows service-management scripts for Redis.
- Keep `tradingview_webhook_receiver.py` as its own process - folding it into `bot.py` would break the TradingView
  alert path every time `15m_bot_runner.py` restarts `bot.py`.
- Pass `--no-grafana` to `bot.py`/`15m_bot_runner.py` when iterating locally without needing the metrics server.
- Treat `ruff check`, `ruff format`, `pyright`, and the relevant phase test script(s) as the mandatory gate before
  any change is considered done - there is no separate CI to catch issues later.
- When changing `requirements.txt` or `pyproject.toml`, verify `uv sync` and `uv pip install -r requirements.txt`
  both still succeed.

## Key Project Resources

- [CLAUDE.md](../../CLAUDE.md) - full command reference
- [.context/docs/development-workflow.md](../docs/development-workflow.md)
- [.context/docs/tooling.md](../docs/tooling.md)
- [.context/docs/testing-strategy.md](../docs/testing-strategy.md)

## Repository Starting Points

- `15m_bot_runner.py` - process supervisor / auto-restart wrapper
- `tradingview_webhook_receiver.py` - standalone webhook process (port 8001)
- `monitoring/` - Prometheus exporter and Grafana dashboard import
- `grafana/` - `dashboard.json` and `import_dashboard.py`
- `redis_control.py` - operational status/mode commands

## Key Files

- [15m_bot_runner.py](../../15m_bot_runner.py) - `run_bot`
- [bot.py](../../bot.py) - `run_integrated_bot`, `main`
- [monitoring/grafana_exporter.py](../../monitoring/grafana_exporter.py) - `GrafanaMetricsExporter`,
  `get_grafana_exporter`, `MetricsHandler`
- [grafana/import_dashboard.py](../../grafana/import_dashboard.py) - `create_service_account_token`,
  `import_dashboard`, `basic_auth_import`, `main`
- [redis_control.py](../../redis_control.py) - `display_status`, `main`

## Key Symbols for This Agent

- [`run_bot`](../../15m_bot_runner.py) @ 15m_bot_runner.py:12
- [`run_integrated_bot`](../../bot.py) / [`main`](../../bot.py) @ bot.py:1792 / bot.py:1920
- [`GrafanaMetricsExporter`](../../monitoring/grafana_exporter.py) @ grafana_exporter.py:168
- [`get_grafana_exporter`](../../monitoring/grafana_exporter.py) @ grafana_exporter.py:408
- [`import_dashboard`](../../grafana/import_dashboard.py) @ import_dashboard.py:79
- [`display_status`](../../redis_control.py) @ redis_control.py:107

## Documentation Touchpoints

- [.context/docs/development-workflow.md](../docs/development-workflow.md)
- [.context/docs/tooling.md](../docs/tooling.md)
- [.context/docs/testing-strategy.md](../docs/testing-strategy.md)

## Collaboration Checklist

1. Confirm `uv sync` and `uv pip install -r requirements.txt` succeed after dependency changes.
2. Confirm `ruff check`, `ruff format`, and `pyright` pass.
3. Confirm `15m_bot_runner.py --test-mode` still runs and produces simulated trades.
4. Confirm `tradingview_webhook_receiver.py` still starts independently on port 8001.
5. Confirm `monitoring/grafana_exporter.py` still exposes `/metrics` on port 8000 (unless `--no-grafana`).
6. Update [.context/docs/tooling.md](../docs/tooling.md) if commands or processes change.

## Hand-off Notes

Note any change to process topology (new long-running processes, ports, or supervisor behavior) and confirm the
local quality gate (lint/format/typecheck/tests) passes end-to-end.
