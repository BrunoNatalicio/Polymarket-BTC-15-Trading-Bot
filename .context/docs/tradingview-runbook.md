---
type: doc
name: tradingview-runbook
description: Operator runbook for the TradingView webhook strategy - named-tunnel setup, dry-run validation, enriched data collection, go-live, and troubleshooting
category: runbook
generated: 2026-06-12
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

Three application processes (webhook receiver, bot, backtest recorder) plus the public tunnel:

| Process | Command | Restarts? |
| --- | --- | --- |
| Webhook receiver | `uv run python tradingview_webhook_receiver.py` | No — must stay up so the tunnel target is stable |
| Tunnel (production) | Named Cloudflare tunnel `tvbot` → `tvbot.<your-domain>`, run as a Windows **service** (setup in §3) | Auto — Windows service; survives reboot, logout, and bot restarts |
| Tunnel (testing only) | `cloudflared tunnel --url http://localhost:8001` (or `ngrok http 8001`) | No — restart changes the throwaway `trycloudflare.com` URL |
| Bot | `uv run python 15m_bot_runner.py --test-mode` (or `--live`) | Yes — supervisor restarts `bot.py` every ~90 min |
| Backtest recorder | `uv run python -m backtest record` | No — keep it always up; records orderbooks + signals to `backtest/data/backtest.db` (irreplaceable data — Polymarket has no historical L2 API) |

Production uses the **named tunnel** because it gives a stable hostname and runs as a service independent of any
terminal — it does not need re-launching after a reboot or after the bot's 90-minute restarts. The quick tunnel
is for throwaway local testing only (its URL changes on every restart).

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

**Logs.** `start_tradingview_stack.ps1` runs each process in its own console window, and each also tees its
loguru output to a rotating file under `logs/`: bot → `logs/bot.log`, receiver → `logs/receiver.log`,
recorder → `logs/recorder.log` (20 MB rotation, 14-day retention, UTF-8, `enqueue` for thread safety). `tail`
these to audit after the window scrolls — e.g. `Pre-subscribed NEXT market`, `Position size: $X.XX
(MARKET_BUY_USD)`, market selection, and errors. These are **operational** logs, separate from `backtest.db`
(the recorder's structured store). `logs/` and `*.log` are gitignored.

### 2.1 Redis Resilience (Self-Healing Consumer)

The bot's webhook consumer tolerates Redis being unavailable — at boot or mid-run — instead of degrading
permanently. (The 2026-06-16 incident: Redis went down during one of the 90-minute restarts, `init_redis()`
returned `None`, and the consumer never came back until a manual restart — even after Redis recovered.) Three
layers now cover it:

- **Bounded boot retry.** `init_redis` retries on startup (`REDIS_CONNECT_RETRIES` × `REDIS_CONNECT_RETRY_DELAY`,
  default 5 × 3s ≈ 12s), so a Redis still coming up (e.g. WSL just restarted) isn't missed. This stays well within
  the supervisor's `BOT_STARTUP_TIMEOUT_S` (default 120s).
- **Self-healing consumer.** The consumer thread **always starts** and reconnects from inside its own loop: if
  `redis_client` is `None` (Redis down at boot) or a `BLPOP` fails (connection dropped at runtime), it retries
  every `REDIS_RECONNECT_SECONDS` (default 5s) via the pure `redis_resilience.ensure_client`, instead of exiting.
  A healthy boot logs `TradingView webhook consumer started (… self-healing Redis)`; a recovery logs
  `Redis connection established`.
- **One-shot mode re-force.** If the boot-time `simulation_mode` write was skipped because Redis was down, the
  consumer re-applies the session's sim/live mode (the mode the bot was **launched** with — `--live`,
  `--test-mode`, or sim) **once** on its first reconnect (`Redis recovered — session mode re-forced to …`), so a
  stale value from a previous run can't silently take over. You can't change the mode via `redis_control.py`
  while Redis is down (it has nothing to write to), so this re-force has nothing of yours to clobber; once it has
  fired, any later `redis_control.py` change is respected across reconnect blips.

| Env var | Default | Effect |
| --- | --- | --- |
| `REDIS_CONNECT_RETRIES` | `5` | Boot connection attempts before `init_redis` gives up |
| `REDIS_CONNECT_RETRY_DELAY` | `3` | Seconds between boot attempts |
| `REDIS_RECONNECT_SECONDS` | `5` | Consumer back-off between runtime reconnect attempts |

