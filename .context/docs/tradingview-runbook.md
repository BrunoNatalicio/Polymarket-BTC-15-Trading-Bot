---
type: doc
name: tradingview-runbook
description: Operator runbook for the TradingView webhook strategy - setup, dry-run validation, go-live, and troubleshooting
category: runbook
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## TradingView Webhook Strategy — Operator Runbook

Step-by-step guide to set up, validate, and operate the TradingView-alert-driven strategy. In this strategy
your TradingView indicator decides every entry; the bot only validates the signal (secret, freshness, dedup),
applies risk limits, and executes. For the architecture behind this, see [architecture.md](architecture.md) and
[data-flow.md](data-flow.md); for the conceptual reference, see [glossary.md](glossary.md).

## 1. Prerequisites

- `.env` configured (copy from `.env.example`) with:
  - `POLYMARKET_PK`, `POLYMARKET_API_KEY`, `POLYMARKET_API_SECRET`, `POLYMARKET_PASSPHRASE` (live trading only)
  - `TRADINGVIEW_WEBHOOK_SECRET` — long random string; the receiver **refuses to start** if unset
  - `TRADINGVIEW_WEBHOOK_PORT` (default `8001`), `TRADINGVIEW_SIGNAL_CONFIDENCE` (default `0.75`),
    `TRADINGVIEW_SIGNAL_TTL_SECONDS` (default `30`)
- Redis reachable at `localhost:6379` DB 2. On this machine Redis runs **inside WSL** (no Windows
  service/Docker); if connections are refused with error 10061, start it first:
  `wsl sudo service redis-server start` (or `wsl redis-server --daemonize yes`). Inspect keys from Windows
  with `wsl redis-cli -n 2 <command>`.
- A TradingView plan that supports webhook alerts (Pro or higher), with your indicator already published/added
  to a chart.
- `cloudflared` or `ngrok` installed for the public tunnel.

## 2. Process Topology

Three long-running processes, started independently:

| Process | Command | Restarts? |
| --- | --- | --- |
| Webhook receiver | `uv run python tradingview_webhook_receiver.py` | No — must stay up so the tunnel target is stable |
| Tunnel | `cloudflared tunnel --url http://localhost:8001` (or `ngrok http 8001`) | No — restart changes quick-tunnel URLs |
| Bot | `uv run python 15m_bot_runner.py --test-mode` (or `--live`) | Yes — supervisor restarts `bot.py` every ~90 min |

The receiver ([tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py)) is deliberately a
separate process from the bot: `15m_bot_runner.py` restarts `bot.py` periodically, and the tunnel must keep a
stable local target across those restarts. Signals flow receiver → Redis list
(`btc_trading:tradingview_signals`) → bot consumer thread (BLPOP).

Restart semantics, stated plainly:
- **Bot restart** (~once per 90 min, takes well under a minute): the Redis queue itself persists, but any
  signal that waited longer than the 30s TTL is discarded as stale when the bot comes back. In practice most
  alerts that fire *during* a bot restart do not trade (only one arriving in the final seconds of the restart
  can still be fresh enough) — a deliberate safety choice (§8), since a 30s-old signal in a 15-minute market
  is no longer the price your indicator saw.
- **Receiver restart**: no state is lost (the queue lives in Redis), but alerts that arrive while the receiver
  is down get no HTTP 200 and are simply gone — TradingView does not reliably retry. Keep receiver downtime
  short and deliberate.

The receiver binds `127.0.0.1` only — nothing but the local tunnel can reach it directly.

## 3. First-Time Setup

1. **Set the secret** in `.env`: `TRADINGVIEW_WEBHOOK_SECRET=<48+ random chars>`. Never reuse it elsewhere;
   it is the only thing authenticating TradingView to your bot.
2. **Start the receiver**: `uv run python tradingview_webhook_receiver.py`. Confirm the log line
   `listening on http://127.0.0.1:8001/webhook`.
3. **Start the tunnel**: `cloudflared tunnel --url http://localhost:8001`. Note the public URL it prints
   (e.g. `https://<random>.trycloudflare.com`).
   - Quick tunnels get a **new URL on every restart** — fine for testing, painful for production. For a stable
     hostname use a named Cloudflare tunnel or a paid ngrok domain.
4. **Sanity-check both hops**: `curl http://localhost:8001/health` → body `ok` (receiver itself), then
   `curl https://<tunnel-host>/health` → body `ok` (tunnel + receiver). If the first works and the second
   doesn't, the problem is the tunnel, not the receiver.
5. **Create two alerts in TradingView** on your indicator (one per direction):
   - *Settings → Notifications → Webhook URL*: `https://<tunnel-host>/webhook`
   - *Message* — exactly this JSON (TradingView sends it as `text/plain`; the receiver parses it anyway):
     ```json
     {"secret": "YOUR_SECRET", "signal": "UP"}
     ```
     and for the other alert:
     ```json
     {"secret": "YOUR_SECRET", "signal": "DOWN"}
     ```
   - `signal` is case-insensitive and whitespace-tolerant; anything other than UP/DOWN is rejected with 400.
   - How your indicator emits UP vs DOWN is indicator-specific: typically you create one alert on the "buy"
     condition and one on the "sell" condition of the same indicator. The bot doesn't care which condition
     fired — only the `signal` field in the message.
   - The secret now also lives inside TradingView's alert config. To rotate it: change
     `TRADINGVIEW_WEBHOOK_SECRET` in `.env`, restart the receiver, update both alert messages.
