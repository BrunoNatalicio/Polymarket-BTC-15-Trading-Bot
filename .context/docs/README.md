# Documentation Index

Welcome to the repository knowledge base. Start with the project overview, then dive into specific guides as needed.

## Core Guides
- [Project Overview](./project-overview.md)
- [Architecture Notes](./architecture.md)
- [Development Workflow](./development-workflow.md)
- [Testing Strategy](./testing-strategy.md)
- [Glossary & Domain Concepts](./glossary.md)
- [Data Flow & Integrations](./data-flow.md)
- [Security & Compliance Notes](./security.md)
- [Tooling & Productivity Guide](./tooling.md)
- [Fusion Strategy (default)](./fusion-strategy.md)
- [TradingView Strategy Runbook](./tradingview-runbook.md)
- [Backtest Validation & Reporting](./backtest-validation.md)
- [TradingView Signal Confirmation Layer](./tv-confirmation-layer.md)
- [TradingView Loss Post-mortem & Findings](./tv-loss-postmortem-findings.md)

## Repository Snapshot
- Directories: `core/`, `data_sources/`, `execution/`, `feedback/`, `grafana/`, `monitoring/`, `workflow/`
- Entry/control scripts: `bot.py`, `15m_bot_runner.py`, `redis_control.py`, `tradingview_webhook_receiver.py`,
  `view_paper_trades.py`
- Startup patches: `patch_gamma_markets.py`, `patch_market_orders.py`
- Tests: `test.py`, `test_tradingview_webhook.py` (plus per-phase `test_*.py` inside packages)
- Config/docs: `pyproject.toml`, `requirements.txt`, `uv.lock`, `CLAUDE.md`, `README.md`

## Document Map
| Guide | File | Primary Inputs |
| --- | --- | --- |
| Project Overview | `project-overview.md` | Roadmap, README, stakeholder notes |
| Architecture Notes | `architecture.md` | ADRs, service boundaries, dependency graphs |
| Development Workflow | `development-workflow.md` | Branching rules, CI config, contributing guide |
| Testing Strategy | `testing-strategy.md` | Test configs, CI gates, known flaky suites |
| Glossary & Domain Concepts | `glossary.md` | Business terminology, user personas, domain rules |
| Data Flow & Integrations | `data-flow.md` | System diagrams, integration specs, queue topics |
| Security & Compliance Notes | `security.md` | Auth model, secrets management, compliance requirements |
| Tooling & Productivity Guide | `tooling.md` | CLI scripts, IDE configs, automation workflows |
| Fusion Strategy (default) | `fusion-strategy.md` | Late-window favorite-follower mechanics, signal-activity gate, calibration-brain roadmap, `fusion-replay` backtest |
| TradingView Strategy Runbook | `tradingview-runbook.md` | Webhook setup, dry-run validation, go-live checklist, troubleshooting |
| Backtest Validation & Reporting | `backtest-validation.md` | CLOB outcome resolution, settle/report commands, strategy-vs-bot hit-rate, pre-live fixes |
| TradingView Loss Post-mortem & Findings | `tv-loss-postmortem-findings.md` | Loss slicing, session/volatility regime, CoinDesk cross-cut, session×p_side edge inversion, EU+band opt-in filter |
