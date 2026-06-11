---
type: skill
name: Bug Investigation
description: Investigate bugs systematically and perform root cause analysis. Use when Investigating reported bugs, Diagnosing unexpected behavior, or Finding the root cause of issues
skillSlug: bug-investigation
phases: [E, V]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Reproduce using `uv run python 15m_bot_runner.py --test-mode` (paper trades every minute) before touching
   anything live - never debug against `--live`.
2. Identify which of the 7 pipeline phases the bug is in (data sources -> ingestion -> NautilusTrader -> signal
   processors -> fusion -> risk -> execution -> monitoring -> learning) and run the corresponding phase test
   script (e.g. `uv run python execution/test_execution.py`) to isolate it.
3. Check Redis state with `uv run python redis_control.py` (`display_status`) - many "bugs" are actually
   `btc_trading:simulation_mode`, `btc_trading:active_strategy`, or `btc_trading:tv_dry_run` set unexpectedly
   (Redis runs in WSL at `localhost:6379` DB 2, see [[redis-runs-in-wsl]]).
4. If the bug is in the TradingView path, check `tv_dry_run_trades.json` and run
   `uv run python test_tradingview_webhook.py` to isolate `validate_secret`, TTL, dedup, or payload parsing.
5. If the bug involves NautilusTrader or the Gamma API, verify `patch_gamma_markets.py` and
   `patch_market_orders.py` were applied successfully (they log/exit on failure) and were imported before
   NautilusTrader in `bot.py` - reordering these is a classic source of subtle bugs.
6. Use `git log`/`git blame` on the affected file to find when the behavior changed.
7. Document the root cause and fix approach before editing code.

## Examples

**Bug investigation notes:**
```
## Bug: TradingView trades not executing

### Reproduction:
1. uv run python redis_control.py status -> active_strategy = "tradingview", tv_dry_run = "0"
2. Send a test alert via test_tradingview_webhook.py -> queued in btc_trading:tradingview_signals
3. bot.py consumer never calls _execute_webhook_trade

### Investigation:
- _start_webhook_consumer uses BLPOP with no timeout issue
- Signal timestamp is 45s old by the time it's read -> exceeds TRADINGVIEW_SIGNAL_TTL_SECONDS (30s)
- Root cause: tunnel (cloudflared) added ~40s latency under load

### Fix approach:
Not a code bug - operator issue with tunnel latency. Document expected TTL margin in security.md.
```

## Quality Bar

- Always reproduce in `--test-mode` or with `tv_dry_run=1` first - never use a live-money repro as the first
  step.
- Check Redis control-plane state (`simulation_mode`, `active_strategy`, `tv_dry_run`) before assuming a code bug.
- For execution/risk bugs, confirm whether `RiskLimits` (the $1 cap) or SL/TP enforcement is implicated -
  these are the highest-severity class of bug in this repo.
- For dry-run bugs, verify `_place_real_order(dry_run=True)` still follows the exact same path as live except
  for the `submit_order` call - any divergence is itself the bug.
- Write a regression check into the relevant phase test script once the root cause is confirmed.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
