---
source: AGENTS.md
type: generic
---

# AGENTS.md

Guidance for AI coding agents working in this repository. The authoritative, detailed version of everything
here is [CLAUDE.md](CLAUDE.md) and the knowledge base in [.context/docs/](.context/docs/README.md) — read those
first.

## Dev environment tips

- Python 3.13 (pinned in `.python-version`), managed with `uv`.
- `uv sync` installs the dev group (ruff, pyright, pytest, pip-audit); runtime deps live in
  `requirements.txt` (`uv pip install -r requirements.txt`).
- Redis (localhost:6379, DB 2) is the runtime control plane; on this machine it runs inside WSL.
- Never reorder the monkey-patch imports at the top of `bot.py` (`patch_gamma_markets`,
  `patch_market_orders` must apply before NautilusTrader imports).

## Testing instructions

- No pytest suite — tests are standalone scripts run with `uv run python <script>`:
  `data_sources/test.py`, `core/ingestion/test_ingestion.py`, `core/nautilus_core/test_nautilus.py`,
  `core/strategy_brain/test_strategy.py`, `execution/test_execution.py`, `test_tradingview_webhook.py`.
- Quality gate before any commit: `uv run ruff check .`, `uv run ruff format .`, `uv run pyright`, plus the
  phase test script(s) for whatever you touched.
- Behavior validation: `uv run python 15m_bot_runner.py --test-mode` (simulated trades) or
  `redis_control.py dryrun on` (full live order path, `submit_order` skipped).

## Hard invariants

- Dry run = full live order path with `submit_order` as the ONLY skipped call. Never add earlier branches.
- Bet size = `MARKET_BUY_USD` env (default $1, currently $3); `execution/risk_engine.py` caps every position
  at it — every path, no exceptions.
- `fusion` and `tradingview` strategies are mutually exclusive (`btc_trading:active_strategy`).
- `tradingview_webhook_receiver.py` stays a separate process from `bot.py`.

## Repository map

- `bot.py` — main NautilusTrader strategy integrating all phases
- `15m_bot_runner.py` — auto-restart supervisor for `bot.py`
- `tradingview_webhook_receiver.py` — standalone webhook HTTP server (port 8001)
- `redis_control.py` — runtime control CLI (sim/live, strategy, dry run)
- `core/` — ingestion, NautilusTrader integration, signal processors, fusion engine
- `execution/` — risk engine, execution engine, Polymarket CLOB client
- `data_sources/` — Binance/Coinbase/Solana/news adapters
- `monitoring/`, `feedback/`, `grafana/` — performance tracking, learning loop, dashboards

## AI Context References

- Project instructions: [CLAUDE.md](CLAUDE.md)
- Documentation index: [.context/docs/README.md](.context/docs/README.md)
- Operator runbook (TradingView strategy): [.context/docs/tradingview-runbook.md](.context/docs/tradingview-runbook.md)
- Agent playbooks: [.context/agents/README.md](.context/agents/README.md)
