# TradingView Signal Confirmation Layer

> Reference doc for the calibrated **confirmation layer** that validates TradingView
> webhook signals before execution. Covers the design decision (multi-agent debate),
> the math, the data reality, the backtest/tune apparatus, the empirical findings so
> far, and the hard **n≥200 go-live guard**. Audience: anyone (human or Claude)
> working on this layer in the future.

## 1. Purpose

The TradingView strategy fires a directional signal (UP/DOWN) at each 15m bar close and
the bot trades the freshly-opened Polymarket window. The goal here is a **mathematical
confirmation layer** that filters those signals to raise win-rate and reject noise —
*without* inventing a new strategy.

The key insight (from the debate below): this is **not greenfield**. A confirmation
layer already exists — the book-agreement gate ([`tv_market_select.conviction_stake`](../../tv_market_select.py),
wired at [`bot.py`](../../bot.py) `_handle_tradingview_signal`) refuses any bet the
Polymarket book prices below `TV_MIN_BOOK_PROB` (0.42). That gate is mathematically a
*degenerate Bayesian posterior*. The work is to **generalize it into a calibrated
win-probability**, add **one** orthogonal feature, and tune it on recorded data —
never hand-set.

## 2. The decision: multi-agent debate (Alternative C, phased)

A 4-agent / 4-round debate (`multi-agent-debate` skill, Domain Expert Panel,
long-stubbornness) converged (8.2/10) on a **phased hybrid**:

- **Phase 1** — re-express the hard floor gate as a smooth calibrated posterior
  combining the signal's historical hit-rate (prior) with the book-implied probability.
- **Phase 2** — add **one** partially-orthogonal feature: volatility-normalized spot
  momentum (`z_mom`). The thin 15m book may not yet price a real underlying move.
- **Deferred** — order-book imbalance (OBI) and a Markov streak/exhaustion guard, behind
  a sample-count switch. OBI is predictive only at 3–10s horizons and often doesn't beat
  costs; both wait for n in the hundreds.

Two constraints the math made non-negotiable:

1. **Sample size is the binding constraint.** With only dozens of labeled trades, every
   fitted coefficient overfits. Start with ≤1 added feature.
2. **The taker fee is part of the threshold.** Confirmation must clear
   `expected_edge > fee`, not just `P(win) > 0.5`. The fee peaks near p≈0.5, exactly
   where these signals land.

**Out of scope:** DOWN signals fail on *execution*, not signal timing — historically the
DOWN side (buying the NO token) had wrong price/fill behavior, independent of when the
signal fires. No statistical filter repairs an execution bug, so it's tracked separately.
The confirmation layer is being developed UP-first; DOWN keeps trading on the legacy floor
gate untouched until its execution path is fixed.

## 3. The math (pure helpers in `tv_market_select.py`)

All helpers are dependency-free and unit-tested. Coefficients are fit via `backtest
tune`, never hand-set (mirrors how `conviction_stake`'s floor/full/frac are swept).

### Calibrated posterior — `confirm_probability`
```
logit(P_win) = logit(base_rate)
               + beta_book * (p_side - p_bar)   # the order book
               + beta_mom  * z_mom              # spot momentum (Phase 2)
P_win = sigmoid(logit)
```
- `base_rate` — the prior: the side's unconditional historical hit-rate (estimated from
  data, not tuned).
- `p_side` — book-implied probability of the bought side (UP=YES mid, DOWN=1−YES mid).
- `p_bar` — reference book prob where the posterior equals the prior (fixed 0.5 today).
- `z_mom` — the volatility-normalized spot momentum (Section 3, `z_momentum`); 0 when
  there isn't enough history.
- `tau` — the win-probability threshold the posterior must clear (a tuned knob,
  `--confirm-tau` / `TV_CONFIRM_TAU`).
- `price` — the price you pay for the bought side; for these markets `price == p_side`
  (mid == fair entry). It enters only the fee-breakeven term, a *second*, independent
  gate alongside `tau`.
- Degenerate cases that keep the change honest:
  - `beta_mom == 0` → Phase-1 book-only posterior (z_mom ignored).
  - `beta_book == 0` → flat prior, `P_win == base_rate` for every book.
  - `base_rate == 0.5`, large `beta_book`, `p_bar == p_floor`, `beta_mom == 0`
    → reproduces the hard floor `p_side >= p_floor` **bit-for-bit** (proven in tests).

### Fee-adjusted breakeven — `fee_breakeven_prob`
```
q_breakeven = price / (1 - fee_rate * (1 - price))
```
Derived from the share-skimmed Polymarket taker fee `fee = C·r·p·(1−p)`, where `C` =
shares bought (= `stake / price`), `r` = `fee_rate` (0.07 on 15m/5m crypto), `p` =
`price` (see [`backtest/matching.py`](../../backtest/matching.py) `simulate_market_buy`).
With `fee_rate == 0` it collapses to `price` (pay-the-probability fair value).

### Confirmation gate — `confirm_signal`
```
confirm  iff  P_win >= tau  AND  P_win > fee_breakeven_prob(price, fee_rate)
```

### Volatility-normalized momentum — `z_momentum(closes, k=3, n=20)`
```
r_i   = ln(close_i / close_{i-1})
sigma = stdev(last n returns)
z     = (sum of last k returns) / (sigma * sqrt(k))
```
Returns `0.0` (neutral — never fabricates a signal) when there's too little history or
zero volatility.

## 4. Data reality (why z_mom is sourced the way it is)

- The bot's in-memory `price_history`/`_tick_buffer` hold **Polymarket implied-probability
  mids, NOT BTC spot OHLC** ([`bot.py`](../../bot.py)).