6. **Activate the strategy**: `uv run python redis_control.py strategy tradingview`
   (asks for a `yes` confirmation only when the bot is currently in live mode; otherwise switches silently).
7. **Verify**: `uv run python redis_control.py status` prints three facts — sim/live mode, a `Strategy:` line,
   and (when dry run is on) a `TV Dry Run: ON` line. It should now show `Strategy: TRADINGVIEW WEBHOOK`.

## 4. Dry-Run Validation Workflow

Always validate a new indicator (or any change to the webhook path) with dry run before risking funds.
This section assumes §3 is done: receiver and tunnel running, alerts configured. The sim/live mode setting is
irrelevant here — dry run takes precedence over it for webhook trades, and the fusion path is gated off by
`strategy tradingview`.

Dry run = the **exact live order path** — token resolution, instrument cache lookup, quantity/precision math,
order construction — diverging at a single point: `submit_order` is not called. This 100% fidelity is a hard
invariant (see §8).

```bash
uv run python redis_control.py dryrun on        # FIRST — before the bot starts
uv run python redis_control.py strategy tradingview
uv run python redis_control.py status           # confirm: TRADINGVIEW + dry run ON
uv run python 15m_bot_runner.py --live
```

Why `--live` and not `--test-mode`? Dry run's whole point is rehearsing the *live* configuration — real
markets, real instrument cache, real order construction. `--test-mode` exercises a different cadence
(simulated trades every minute) and would not validate the live path. This is still risk-free **provided the
order above is respected**: enable `dryrun on` and switch the strategy *before* starting the bot, and confirm
both via `status`. With `strategy tradingview` active the fusion path is skipped entirely, and with dry run on
the webhook path never calls `submit_order` — so no code path can submit an order.

Then either wait for real TradingView alerts or inject a test signal locally
(on Windows PowerShell use `curl.exe`, not the `curl` alias):

```bash
curl.exe -X POST http://localhost:8001/webhook -d "{\"secret\":\"YOUR_SECRET\",\"signal\":\"UP\"}"
```

Expected observable sequence:
1. Receiver log: `Signal queued: UP` (HTTP 200).
2. Bot log: `TRADINGVIEW SIGNAL TRADE: UP ...` then `DRY RUN - LIVE ORDER PATH` then
   `DRY RUN - ORDER BUILT AND VALIDATED, NOT SUBMITTED`.
3. A record appended to `tv_dry_run_trades.json` (timestamp, direction, price, qty, order id, market slug).

Let it run with real alerts for at least a day (ideally 20+ signals across different market conditions).
Review `tv_dry_run_trades.json` against actual market outcomes to measure the indicator's hit rate — for a $1
binary market, the hit rate must beat the average entry price (e.g. entries around $0.60 need >60% accuracy
to be profitable). When satisfied, proceed to §5.

## 5. Go-Live Checklist

Run through in order; each step has a verification.

1. `uv run python test_tradingview_webhook.py` → last line reads `RESULT: N passed, 0 failed` (exit code 0).
   Redis does not need to be up — the Redis round-trip section skips gracefully — but with Redis up all
   sections run.
2. Receiver up → `curl https://<tunnel-host>/health` returns the body `ok` with HTTP 200.
3. TradingView alerts configured with the **current** tunnel URL (quick-tunnel URLs change on restart).
4. `uv run python redis_control.py status` → shows `Strategy: TRADINGVIEW WEBHOOK` **and** `TV Dry Run: ON`
   (dry run must still be ON at this step; it is turned off only at step 8).
5. Dry-run period completed (§4) and `tv_dry_run_trades.json` reviewed — entries fire when your indicator
   fires, direction mapping is correct (UP buys YES, DOWN buys NO). In the bot logs, `DISCARDED — stale`,
   `DISCARDED — no active market`, and `IGNORED — already traded market` are normal protections; what should
   make you stop and investigate is `IGNORED — fusion strategy active` (wrong strategy key) or frequent
   `Risk engine blocked` lines.
6. Funds: the risk engine allows up to 5 concurrent $1 positions ($10 max exposure) — keep at least ~$15 USDC
   in the Polymarket wallet so trades never fail on balance.
7. `uv run python redis_control.py live` → type `yes` at the prompt.
8. `uv run python redis_control.py dryrun off` — **the very next alert can submit a real order within
   seconds.** The safe procedure: pause the TradingView alerts, confirm the queue is empty
   (`wsl redis-cli -n 2 LLEN btc_trading:tradingview_signals` → `0`), flip dry run off, then re-enable the
   alerts. (Checking LLEN alone leaves a race window between the check and the flip.)
9. Watch the first live trade end-to-end: bot log (`REAL ORDER SUBMITTED!`), Prometheus metrics at
   `http://localhost:8000/metrics`, the Grafana dashboard (default `http://localhost:3000`, provisioned from
   `grafana/dashboard.json`), and the Polymarket UI.

