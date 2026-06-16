---
type: doc
name: fusion-strategy
description: How the default "fusion" strategy actually trades — a late-window favorite-follower gated by signal activity, not a directional 6-signal vote. Decision flow, processors, operating, and backtesting.
category: strategy
generated: 2026-06-16
status: filled
scaffoldVersion: "2.0.0"
---

## Fusion Strategy — What It Actually Does

`fusion` is the **default** strategy (`btc_trading:active_strategy`, the alternative being
[`tradingview`](tradingview-runbook.md) — the two are mutually exclusive). It is the path
`_make_trading_decision` takes in [bot.py](../../bot.py) when `active_strategy != "tradingview"`.

**The headline, because the name is misleading:** despite the "fusion of 6 signals" framing, the
deployed strategy does **not** trade in the direction the fused signal points. It is a
**late-window favorite-follower**:

> At minute 13–14 of each 15-minute market, if the signal processors show *any* activity, buy
> whichever side the Polymarket price already favors (YES if the UP probability is > 0.60, NO if
> < 0.40), skip the coin-flip middle, and hold to settlement.

The 6 processors and their weighted vote are computed every cycle, but their **direction is
discarded** — they serve only as an *activity gate* ("is there enough going on to bother?"). What
sets the trade direction is the Polymarket price itself. Keep that distinction front of mind; the
rest of this doc is just the detail behind it.

## 1. Decision Flow (the real mechanics, in order)

All of this lives in `_make_trading_decision` and its trigger in [bot.py](../../bot.py).

1. **Time gate — minutes 13–14 only.** A trade is considered only when
   `780 <= seconds_into_sub_interval < 840` of the current 15-minute market, and at most **once per
   market** (dedup via `trade_key = (market_start_ts, sub_interval)` vs `last_trade_time`). The
   rationale (§3): by minute 13 the price *is* the market's near-resolved verdict, so the bot reads
   it rather than predicting.
2. **Activity gate — "did any processor fire?"** It runs the processors, then
   `fused = fusion_engine.fuse_signals(signals, min_signals=1, min_score=40.0)`; if `fused` is
   `None` it returns (no trade). Because the fusion `score` is a *consensus* ratio
   (`dominant / total × 100`, always ≥ 50 when at least one signal exists), `min_score=40`
   effectively means **"trade only if ≥ 1 processor produced a signal this cycle."**
   - Note: `FusedSignal.is_actionable` (`score ≥ 60 and confidence ≥ 0.6`) and `is_strong`
     (`score ≥ 70`) exist in [signal_fusion.py](../../core/strategy_brain/fusion_engine/signal_fusion.py)
     but are **not** what gates the trade here — the deployed path checks only that `fused` is
     non-`None`.
3. **Direction — the TREND FILTER (follows the price, ignores the vote).**
   With `price` = the Polymarket YES (UP) mid:
   - `price > 0.60` → **buy YES** (long)
   - `price < 0.40` → **buy NO** (short)
   - `0.40 ≤ price ≤ 0.60` → **SKIP** (coin-flip territory — this is where the bot used to lose)

   The fused signal's direction is explicitly overridden here ("we ignore what the signal
   processors say and simply follow the price").
4. **Sizing & guards.** Position size is the fixed `POSITION_SIZE_USD` (from `MARKET_BUY_USD`).
   `risk_engine.validate_new_position` enforces position-count/exposure limits (no sizing math). A
   liquidity guard skips the trade if the relevant side's price ≤ $0.02 (essentially empty book).
5. **Execute.** Simulation → paper trade; live → a **taker market order** on Polymarket (pays the
   15m crypto taker fee — see [backtest-validation.md](backtest-validation.md)). One position, held
   to settlement.

## 2. The Processors & Fusion (the activity gate)

All processors live in `core/strategy_brain/signal_processors/`, extend `BaseSignalProcessor`, and
return a `TradingSignal` (`direction`, `confidence`, `strength`). They are fused by
[`SignalFusionEngine`](../../core/strategy_brain/fusion_engine/signal_fusion.py).

**Runtime weights** (set in `on_start`, overriding the engine's constructor defaults of
Spike 0.40 / Divergence 0.30 / Sentiment 0.20):

| Processor | Weight | What it reads |
| --- | --- | --- |
| `OrderBookImbalance` | 0.30 | bid/ask depth imbalance on the Polymarket book |
| `TickVelocity` | 0.25 | trade-arrival-rate anomalies (fast Polymarket momentum) |
| `PriceDivergence` | 0.18 | Polymarket UP probability vs BTC spot momentum (Coinbase) |
| `SpikeDetection` | 0.12 | mean-reversion + velocity on the **Polymarket UP-probability** series |
| `DeribitPCR` | 0.10 | Deribit put/call ratio |
| `SentimentAnalysis` | 0.05 | Fear & Greed + social (daily; weak) |