- The only spot data arriving with a live signal is `preco_fechamento` + `volume` in the
  webhook payload, carried through to `raw_json`
  ([`tradingview_webhook_receiver.py`](../../tradingview_webhook_receiver.py)).
- **Backtest close sources for z_mom** (`backtest/ingest.py`):
  - `load_closes_from_signals` — from the signals' own `raw_json.preco_fechamento`. This
    is the **live-aligned** source (same timestamps; the signal's `window_start` equals
    the just-closed bar's close ts). Use this for the live `tradingview` stream.
  - `load_bar_closes_csv` — from a Coinbase/TradingView OHLC export (`time`+`close`;
    bar-close ts = `time + bar_seconds`). Only useful for the historical CSV import
    period, which has **no recorded Polymarket books** (the recorder wasn't running then),
    so it cannot currently be settled against.
- `z_mom_by_window` maps each bar-close ts → `z_momentum` of closes up to AND including it
  (no lookahead).

## 5. Backtest / tune apparatus

The math is shared between live and backtest. The replay engine gates per-side and the
defaults reproduce current behavior exactly when confirmation is off.

- [`backtest/engine.py`](../../backtest/engine.py) `run_replay` — optional
  `confirm_side` / `confirm_base_rate` / `confirm_beta` / `confirm_tau` / `confirm_p_bar`
  / `confirm_beta_mom` / `z_mom_by_window`. Default `confirm_side=None` → the
  `conviction_stake` baseline is byte-for-byte unchanged. When enabled for a side, that
  side uses `confirm_signal`; the other side keeps the floor.
- [`backtest/__main__.py`](../../backtest/__main__.py) `tune --confirm-side UP`:
  - Estimates `base_rate` from a no-gate replay (the side's unconditional win-rate).
  - Compares against the current live gate (`--current-floor`, default 0.42).
  - Sweeps `beta_book × beta_mom × tau`, picks argmax(PnL) on that side.
  - `--closes-from-signals` (live-aligned) or `--closes-csv PATH` enables the Phase-2
    z_mom feature; omit both for a book-only (Phase 1) sweep.

Run examples:
```bash
# Phase 1 (book-only)
uv run python -m backtest tune --confirm-side UP
# Phase 2 (book + z_mom, live-aligned closes)
uv run python -m backtest tune --confirm-side UP --closes-from-signals
```

## 6. Findings so far (LOW confidence — tiny sample)

As of the last run (live `tradingview` stream, n=40 settled UP). *"Settled"* = the signal
was matched to a recorded Polymarket book and resolved to a win/loss label; un-resolved
signals don't count toward n.

- **`base_rate` for UP over recorded books = 52.5%** (21/40) — NOT the 64% headline (that
  came from a CLOB-candle proxy, not the full book replay with fills + fee).
- The **current floor-0.42 gate already lifts UP to 62.5%** (20/32) and makes +$10. (The
  floor skips the 8 signals whose book prob sat below 0.42, hence 32 of the 40.)
- Neither the book-only posterior nor book+z_mom **beats the current gate** at n=40. The
  book-only posterior is just a smooth floor (expected); z_mom changes decisions (wiring
  confirmed) but adds no edge at this sample, partly because live signals are sparse
  (gaps between fires make signal-to-signal returns noisy).

## 7. Go-live guard (n≥200) and how to graduate

**Hard rule, enforced in code:** `MIN_LIVE_SAMPLE = 200` in
[`backtest/__main__.py`](../../backtest/__main__.py). While the side's settled sample is
below 200, `tune --confirm-side` still prints the table but **suppresses the suggested
`TV_CONFIRM_*` env defaults** — any argmax over a tiny sample is noise.

To graduate to live (only when the `tradingview` stream has ≥200 settled):
1. `uv run python -m backtest tune --confirm-side UP --closes-from-signals` → it now emits
   suggested `TV_CONFIRM_SIDE / TV_CONFIRM_BASE_RATE / TV_CONFIRM_BETA /
   TV_CONFIRM_BETA_MOM / TV_CONFIRM_TAU / TV_CONFIRM_P_BAR`.
2. Verify the calibrated config beats the current gate out-of-sample.
3. Wire `confirm_signal` into [`bot.py`](../../bot.py) `_handle_tradingview_signal` behind
   those env knobs (this is the **only remaining live-path change** — `bot.py` is
   currently untouched). Live z_mom needs a buffer of recent bar closes (from
   `preco_fechamento` or a Binance/Coinbase tap).
4. Validate via `redis_control.py dryrun on` (full live order path, `submit_order`
   skipped) before flipping to live.

## 8. File map

| File | Role |
|------|------|
| [`tv_market_select.py`](../../tv_market_select.py) | Pure math: `confirm_probability`, `fee_breakeven_prob`, `confirm_signal`, `z_momentum` |
| [`backtest/ingest.py`](../../backtest/ingest.py) | `load_closes_from_signals`, `load_bar_closes_csv`, `z_mom_by_window` |
| [`backtest/engine.py`](../../backtest/engine.py) | `run_replay` confirmation params (default off) |
| [`backtest/__main__.py`](../../backtest/__main__.py) | `tune --confirm-side` sweep + `MIN_LIVE_SAMPLE` guard |
| [`test_tradingview_webhook.py`](../../test_tradingview_webhook.py) | Tests for the posterior, fee-breakeven, z_momentum |
| [`backtest/test_backtest.py`](../../backtest/test_backtest.py) | Tests for the z_mom ingest loaders |
| `bot.py` | Live path — **not yet wired** (deferred until n≥200) |