**Instant kill switch**: `uv run python redis_control.py sim` flips to paper trading without restarting
anything. `redis_control.py strategy fusion` silences the webhook path entirely — the consumer keeps popping
each arriving signal but logs `IGNORED — fusion strategy active` instead of trading.

## 6. Day-to-Day Operations

- **Status**: `uv run python redis_control.py status` — shows sim/live, active strategy, dry-run flag.
- **Records**: live trades → bot log + Grafana; dry-run trades → `tv_dry_run_trades.json`; paper trades →
  `paper_trades.json` (`uv run python view_paper_trades.py`).
- **Queue inspection** (rarely needed; redis-cli lives in WSL on this machine):
  ```bash
  wsl redis-cli -n 2 LRANGE btc_trading:tradingview_signals 0 -1   # pending signals
  wsl redis-cli -n 2 GET btc_trading:tv_last_traded_market         # last traded market id
  ```
- **Receiver restart** corrupts nothing (all state lives in Redis), but alerts that fire while it's down are
  lost — TradingView does not reliably retry. **Tunnel restart** changes quick-tunnel URLs — update the
  TradingView alerts.
- The bot enforces **max 1 trade per 15-minute market**. The dedup key only needs to outlive the current
  15-minute market; its 1-hour expiry is just housekeeping. The point of keeping it in Redis (instead of bot
  memory) is that a bot restart *mid-market* can't cause a second trade in the same market. Extra alerts in
  the same market are logged (`already traded market`) and dropped.

## 7. Troubleshooting Matrix

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Receiver exits at startup: `TRADINGVIEW_WEBHOOK_SECRET is not set` | Missing/empty secret in `.env` | Set the secret, restart receiver |
| Receiver exits: `Redis connection failed ... 10061` | Redis (WSL) not running | Start Redis inside WSL, restart receiver |
| Alert returns `403 forbidden` | Secret in alert JSON ≠ `.env` secret | Fix the alert message; never log/echo the secret |
| Alert returns `400 invalid JSON` / `invalid signal` | Alert message isn't the exact JSON, or signal ≠ UP/DOWN | Copy the JSON template from §3 verbatim |
| Alert returns 200 but bot does nothing | Bot not running, or strategy ≠ `tradingview` | `redis_control.py status`; start bot; `strategy tradingview` |
| Bot log: `IGNORED — fusion strategy active` | `btc_trading:active_strategy` is `fusion` | `uv run python redis_control.py strategy tradingview` |
| Bot log: `DISCARDED — stale` | Signal sat in the queue > TTL (bot was down, or tunnel latency) | Expected protection; check why the bot was slow/down |
| Bot log: `IGNORED — already traded market` | Second alert in same 15-min market | Expected (dedup); at most 1 trade per market |
| Bot log: `DISCARDED — no active market` / `no quote yet` | Signal arrived between markets or before the first tick | Expected at market boundaries; signal would be stale by next market anyway |
| Bot log: `Risk engine blocked TradingView trade` | Position-count/exposure/daily-loss limit hit | Review open positions; limits live in [execution/risk_engine.py](../../execution/risk_engine.py) |
| Bot log: `No liquidity for BUY/SELL` | Orderbook essentially empty (bid/ask ≤ $0.02) | Expected guard; no action |
| TradingView shows webhook errors | Tunnel down or URL stale | `curl http://localhost:8001/health` first: if it returns `ok`, the receiver is fine and the tunnel is the problem — restart tunnel, update alert URL |
| Dry-run trades missing from `tv_dry_run_trades.json` | Dry run not enabled, or trade was blocked earlier (see rows above) | `redis_control.py status`; search bot log for the signal |

## 8. Invariants — Do Not Break

These are hard requirements, enforced by convention and review (see [glossary.md](glossary.md) §Domain Rules
and [security.md](security.md)):

- **Dry-run fidelity**: dry run must execute the full live order path with `submit_order` as the *only*
  skipped call (`_place_real_order(dry_run=True)` in [bot.py](../../bot.py)). Never add earlier branches.
- **Strategy exclusivity**: `fusion` and `tradingview` are mutually exclusive via
  `btc_trading:active_strategy`; never both.
- **Signal TTL**: webhook signals older than `TRADINGVIEW_SIGNAL_TTL_SECONDS` (30s) must be discarded.
- **Per-market dedup**: at most one TradingView trade per 15-minute market
  (`btc_trading:tv_last_traded_market`, Redis-backed so it survives bot restarts).
- **$1 position cap**: enforced by `RiskEngine` for every path — fusion, webhook, sim, live, dry run.
- **Receiver stays a separate process**: never fold it into `bot.py`.

## Related Resources

- [architecture.md](architecture.md) — system boundaries and design decisions
- [data-flow.md](data-flow.md) — the TradingView path step by step
- [security.md](security.md) — secret handling and incident response
- [testing-strategy.md](testing-strategy.md) — `test_tradingview_webhook.py` and quality gates
- [../../CLAUDE.md](../../CLAUDE.md) — command reference
