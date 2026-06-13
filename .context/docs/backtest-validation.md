---
type: doc
name: backtest-validation
description: Validate TradingView signals and bot trades against the Polymarket CLOB - the settle/report commands, the CLOB→Gamma→candle outcome model, strategy-vs-bot reporting, findings, and the pre-live fix list
category: reference
generated: 2026-06-13
status: filled
scaffoldVersion: "2.0.0"
---

## Backtest Validation & Reporting — Signals vs Bot via the CLOB

How we measure whether the strategy actually wins: resolve every market against the
**Polymarket CLOB (Central Limit Order Book) orderbook we recorded**, then report hit-rate + PnL
two ways — the **strategy**
(the signal, mapped to the window it intends to bet) and the **bot** (what it actually traded).
This is the "source of truth" behind the manual validation in
[tradingview-runbook.md](tradingview-runbook.md) §4. For data collection itself see
[data-flow.md](data-flow.md); for terms see [glossary.md](glossary.md).

## 1. Why this exists

A signal firing on the chart says nothing about whether it was right. To grade it we need the
real outcome of each 15-minute (or 5-minute) Polymarket up/down market. Two facts make that
non-trivial, and shaped the design:

- **Gamma de-indexes expired micro-markets.** `gamma-api.polymarket.com/markets?slug=…` returns
  *nothing* for a closed `btc-updown-15m-*` market (querying by `condition_ids` is empty too).
  The candle approximation (Coinbase close vs open) is only a proxy and **has disagreed with the
  CLOB** (see §5). So neither is the authority.
- **The recorder already captures the truth.** It polls each market's CLOB orderbook every ~2s up
  to expiry (`backtest/data/backtest.db`). At expiry the **winning** outcome token bids ~$0.99 and
  the **loser** asks ~$0.01 — an unambiguous, replayable resolution we own. This is the project's
  gold: Polymarket has no historical L2 API, so only what we recorded exists.

## 2. Outcome resolution: CLOB → Gamma → candle

`settlement.resolve_outcome(con, slug, window_start, window_end)`
([backtest/settlement.py](../../backtest/settlement.py)) tries three sources in trust order and
stamps the winner in `markets.outcome` with `markets.outcome_source`:

| Source | How | When it wins |
| --- | --- | --- |
| `clob` | `clob_outcome()` — last recorded snapshot per side ≤ `window_end + 30s`; winner = side with `best_bid ≥ 0.90` **and** loser `best_ask ≤ 0.10` | Authoritative; used whenever we have a decisive recorded book |
| `gamma` | `gamma.get_resolved_outcome()` — `outcomePrices` on a market flagged `closed` | Fallback (rare for these de-indexed markets) |
| `candle` | `_candle_outcome()` — Coinbase BTC-USD candle, `close ≥ open ⇒ YES` | Last resort; **approximation only** |

The two price numbers play different roles: **~$0.99 / ~$0.01** is what a *resolved* market's book
actually shows, while **0.90 / 0.10** are the deliberately tolerant *decision thresholds* — loose
enough that a snapshot taken a few seconds before the final tick still resolves, strict enough to
reject a still-live book. `clob_outcome` returns `None` (not a guess) when books are missing or not
yet decisive (e.g. a pre-expiry 0.60/0.40 book), so the caller falls through cleanly. Prices are
stored as integer thousandths (`best_bid_m`, 0–1000) — exact, no float drift.

## 3. Running it

```bash
# 1. (Optional) Resolve outcomes for all expired markets, CLOB-first, into
#    markets.outcome. `report` already runs this internally — use `settle` on
#    its own only to populate/inspect outcomes without a full report.
uv run python -m backtest settle

# 2. The report (self-contained: runs settle internally): strategy vs bot,
#    hit-rate + PnL, resolved via the CLOB.
uv run python -m backtest report                       # defaults: live stream, 15m, $1 stake
uv run python -m backtest report --start 2026-06-12 --end 2026-06-13
uv run python -m backtest report --series 5m --signal-source tradingview_csv_300s
```

`report` flags: `--start/--end` (ISO date/datetime or unix seconds), `--series {15m,5m}`,
`--signal-source` (SQL LIKE on `signals.source`, default `tradingview` = the live webhook stream),
`--stake` (default `1.0`), `--bot-trades` (default `tv_dry_run_trades.json`). It runs `settle`
internally, so a bare `report` is self-contained. Annotated output:

```
FONTE DA VERDADE — SINAIS vs BOT (resolvido via CLOB)
Período : 2026-06-12 00:00 -> 2026-06-13 10:25 UTC | série 15m | fonte=tradingview
[ESTRATÉGIA] sinal -> janela N+1 -> CLOB
  Sinais recebidos : 10
  Resolvidos       : 10 -> 7 WIN / 3 LOSS (70%)   # graded against the CLOB
    UP  : 7 sinais | 4 WIN | 57%
    DOWN: 3 sinais | 3 WIN | 100%
  PnL              : $+4.14 sobre $10.00 | slip 7 bps   # fill simulado no ask real; slip = slippage em basis points
[BOT] trades realmente executados
  Convertidos      : 9 de 10 sinais (dropados: 1)        # signals received vs trades executed
  Resolvidos       : 8 -> 6 WIN / 2 LOSS (75%)  (sem CLOB: 1)   # sem CLOB = sem book gravado decisivo p/ resolver
  PnL              : $+4.10 sobre $8.00
[GAP] sinais recebidos que o bot NÃO negociou:
    2026-06-13 03:30:03 UTC  DOWN                         # o DOWN dropado: 03:30 UTC = 00:30 local (§5)
```