Two corrections to older descriptions, because they matter for understanding the strategy:
- **`SpikeDetection` reads the Polymarket UP-probability series, not "Binance/Coinbase divergence."**
  Its thresholds (5% MA deviation, 3% velocity over 3 ticks) are calibrated for 0–1 probability
  prices. Spike up → fade BEARISH; spike down → fade BULLISH; short velocity bursts →
  momentum-continuation.
- **`PriceDivergence`** is the only processor that consumes BTC spot, and only as a *secondary*
  input: its primary signals are an extreme-probability fade (poly prob > 0.68 → DOWN, < 0.32 → UP)
  and a momentum-mispricing (poly prob 0.35–0.65 + a real BTC spot move); with no spot price it
  falls back to Polymarket-price momentum.

How the score works: `consensus_score = dominant_contribution / total_contribution × 100`, where
each signal contributes `weight × confidence × (strength/4)` to its direction's bucket. So the
score measures **agreement among whichever processors fired**, weighted — not the magnitude of an
edge. With few signals it is trivially ≥ 50, which is why the `min_score=40` activity gate almost
always passes when anything fires.

**Learning loop.** `LearningEngine` ([feedback/learning_engine.py](../../feedback/learning_engine.py))
adjusts these weights from closed-trade outcomes. But since the weights only affect the *activity
gate* (not the trade direction, which follows the price), learning tunes *which markets clear the
gate*, not which way the bot bets.

## 3. Why Late-Window Favorite-Following

From the design comments in [bot.py](../../bot.py):

- **Why minute 13:** at ~13 minutes into a 15-minute market the UP/DOWN result is nearly decided —
  if YES trades at $0.78, BTC has gone up this interval. The bot reads a nearly-resolved outcome
  instead of predicting one.
- **Why not earlier:** at 30 seconds in, nobody knows the direction; signals near $0.50 have no
  edge — that's where the bot historically lost.
- **Share-count intuition** (shares = stake / price): 1.4 shares = price $0.71 → strong trend,
  ~71% win rate; 1.9 shares = $0.53 → near coin flip; 2.0+ shares = $0.50 → pure coin flip → SKIP.
  The 0.40–0.60 deadband is precisely the "don't bet on a coin flip" rule.

The trade-off is baked in: the strategy wins often (it buys resolved favorites) but at skewed
prices ($0.65–0.80+), so each win pays little and the taker fee plus the occasional late reversal
are what decide net edge.

## 4. Fusion vs TradingView

| | **Fusion** | **[TradingView](tradingview-runbook.md)** |
| --- | --- | --- |
| Direction decided by | the Polymarket price (favorite) | your TradingView indicator |
| Entry timing | minute 13 of the **current** window | bar close → the freshly-opened **N+1** window |
| Typical entry price | $0.65–0.80 (a resolved favorite) | ~$0.50 (a just-opened market) |
| Role of the 6 signals | activity gate only | not used (bypassed) |
| Skips | coin-flip prices (0.40–0.60) | stale signals (TTL), already-traded markets |
| Taker fee impact | smaller (skewed prices) | largest near $0.50 |

Both are mutually exclusive (`btc_trading:active_strategy`), both bet `MARKET_BUY_USD` capped by the
risk engine, both are taker market orders held to settlement.

## 5. Operating the Fusion Strategy

- **Activate** (it is the default): `uv run python redis_control.py strategy fusion`.
- **Status**: `uv run python redis_control.py status` → `Strategy: FUSION`.
- **Sim/live**: same control plane as TradingView — `redis_control.py sim|live`; `--test-mode`
  paper-trades. Fusion is unaffected by the TradingView `dryrun` flag.
- **What to expect in the logs** (per market, around minute 13):
  - `LATE-WINDOW TRADE: …` (the time gate fired)
  - `FUSED SIGNAL: … (score=…, confidence=…)` (the activity gate passed)
  - `TREND: UP …` / `TREND: DOWN …` (a trade) or `TREND: NEUTRAL … SKIPPING` (price in the deadband)
  - `Risk engine blocked trade` / `No liquidity for BUY/SELL` are normal protective skips.
- **Funds**: the risk engine allows up to 5 concurrent `MARKET_BUY_USD` positions (5× exposure cap).

## 6. Backtesting the Fusion Strategy

The existing replay engine ([backtest-validation.md](backtest-validation.md)) is signal-driven and
covers TradingView. The fusion backtest is its own command, **`fusion-replay`** (the **L0 baseline**
of the brain ladder in §7), reusing the same recorded books, matching, and CLOB settlement:

```bash
uv run python -m backtest fusion-replay --series 15m --stake 3 --fee-rate 0.07
```