Two boundaries to keep in mind:
- The **receiver** ([tradingview_webhook_receiver.py](../../tradingview_webhook_receiver.py)) is unchanged — it
  still **exits at startup** if Redis is unreachable (§7), since it is a separate process with no node to keep alive.
- This is **distinct from the startup watchdog** (§7, [architecture.md](architecture.md)): the watchdog recovers a
  boot that hangs *inside* `node.run()`; the self-healing consumer recovers a *Redis* outage on an otherwise-healthy
  boot. Different failure modes, both covered.

## 3. First-Time Setup

1. **Set the secret** in `.env`: `TRADINGVIEW_WEBHOOK_SECRET=<48+ random chars>`. Never reuse it elsewhere;
   it is the only thing authenticating TradingView to your bot.
2. **Start the receiver**: `uv run python tradingview_webhook_receiver.py`. Confirm the log line
   `listening on http://127.0.0.1:8001/webhook`.
3. **Start the tunnel.** Production uses a **named Cloudflare tunnel** (stable hostname, runs as a Windows
   service). A quick tunnel is acceptable only for throwaway testing.

   **Production — named tunnel (one-time setup).** Prerequisite: a domain **already added to your Cloudflare
   account** (the tunnel's DNS record is created in that zone). The three commands below write two files into
   `%USERPROFILE%\.cloudflared\` that later steps depend on: `login` writes `cert.pem`, `create` writes the
   `<uuid>.json` credentials.
   ```bash
   cloudflared tunnel login                                  # browser auth; writes cert.pem
   cloudflared tunnel create tvbot                           # creates the tunnel + a <uuid>.json credentials file
   cloudflared tunnel route dns tvbot tvbot.<your-domain>    # DNS CNAME -> the tunnel
   ```
   Write `%USERPROFILE%\.cloudflared\config.yml`:
   ```yaml
   tunnel: <tunnel-uuid>
   credentials-file: C:\Users\<you>\.cloudflared\<tunnel-uuid>.json
   ingress:
     - hostname: tvbot.<your-domain>
       service: http://localhost:8001
     - service: http_status:404
   ```
   Then install it as a service and apply the **two Windows gotchas** (both need an elevated/admin shell):
   - The service runs as **LocalSystem**, which looks for `config.yml` in
     `C:\Windows\System32\config\systemprofile\.cloudflared\`, **not** your user profile. Copy `config.yml`,
     `cert.pem`, and the `<uuid>.json` credentials there.
   - `cloudflared service install` registers the service with **no run arguments**, so it starts and exits
     immediately (symptom: service shows *failed to start*, or RUNNING with **0 connections**). Point its
     ImagePath at the real `tunnel run`:
     ```powershell
     Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\cloudflared' -Name ImagePath `
       -Value '"C:\Users\<you>\AppData\Local\Microsoft\WinGet\Packages\Cloudflare.cloudflared_*\cloudflared.exe" --config "C:\Windows\System32\config\systemprofile\.cloudflared\config.yml" tunnel run' `
       -Type ExpandString
     Start-Service cloudflared
     ```
     (`Cloudflare.cloudflared_*` is a **placeholder** — the registry does not expand `*`; substitute the real
     versioned folder, e.g. `Cloudflare.cloudflared_Microsoft.Winget.Source_8wekyb3d8bbwe`.)
   Healthy = `cloudflared tunnel info tvbot` lists an **active connector with edge connections**, and the
   service log shows `Registered tunnel connection`. (A `Failed to initialize DNS local resolver` line is
   benign and can be ignored.) The hostname is now permanent — TradingView alerts never need their URL updated
   again.

   **Testing only — quick tunnel:** `cloudflared tunnel --url http://localhost:8001` prints a new
   `https://<random>.trycloudflare.com` on every restart.
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
   - **Optional — richer data for the backtest recorder.** Any extra fields you add to the message are carried
     through to `backtest.db` (`signals.raw_json`) by the recorder, while the **trade gate stays `secret` +
     `signal` only** (the bot ignores the extras). This capture only happens when the **recorder is running**
     (`uv run python -m backtest record`): the receiver copies every accepted signal to a separate Redis list
     `btc_trading:tv_signal_log` that only the recorder drains (the bot's trade queue
     `btc_trading:tradingview_signals` is never touched by it). Capturing the BTC close at signal time is valuable: the 15m
     market is binary on the BTC price, so the close lets the replay relate the signal to the strike and the
     token's implied probability. **Quote numeric placeholders** so an empty value can never break the JSON:
     ```json
     {"secret": "YOUR_SECRET", "signal": "UP", "preco_fechamento": "{{close}}", "volume": "{{volume}}"}
     ```
     The `secret` is **never** persisted — `parse_alert` strips it before the signal message is built (§8).
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
To measure the hit rate, don't eyeball `tv_dry_run_trades.json` — run the validation tool, which resolves every
market against the recorded Polymarket CLOB and reports strategy-vs-bot hit-rate + PnL:
```bash
uv run python -m backtest report --signal-source tradingview
```
See [backtest-validation.md](backtest-validation.md) for the outcome model and how to read the two views. For a
$1 binary market the hit rate must beat the average entry price **plus the taker fee** (15m/5m crypto charge
`fee = C × feeRate × p × (1 − p)`, where `C` = shares traded (`stake/p`), crypto `feeRate = 0.07`, charged in
shares — see backtest-validation.md §4).
The fee peaks near $0.50, which is exactly where the rollover fix now lands entries, so it is not negligible:
e.g. entries around $0.50 need meaningfully more than 50% accuracy once the ~3.5%-of-notional fee is paid. Run
`report` with the default `--fee-rate 0.07` to read the **net** PnL. When satisfied, proceed to §5.

## 5. Go-Live Checklist

Run through in order; each step has a verification.

1. `uv run python test_tradingview_webhook.py` → last line reads `RESULT: N passed, 0 failed` (exit code 0).
   Redis does not need to be up — the Redis round-trip section skips gracefully — but with Redis up all
   sections run.
2. Receiver up → `curl https://<tunnel-host>/health` returns the body `ok` with HTTP 200.
3. TradingView alerts point at the **named** tunnel URL (`https://tvbot.<your-domain>/webhook`) — stable, no
   update needed across restarts. (Only a quick tunnel, used for testing, changes its URL on restart.)
4. `uv run python redis_control.py status` → shows `Strategy: TRADINGVIEW WEBHOOK` **and** `TV Dry Run: ON`
   (dry run must still be ON at this step; it is turned off only at step 8).
5. Dry-run period completed (§4) and `tv_dry_run_trades.json` reviewed — entries fire when your indicator
   fires, direction mapping is correct (UP buys YES, DOWN buys NO). In the bot logs, `DISCARDED — stale`,
   `DISCARDED — no active market`, and `IGNORED — already traded market` are normal protections; what should
   make you stop and investigate is `IGNORED — fusion strategy active` (wrong strategy key) or frequent
   `Risk engine blocked` lines.
6. Funds: the risk engine allows up to 5 concurrent positions of `MARKET_BUY_USD` each (exposure cap = 5×, so
   $15 at a $3 bet) — keep at least that much USDC in the Polymarket wallet so trades never fail on balance.
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
- **Records**: operational logs → `logs/{bot,receiver,recorder}.log` (rotating; see §2) + Grafana metrics;
  dry-run trades → `tv_dry_run_trades.json`; paper trades → `paper_trades.json`
  (`uv run python view_paper_trades.py`).
- **Queue inspection** (rarely needed; redis-cli lives in WSL on this machine):
  ```bash
  wsl redis-cli -n 2 LRANGE btc_trading:tradingview_signals 0 -1   # pending signals
  wsl redis-cli -n 2 GET btc_trading:tv_last_traded_market         # last traded market id
  ```
- **Receiver restart** corrupts nothing (all state lives in Redis), but alerts that fire while it's down are
  lost — TradingView does not reliably retry. The **named tunnel** is a Windows service with a stable hostname,
  so its URL never changes; only a **quick tunnel** (testing) gets a new URL on restart, which then requires
  updating the TradingView alerts.
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
| Alert returns 200, `LLEN btc_trading:tradingview_signals` stays > 0, no trade | The consumer now **self-heals** a Redis outage (reconnects every `REDIS_RECONNECT_SECONDS`, default 5s, whether Redis was down at boot or dropped at runtime — see §2.1), so a stuck queue is rarely Redis. Likely causes: `strategy ≠ tradingview` (signals are drained and logged `IGNORED — fusion strategy active`), or a non-Redis boot hang (the consumer launches inside `on_start`, after the live node reaches RUNNING + a 10s post-reconciliation delay) | `redis_control.py status`. If Redis was down, **wait ~5–15s** — the consumer reconnects on its own, no manual restart; a healthy/recovered consumer logs `TradingView webhook consumer started (… self-healing Redis)` then `Redis connection established`, and the queue drains within seconds. Restart `bot.py` only if the queue stays stuck with Redis up and `strategy tradingview` |
| Bot log stops after `Nautilus node built successfully`, `Strategy active` **never** logs, `LLEN` keeps growing for minutes, process alive but frozen | `node.run()` hung connecting the Polymarket client *before* `on_start` ran (intermittent connect stall) — the worst case, since the bot's own 90-min self-restart never arms either. Distinct from the row above, where the node *did* reach RUNNING but only the consumer failed to start | **Don't restart manually** — the supervisor's **startup watchdog** auto-recovers: no `Strategy active` within `BOT_STARTUP_TIMEOUT_S` (set in `.env`, default 120s) → it kills the process tree and relaunches. Allow **~2–3 min** (timeout + a fresh boot). Raise `BOT_STARTUP_TIMEOUT_S` only if boots are legitimately slower, then restart the supervisor to apply. If it recurs, `uv pip install py-spy` and `py-spy dump --pid <bot.py child PID>` (the runner's `python bot.py` subprocess, **not** the supervisor) — a MainThread parked in `node.run` confirms it |
| Bot log at startup: `PolyApiException[status_code=401 ... Unauthorized/Invalid api key]` | Polymarket API credentials in `.env` are invalid/expired | **Dry run is unaffected** (`submit_order` is skipped); regenerate `POLYMARKET_API_KEY`/`SECRET`/`PASSPHRASE` **before going live**, or real orders fail with 401 |
| Bot log: `IGNORED — fusion strategy active` | `btc_trading:active_strategy` is `fusion` | `uv run python redis_control.py strategy tradingview` |
| Bot log: `DISCARDED — stale` | Signal sat in the queue > TTL (bot was down, or tunnel latency) | Expected protection; check why the bot was slow/down |
| Bot log: `IGNORED — already traded market` | Second alert in same 15-min market | Expected (dedup); at most 1 trade per market |
| Bot log: `DISCARDED — no active market` / `no quote yet` | Signal arrived between markets or before the first tick | Expected at market boundaries; signal would be stale by next market anyway |
| Bot log: `Risk engine blocked TradingView trade` | Position-count/exposure/daily-loss limit hit | Review open positions; limits live in [execution/risk_engine.py](../../execution/risk_engine.py) |
| Bot log: `No liquidity for BUY/SELL` | Orderbook essentially empty (bid/ask ≤ $0.02) | Expected guard; no action |
| TradingView shows webhook errors | Tunnel down or URL stale | `curl http://localhost:8001/health` first: if it returns `ok`, the receiver is fine and the tunnel is the problem — restart tunnel, update alert URL |
| Public `/health` returns Cloudflare `error 1033` | Named tunnel has **no active connector** (the LocalSystem service isn't serving it) | `cloudflared tunnel info tvbot`; ensure the config lives in the systemprofile `.cloudflared` and the service ImagePath runs `tunnel run` (§3) |
| Public `/health` returns `502` | Tunnel is up but **nothing is listening on 8001** | Start the receiver; the 502 clears the instant it binds (confirms the tunnel→origin path is intact) |
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
- **Position cap = `MARKET_BUY_USD`** (env, default $1; currently $3): enforced by `RiskEngine` for every
  path — fusion, webhook, sim, live, dry run. The cap scales with the env var.
- **Secret is never persisted**: `parse_alert` excludes `secret` from the fields carried into the signal
  message. It must never reach Redis, `tv_dry_run_trades.json`, or `backtest.db` (`signals.raw_json`). Extra
  alert fields may be collected; the secret may not.
- **Trade gate is `secret` + `signal` only**: extra alert fields are passed through for data collection but
  must never influence the trade decision; `build_signal_message` writes the canonical `id`/`signal`/
  `received_at` last so caller-supplied extras cannot override them.
- **Receiver stays a separate process**: never fold it into `bot.py`.

## Related Resources

- [architecture.md](architecture.md) — system boundaries and design decisions
- [data-flow.md](data-flow.md) — the TradingView path step by step
- [security.md](security.md) — secret handling and incident response
- [testing-strategy.md](testing-strategy.md) — `test_tradingview_webhook.py` and quality gates
- [../../CLAUDE.md](../../CLAUDE.md) — command reference