The report prints timestamps in **UTC**; §5 below narrates in **local time (UTC-3)** — the same
dropped DOWN is `03:30 UTC` in the output and `00:30` in the findings.

## 4. The two views (and why they differ)

The same signal can read as WIN in one view and LOSS in the other — that gap is the point.

- **[ESTRATÉGIA]** — `engine.run_replay`. Each signal is mapped to the window it intends to bet:
  the **next** 15-minute window, "N+1". The mapping is `ingest.attach_target_tokens`'
  `floor(ts/900)*900`; this lands on N+1 (not the window that just closed) because the signal's
  timestamp *is* the bar close, which is also the next window's start. Same intent as
  `csv_hitrate.py`. It then simulates a market-buy on the recorded ask book (real slippage, no
  look-ahead — `merge_asof` matches the first snapshot at or after the signal) and settles against
  `markets.outcome`. This grades the **signal**, independent of bot bugs.
- **[BOT]** — `bot_trades.evaluate_bot_trades`. Reads `tv_dry_run_trades.json` (the market the bot
  **actually** bought + the entry price it paid) and resolves that market via `clob_outcome`.
  `conversion_stats` matches received signals (DB) to executed trades by timestamp (±5s) and lists
  the **dropped** ones. This grades **execution reality**.

PnL model (both views): buying `$stake` at price `p` yields `stake/p` tokens; each winning token
redeems `$1`. `pnl = payout − stake` (identical to `settlement.settle_fill`).

## 5. Findings (2026-06-12 → 13, first live night)

The report surfaces these automatically; recorded here so they aren't rediscovered:

- **Rollover race (entry price).** The "Once per bar close" alert fires at `:00/:15/:30/:45`, which
  is exactly when a 15m Polymarket window **expires**. The bot sometimes still points at the
  expiring window (buys near-resolved, ~$0.99, no edge) and sometimes the fresh one (~$0.50).
  Evidence: 21:15 UP filled at **$0.99** (expiring) vs 22:45 UP at **$0.465** (fresh). Not
  systematic — a race in `current_instrument_index` at the boundary.
- **DOWN signals get dropped.** The 00:30 DOWN was received and logged by the recorder but produced
  **no trade** — the bot bailed before recording (most likely `_no_instrument_id is None` →
  *"cannot bet DOWN. Skipping trade"* in [bot.py](../../bot.py), around line 1491). Confronted with the CLOB it
  would have **won** (the N+1 window resolved NO). A winning signal lost to a bug, not a bad call.
- **Candle ≠ CLOB.** For the 00:30 window the CLOB resolved **NO** but the Coinbase candle said
  **YES** — the first disagreement. Polymarket settles on Chainlink BTC/USD, not Coinbase; near a
  coin-flip the proxy flips. Confirms the CLOB must be the authority, candle only a last resort.

## 6. Pre-live fix list

Fixing these is **out of scope for the report** (it only measures), but they are the open items the
data exposes — clear them before turning dry run off (also see
[tradingview-runbook.md](tradingview-runbook.md) §5):

1. **Market selection at rollover** — when the current market is seconds from expiry, the webhook
   path should target the **next** window (match `attach_target_tokens`' N+1 mapping) so entries
   land near $0.50, not $0.99.
2. **DOWN execution path** — ensure the NO-token instrument (`_no_instrument_id`) is loaded for the
   active market so DOWN signals trade instead of silently dropping.
3. **Polymarket API key 401** — credentials in `.env` are invalid; dry run masks it
   (`submit_order` skipped). Regenerate `POLYMARKET_API_KEY`/`SECRET`/`PASSPHRASE` before live, or
   the first real order fails with 401 (see runbook §7).

## 7. Reference

| Piece | Location |
| --- | --- |
| CLOB resolver, source order, PnL model | `clob_outcome`, `resolve_outcome`, `settle_backfill`, `settle_fill` — [backtest/settlement.py](../../backtest/settlement.py) |
| Bot-trade evaluation + conversion gap | `load_bot_trades`, `evaluate_bot_trades`, `conversion_stats` — [backtest/bot_trades.py](../../backtest/bot_trades.py) |
| Strategy replay (fills, settle) | `run_replay`, `ReplayReport.summary` — [backtest/engine.py](../../backtest/engine.py) |
| Signal→N+1 window mapping | `attach_target_tokens` — [backtest/ingest.py](../../backtest/ingest.py) |
| CLI (`settle`, `report`, `replay`) | [`backtest/__main__.py`](../../backtest/__main__.py) |
| Tests (offline, in-memory SQLite) | `test_clob_outcome`, `test_bot_trades` — [backtest/test_backtest.py](../../backtest/test_backtest.py) |

Relevant schema ([backtest/db.py](../../backtest/db.py)): `markets(outcome, outcome_source,
resolved_at)`; `signals(source)` — `tradingview` is the live webhook stream, `tradingview_csv_*`
are CSV imports (distinct strategies, replay one at a time); `orderbook_snapshots(side_label
YES/NO, best_bid_m, best_ask_m)` in integer thousandths.

## Related Resources

- [tradingview-runbook.md](tradingview-runbook.md) — operate the strategy; §4 dry-run validation, §5 go-live
- [data-flow.md](data-flow.md) — how signals + orderbooks reach `backtest.db`
- [testing-strategy.md](testing-strategy.md) — running the standalone test scripts
- [../../CLAUDE.md](../../CLAUDE.md) — command reference
