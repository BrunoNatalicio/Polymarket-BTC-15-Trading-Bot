"""Structural Yes-Bias scan — calibration of EVERY recorded market.

Unlike ``loss_postmortem`` (which is conditioned on the TradingView signals — a
selection-biased ~11-day slice), this sweeps **every recorded market** in a
series, with no signal filter, to answer one question:

    Is the YES/UP side systematically overpriced near resolution (a "Yes Bias",
    making NO/DOWN structurally +EV), or is buying YES vs NO symmetric (just
    favorite-longshot / noise)?

For each resolved market it reads the YES token's mid at a reference instant
(``window_start + ref_seconds``, AT-OR-BEFORE — no lookahead) and confronts it
with the realized outcome. Three lenses:

1. **Calibration** — implied (YES mid) vs realized P(YES win), per price bin.
   ``gap = realized - implied`` < 0 systematically => YES overpriced.
2. **Symmetric EV** — simulate buying YES vs buying NO at that instant (each on
   its own book, concave taker fee), settle to expiry. If buying NO is +EV while
   buying YES is -EV at matched conditions => directional (Yes) bias. This is the
   discriminant that resolves the §2 contradiction in
   ``.context/docs/microstructure-edge-research.md``.
3. **Robustness** — the same cut by session (UTC), a recorded-mid volatility
   tercile, and a temporal half, plus a time-to-expiry sweep of the calibration.

Pure read against the recorded CLOB. Reuses ``ingest`` (markets/snapshots/levels),
``matching.simulate_market_buy`` (concave fee), ``settlement.settle_fill`` and
``loss_postmortem``'s session helper. No new outcome logic.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any, cast

import pandas as pd

import backtest.ingest as ingest
from backtest.loss_postmortem import _session
from backtest.matching import simulate_market_buy
from backtest.settlement import settle_fill

# series -> (window_seconds, slug_prefix, align tolerance s, sweep ref seconds)
# sweep refs span ~25% / 50% / near-close of the window; the last is the primary
# reference used for the EV test and the robustness cuts.
SERIES_SPEC: dict[str, tuple[int, str, float, tuple[int, ...]]] = {
    "5m": (300, "btc-updown-5m-", 30.0, (75, 150, 270)),
    "15m": (900, "btc-updown-15m-", 60.0, (225, 450, 810)),
    "4h": (14400, "btc-updown-4h-", 300.0, (3600, 7200, 14100)),
}

PROB_BINS = [0.0, 0.10, 0.25, 0.40, 0.50, 0.60, 0.75, 0.90, 1.001]
PROB_LABELS = [
    "<.10",
    ".10-.25",
    ".25-.40",
    ".40-.50",
    ".50-.60",
    ".60-.75",
    ".75-.90",
    ">=.90",
]


def _align_ref(meta: pd.DataFrame, refs: pd.DataFrame, tol_s: float) -> pd.DataFrame:
    """For each ref row (token_id, ref_ts) attach the snapshot AT-OR-BEFORE it.

    merge_asof backward by token_id: the book as it stood at the reference
    instant (information available then; the outcome is in the future).
    """
    if refs.empty or meta.empty:
        out = refs.copy()
        out["snapshot_id"] = pd.NA
        out["best_bid_m"] = pd.NA
        out["best_ask_m"] = pd.NA
        return out
    left = refs.copy()
    left["ref_dt"] = pd.to_datetime(left["ref_ts"], unit="s", utc=True)
    left["token_id"] = left["token_id"].astype(str)
    left = left.sort_values("ref_dt")
    right = meta.copy()
    right["ref_dt"] = pd.to_datetime(right["ts"], unit="s", utc=True)
    right["token_id"] = right["token_id"].astype(str)
    right = right.sort_values("ref_dt")
    merged = pd.merge_asof(
        left,
        right[["ref_dt", "token_id", "snapshot_id", "best_bid_m", "best_ask_m"]],
        on="ref_dt",
        by="token_id",
        direction="backward",
        tolerance=cast(pd.Timedelta, pd.Timedelta(seconds=tol_s)),
    )
    return merged


def _mid(bid_m: Any, ask_m: Any) -> float | None:
    """Mid from integer-thousandths bid/ask; None if either side is missing."""
    if pd.isna(bid_m) or pd.isna(ask_m):
        return None
    return (float(bid_m) + float(ask_m)) / 2000.0


def _prob_frame(
    markets: pd.DataFrame, meta_yes: pd.DataFrame, ref_seconds: int, tol_s: float
) -> pd.DataFrame:
    """One row per market: YES mid at ref_seconds + realized YES win + cuts."""
    refs = pd.DataFrame(
        {
            "market_slug": markets["market_slug"],
            "token_id": markets["yes_token_id"].astype(str),
            "ref_ts": markets["window_start"] + ref_seconds,
            "window_start": markets["window_start"],
            "outcome": markets["outcome"],
        }
    )
    aligned = _align_ref(meta_yes, refs, tol_s)
    aligned["mid"] = aligned.apply(
        lambda r: _mid(r["best_bid_m"], r["best_ask_m"]), axis=1
    )
    df = aligned.dropna(subset=["mid"]).copy()
    df["won_yes"] = (df["outcome"] == "YES").astype(int)
    hours = df["window_start"].map(
        lambda w: datetime.fromtimestamp(float(w), tz=UTC).hour
    )
    df["session"] = hours.map(_session)
    return df


def _calib_table(df: pd.DataFrame, title: str) -> None:
    """Implied (YES mid) vs realized P(YES) per price bin."""
    base = df["won_yes"].mean()
    print(f"\n{title}  (P(YES) global {base:.0%}, n={len(df)})")
    print(f"  {'bin p_yes':<10} {'n':>5} {'implied':>8} {'realized':>9} {'gap':>7}")
    df = df.copy()
    df["bin"] = pd.cut(df["mid"], bins=PROB_BINS, labels=PROB_LABELS, right=False)
    for label in PROB_LABELS:
        sub = df[df["bin"] == label]
        if sub.empty:
            continue
        implied = sub["mid"].mean()
        realized = sub["won_yes"].mean()
        print(
            f"  {label:<10} {len(sub):>5} {implied:>8.3f} {realized:>9.3f} "
            f"{realized - implied:>+7.3f}"
        )


def _sweep_table(frames: dict[int, pd.DataFrame], window_seconds: int) -> None:
    """Overall calibration gap at each ref second — does the bias grow near close?"""
    print("\n[sweep] calibração por tempo-até-expiry (o viés cresce perto do fim?)")
    print(
        f"  {'ref':>8} {'%janela':>8} {'n':>5} {'implied':>8} {'realized':>9} {'gap':>7}"
    )
    for ref_s in sorted(frames):
        df = frames[ref_s]
        if df.empty:
            continue
        implied = df["mid"].mean()
        realized = df["won_yes"].mean()
        print(
            f"  {ref_s:>8} {ref_s / window_seconds:>7.0%} {len(df):>5} "
            f"{implied:>8.3f} {realized:>9.3f} {realized - implied:>+7.3f}"
        )


def _ev_frame(
    con: sqlite3.Connection,
    markets: pd.DataFrame,
    prob_df: pd.DataFrame,
    meta_no: pd.DataFrame,
    ref_seconds: int,
    tol_s: float,
    stake_usd: float,
    fee_rate: float,
) -> pd.DataFrame:
    """Per market: simulated PnL of buying YES vs buying NO at the ref instant.

    Each side is filled against its OWN recorded book (concave taker fee) and
    settled to expiry. Markets whose YES or NO book is missing/too thin to fill
    the stake are dropped (can't price the trade honestly).
    """
    no_refs = pd.DataFrame(
        {
            "market_slug": markets["market_slug"],
            "token_id": markets["no_token_id"].astype(str),
            "ref_ts": markets["window_start"] + ref_seconds,
        }
    )
    no_aligned = cast(
        "pd.DataFrame",
        _align_ref(meta_no, no_refs, tol_s)[["market_slug", "snapshot_id"]],
    ).rename(columns={"snapshot_id": "no_snapshot_id"})
    base = cast(
        "pd.DataFrame",
        prob_df[
            [
                "market_slug",
                "snapshot_id",
                "mid",
                "won_yes",
                "outcome",
                "session",
                "window_start",
            ]
        ],
    ).rename(columns={"snapshot_id": "yes_snapshot_id"})
    merged = base.merge(no_aligned, on="market_slug", how="inner")
    merged = merged.dropna(subset=["yes_snapshot_id", "no_snapshot_id"])

    sids: list[int] = []
    for col in ("yes_snapshot_id", "no_snapshot_id"):
        sids.extend(int(s) for s in merged[col].tolist())
    books = ingest.load_levels_for_snapshots(con, sids)

    rows: list[dict[str, Any]] = []
    for r in merged.to_dict("records"):
        yes_asks = books.get(int(r["yes_snapshot_id"]), {}).get("asks", [])
        no_asks = books.get(int(r["no_snapshot_id"]), {}).get("asks", [])
        fy = simulate_market_buy(yes_asks, stake_usd, fee_rate)
        fn = simulate_market_buy(no_asks, stake_usd, fee_rate)
        if fy.exhausted or fn.exhausted:
            continue
        outcome = str(r["outcome"])
        sy = settle_fill("UP", fy.filled_usd, fy.filled_tokens, outcome)
        sn = settle_fill("DOWN", fn.filled_usd, fn.filled_tokens, outcome)
        rows.append(
            {
                "market_slug": r["market_slug"],
                "mid": r["mid"],
                "won_yes": r["won_yes"],
                "session": r["session"],
                "window_start": r["window_start"],
                "yes_filled": fy.filled_usd,
                "yes_pnl": sy["pnl"],
                "no_filled": fn.filled_usd,
                "no_pnl": sn["pnl"],
            }
        )
    return pd.DataFrame(rows)


def _ev_pct(pnl: float, filled: float) -> str:
    return f"{pnl / filled:>+6.1%}" if filled > 0 else "   n/a"


def _ev_table(ev: pd.DataFrame, title: str) -> None:
    """EV of buying YES vs buying NO, per YES-price bin (the discriminant)."""
    print(f"\n{title}  (n={len(ev)})")
    print(f"  {'bin p_yes':<10} {'n':>5} {'EV buy-YES':>11} {'EV buy-NO':>11}")
    ev = ev.copy()
    ev["bin"] = pd.cut(ev["mid"], bins=PROB_BINS, labels=PROB_LABELS, right=False)
    for label in PROB_LABELS:
        sub = ev[ev["bin"] == label]
        if sub.empty:
            continue
        print(
            f"  {label:<10} {len(sub):>5} "
            f"{_ev_pct(sub['yes_pnl'].sum(), sub['yes_filled'].sum()):>11} "
            f"{_ev_pct(sub['no_pnl'].sum(), sub['no_filled'].sum()):>11}"
        )
    print(
        f"  {'TOTAL':<10} {len(ev):>5} "
        f"{_ev_pct(ev['yes_pnl'].sum(), ev['yes_filled'].sum()):>11} "
        f"{_ev_pct(ev['no_pnl'].sum(), ev['no_filled'].sum()):>11}"
    )


def _cut_table(ev: pd.DataFrame, key: str, title: str) -> None:
    """P(YES), implied gap and EV(YES/NO) per bucket of a robustness cut."""
    print(f"\n{title}")
    print(
        f"  {'bucket':<12} {'n':>5} {'P(YES)':>7} {'gap':>7} {'EV-YES':>8} {'EV-NO':>8}"
    )
    for bucket, sub in ev.groupby(key, observed=True):
        n = len(sub)
        realized = sub["won_yes"].mean()
        gap = realized - sub["mid"].mean()
        print(
            f"  {str(bucket):<12} {n:>5} {realized:>7.0%} {gap:>+7.3f} "
            f"{_ev_pct(sub['yes_pnl'].sum(), sub['yes_filled'].sum()):>8} "
            f"{_ev_pct(sub['no_pnl'].sum(), sub['no_filled'].sum()):>8}"
        )


def _vol_tercile(meta_yes: pd.DataFrame, markets: pd.DataFrame) -> dict[str, str]:
    """market_slug -> 'vol-baixa|media|alta' from the recorded YES-mid range.

    A dependency-free volatility proxy: the peak-to-trough range of the YES mid
    over the window (more BTC movement => the prob swings more). Terciles over
    the markets actually present.
    """
    m = meta_yes.dropna(subset=["best_bid_m", "best_ask_m"]).copy()
    if m.empty:
        return {}
    m["mid"] = (m["best_bid_m"].astype(float) + m["best_ask_m"].astype(float)) / 2000.0
    rng = m.groupby("token_id", observed=True)["mid"].agg(lambda s: s.max() - s.min())
    tok2slug = dict(
        zip(markets["yes_token_id"].astype(str), markets["market_slug"], strict=False)
    )
    rng.index = rng.index.astype(str)
    labels = cast(
        "pd.Series",
        pd.qcut(
            rng, 3, labels=["vol-baixa", "vol-media", "vol-alta"], duplicates="drop"
        ),
    )
    out: dict[str, str] = {}
    for tok, lbl in labels.items():
        slug = tok2slug.get(str(tok))
        if slug is not None:
            out[slug] = str(lbl)
    return out


def run_bias_scan(
    con: sqlite3.Connection,
    start_ts: float,
    end_ts: float,
    series: str = "15m",
    fee_rate: float = 0.07,
    stake_usd: float = 3.0,
    ref_seconds: int | None = None,
    sweep: bool = True,
) -> None:
    if series not in SERIES_SPEC:
        print(f"Série desconhecida: {series} (use {list(SERIES_SPEC)})")
        return
    window_seconds, prefix, tol_s, sweep_refs = SERIES_SPEC[series]
    primary_ref = ref_seconds if ref_seconds is not None else sweep_refs[-1]
    refs_to_run = tuple(sorted({*sweep_refs, primary_ref})) if sweep else (primary_ref,)

    all_markets = ingest.load_markets(con)
    markets = cast(
        "pd.DataFrame",
        all_markets[
            all_markets["market_slug"].str.startswith(prefix)
            & all_markets["outcome"].notna()
            & (all_markets["window_start"] >= start_ts)
            & (all_markets["window_start"] < end_ts)
        ].copy(),
    )

    line = "=" * 72
    print(line)
    print(f"YES-BIAS SCAN — estrutural (todo mercado gravado), série {series}")
    print(line)
    print(
        f"Período : {datetime.fromtimestamp(start_ts, tz=UTC):%Y-%m-%d %H:%M} -> "
        f"{datetime.fromtimestamp(end_ts, tz=UTC):%Y-%m-%d %H:%M} UTC | "
        f"fee={fee_rate:.2f} | stake=${stake_usd:.2f} | ref~{primary_ref}s "
        f"({primary_ref / window_seconds:.0%} da janela)"
    )
    if markets.empty:
        print("Nenhum mercado resolvido nessa janela/série — nada a analisar.")
        print(line)
        return
    print(
        f"Universo: {len(markets)} mercados resolvidos | "
        f"P(YES) bruto {(markets['outcome'] == 'YES').mean():.1%}"
    )

    yes_ids = markets["yes_token_id"].astype(str).tolist()
    no_ids = markets["no_token_id"].astype(str).tolist()
    lo = float(markets["window_start"].min()) - 1
    hi = float(markets["window_end"].max()) + 60
    meta_yes = ingest.load_snapshot_meta(con, yes_ids, lo, hi)
    meta_no = ingest.load_snapshot_meta(con, no_ids, lo, hi)

    # --- calibration at each ref + time-to-expiry sweep --------------------------
    frames: dict[int, pd.DataFrame] = {
        ref_s: _prob_frame(markets, meta_yes, ref_s, tol_s) for ref_s in refs_to_run
    }
    primary = frames[primary_ref]
    if primary.empty:
        print("Sem snapshots YES no instante de referência — nada a calibrar.")
        print(line)
        return
    _calib_table(primary, f"[1] Calibração @ ref {primary_ref}s")
    if sweep and len(frames) > 1:
        _sweep_table(frames, window_seconds)

    # --- symmetric EV (the discriminant) + robustness cuts -----------------------
    ev = _ev_frame(
        con, markets, primary, meta_no, primary_ref, tol_s, stake_usd, fee_rate
    )
    if ev.empty:
        print("\nSem book preenchível nos dois lados — EV simétrico indisponível.")
        print(line)
        return
    _ev_table(ev, "[2] EV simétrico: comprar YES vs comprar NO (líq. fee, por $)")

    _cut_table(ev, "session", "[3] Por sessão (UTC)")

    vol_map = _vol_tercile(meta_yes, markets)
    ev["vol"] = ev["market_slug"].map(lambda s: vol_map.get(str(s), "?"))
    _cut_table(ev, "vol", "[4] Por volatilidade (range do mid YES, tercil)")

    median_ws = ev["window_start"].median()
    ev["bloco"] = ev["window_start"].map(
        lambda w: "1a-metade" if w < median_ws else "2a-metade"
    )
    _cut_table(ev, "bloco", "[5] Por bloco temporal (estabilidade)")
    print(line)
