---
type: doc
name: security
description: Security policies, authentication, secrets management, and compliance requirements
category: security
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Security & Compliance Notes

This bot moves real money on a live exchange (Polymarket), so its main security guardrails are: keeping trading
credentials out of source control, enforcing a hard position-size cap regardless of signal confidence, and
validating every external input that can trigger a trade (TradingView webhook payloads, Redis control keys).

## Authentication & Authorization

- **Polymarket CLOB**: authenticated via a private key + API key/secret/passphrase
  (`POLYMARKET_PK`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`), wrapped by
  `PolymarketClient` ([execution/polymarket_client.py](../../execution/polymarket_client.py)). There is no
  multi-user/role model - this is a single-operator bot.
- **TradingView webhook**: [tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py) validates a
  shared secret embedded in the JSON alert body (`validate_secret`) before any signal is queued. Requests without
  a valid secret are rejected.
- **Redis control plane**: no auth model beyond network access to `localhost:6379` DB 2 (WSL-hosted, not exposed
  externally - see [[redis-runs-in-wsl]]). Anyone able to reach Redis can flip `simulation_mode`,
  `active_strategy`, or `tv_dry_run`, so Redis must not be exposed beyond localhost/WSL.
- **Grafana**: `grafana/import_dashboard.py` uses a service-account token (`create_service_account_token`,
  `basic_auth_import`) for dashboard provisioning.

## Secrets & Sensitive Data

- All credentials live in `.env` (copied from `.env.example`, never committed). Required keys: `POLYMARKET_PK`,
  `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE`.
- The TradingView webhook shared secret is read from the environment/config used by
  `tradingview_webhook_receiver.py` and must match the secret configured in the TradingView alert payload.
- No secrets are logged; trade records (`tv_dry_run_trades.json`, paper trade history) contain only market/order
  data, not credentials.
- `POLYMARKET_PK` is a wallet private key with direct fund-moving authority - treat it with the same care as a
  hot-wallet key, not just an API credential.

## Compliance & Policies

- Position-size cap per trade = the configured bet size (`MARKET_BUY_USD` env, default $1; currently $3),
  enforced in `execution/risk_engine.py` (`RiskLimits`) - the primary loss-control mechanism. It scales with
  the env var, but must not be bypassed by any code path.
- Stop-loss/take-profit enforcement also lives in `RiskEngine`.
- TradingView signals are time-bounded: anything older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) is discarded to
  avoid acting on stale market conditions.
- Max 1 trade per 15-minute market via `btc_trading:tv_last_traded_market`, preventing repeated/duplicate
  execution from retried webhooks.
- Dry-run mode (`btc_trading:tv_dry_run`) must mirror the live order path exactly (only `submit_order` is
  skipped) so that pre-production validation reflects true behavior.

## Incident Response

- If the bot behaves unexpectedly in live mode, the fastest mitigation is
  `uv run python redis_control.py sim` to switch to simulation mode without restarting the process.
- `15m_bot_runner.py` auto-restarts `bot.py` on crash; check its logs and `monitoring/performance_tracker.py`
  output / Grafana dashboard (`grafana/dashboard.json`) for anomalous trade patterns.
- For TradingView webhook issues, `tradingview_webhook_receiver.py` runs as a separate process - it can be
  restarted independently without affecting the trading strategy process, and `tv_dry_run_trades.json` can be
  inspected to audit recent webhook-triggered decisions.

## Related Resources

- [architecture.md](architecture.md)
