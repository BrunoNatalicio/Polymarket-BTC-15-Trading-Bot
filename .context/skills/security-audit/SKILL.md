---
type: skill
name: Security Audit
description: Review code and infrastructure for security weaknesses. Use when Reviewing code for security vulnerabilities, Assessing authentication/authorization, or Checking for OWASP top 10 issues
skillSlug: security-audit
phases: [R, V]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Verify `POLYMARKET_PK`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE` are read only
   from environment/`.env`, never hardcoded, logged, or written to `tv_dry_run_trades.json` or paper-trade JSON
   files.
2. Audit `tradingview_webhook_receiver.py`'s `validate_secret` - confirm it runs before `parse_alert` and before
   any push to `btc_trading:tradingview_signals`, and that it rejects missing/incorrect secrets with 401, not a
   silent pass-through.
3. Confirm the Redis control plane (`localhost:6379` DB 2, `btc_trading:*` keys, see [[redis-runs-in-wsl]]) is
   only reachable from localhost/WSL - flag any change that binds Redis to `0.0.0.0` or exposes it via a tunnel.
4. Audit `execution/risk_engine.py` (`RiskLimits`, `RiskEngine`) - confirm the $1 max position size and SL/TP
   enforcement apply on every order-submission path, including `_execute_webhook_trade`.
5. Audit TradingView signal handling - confirm `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) staleness check and
   `btc_trading:tv_last_traded_market` per-market dedup run before order submission.
6. Audit dry-run fidelity - confirm `_place_real_order(dry_run=True)` follows the exact same path as live and
   the *only* divergence is that `submit_order` is not called, with no earlier branches.
7. Run `uv run pip-audit` (declared as a dev dependency) when dependencies changed.
8. Report findings by severity with file:line and recommended fix; recommend blocking on credential exposure,
   risk-cap bypass, or webhook auth bypass.

## Examples

**Critical finding:**
```
## Critical: Webhook auth bypass

File: tradingview_webhook_receiver.py:94 (WebhookHandler.do_POST)
The new `if alert.get("source") == "internal": return self._queue_signal(alert)` branch
skips validate_secret entirely for payloads with source="internal". Since "source" is
attacker-controlled JSON, this allows unauthenticated trade signals.

Fix: Remove the branch, or call validate_secret unconditionally before any branching.
```

**Medium finding:**
```
## Medium: pip-audit flags outdated dependency

`uv run pip-audit` reports CVE-XXXX-XXXX in <package>==X.Y.Z (used by data_sources/news_social).
No known exploit path in this codebase's usage, but recommend upgrading to X.Y.Z+1
in the next dependency bump.
```

## Quality Bar

- Treat `POLYMARKET_PK` as a hot-wallet private key - any logging, error message, or exception trace that could
  contain it is a critical finding, not a style issue.
- The TradingView webhook is the only externally-reachable network surface - scrutinize it for injection,
  replay, and auth-bypass with the same rigor as a public API.
- Any new branch added before `submit_order` in the live order path is a dry-run-fidelity risk and must be
  checked both ways (could make dry-run place real orders, or make live silently skip orders).
- Redis having no auth is an accepted risk *only* while bound to localhost/WSL - any change that makes it
  reachable from outside the host is critical.
- Flag new outbound network calls (new exchange, new RPC endpoint) for review of what data they send/receive.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