It reconstructs each recorded market's UP-probability from the snapshot nearest `window_start +
--entry-second` (default 810s, ±`--entry-tolerance`), applies the TREND FILTER (follow favorite,
`--trend-up`/`--trend-down` deadband) for direction, prices the bought token against its own recorded
asks (taker fee), settles via `markets.outcome`, and reports win-rate + PnL **with and without the
fee**, split UP/DOWN. Implemented in [backtest/fusion_replay.py](../../backtest/fusion_replay.py)
(`run_fusion_replay`), tested in [backtest/test_backtest.py](../../backtest/test_backtest.py).

**Honest caveats:**
- Because direction = "follow the favorite," this answers *"does the late favorite still pay after
  the taker fee?"* — and on the recorded sample so far, **yes** (net positive).
- **Selection effect:** when the favorite is near-certain (~$0.97+) there are often no asks to buy
  (nobody sells the sure winner), so those markets don't fill — exactly as live. The fills skew to
  the *less extreme* favorites.
- L0 omits the processor **activity gate** and the calibration brain (§7); those are the next rungs,
  and any live use must first clear CPCV out-of-sample validation, not just this in-sample read.

## 7. Planned Evolution — a Self-Learning Calibration Brain

> Status: **design only, not implemented.** Output of a multi-agent debate (4 agents + prior-art
> research). It adds *intelligence without changing the strategy*: the direction stays "follow the
> favorite"; the brain only decides **whether** a given favorite is worth trading and **how much**.

The prior art is decisive: prediction markets show a **favorite-longshot bias** — high-price
contracts (>$0.50) earn a small *positive* return, longshots bleed. The edge, if any, is in
**calibrating** the minute-13 price into a true win-probability and only trading when it clears the
fee. So the brain is a **gate + sizer**, plugged into the same seam the engine already exposes for
TradingView (`min_entry_prob` / `confirm_signal` in [tv_market_select.py](../../tv_market_select.py)).

**The ladder (each rung ships only if it beats the rung below — and the fee — out-of-sample):**

| Rung | Adds | Go/no-go |
| --- | --- | --- |
| **L0** baseline | the current "always-trade-the-favorite @min-13" | reference |
| **L1** EV gate | a calibrator `P(win\|price@13)` (**Platt** while data is scarce, **isotonic** once it grows) → trade only if `P_cal > breakeven(price, fee)` | beats L0 net of fee |
| **L2** sizing | **fractional-Kelly** stake, always within the `MARKET_BUY_USD` cap (scales *down* only) | beats L1 on PnL/risk |
| **L3** microstructure | one leakage-safe feature at a time — order-flow imbalance (OFI), late-window prob drift, prob×BTC-spot divergence | each feature must beat the prior rung |

**"Self-learning", safely.** Not online tick-by-tick (that overfits a small, non-stationary sample
and can self-destruct). Instead: **scheduled offline refit** + a **drift monitor** (ADWIN / PSI /
KS) that triggers retraining, a **minimum-sample guard** (the repo already uses
`MIN_LIVE_SAMPLE=200`), live **calibration monitoring**, and a **kill-switch** that falls back to L0
(raw favorite) if calibration drifts.

**Validation discipline.** Walk-forward tests a single path and overfits easily; use **Combinatorial
Purged Cross-Validation** (purged + embargoed, López de Prado). A rung is armed live only after it
beats the L0 baseline *and* the fee under CPCV, with ≥100 win / 100 loss settled events and a clean
reliability diagram.

**Open empirical question (gates everything above):** *does the late favorite pay after the taker
fee?* That is exactly what the L0 `fusion-replay` (§6) measures — so it is built first; no rung is
worth coding until L0 shows a real, fee-surviving edge to refine.

References: favorite-longshot bias and prediction-market calibration; Platt vs isotonic scaling;
Kelly / fractional-Kelly sizing; OFI→price (Cont/Kukanov/Stoikov); CPCV (López de Prado); concept-
drift detection (ADWIN/PSI).

## 8. Invariants & Gotchas

- **The vote does not set direction.** `is_actionable`/`is_strong`/the weighted direction exist but
  the deployed path gates on `fused` being produced (`min_score=40`) and then **follows the price**.
  Don't "fix" the code to trade the fused direction without re-validating — that is a different
  strategy.
- **Weights affect only the gate.** Re-tuning fusion weights (or the learning loop) changes which
  markets clear the activity gate, not the bet direction.
- **Deadband is load-bearing.** Trades only fire outside 0.40–0.60; the near-$0.50 skip is the core
  loss-avoidance rule.
- **Position cap = `MARKET_BUY_USD`**, enforced by the risk engine on every path.
- **Mutually exclusive with TradingView** via `btc_trading:active_strategy`.

## Related Resources

- [tradingview-runbook.md](tradingview-runbook.md) — the alternative strategy
- [architecture.md](architecture.md) — system boundaries and the strategy fork
- [backtest-validation.md](backtest-validation.md) — CLOB settlement, fee model, report format
- [data-flow.md](data-flow.md) — signal pipeline data flow
- [../../bot.py](../../bot.py) — `_make_trading_decision` and the trade-window trigger
