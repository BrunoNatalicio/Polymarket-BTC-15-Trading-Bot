"""Parity gate for the local Guppy signal (TRK-001 T3) — the go/no-go check.

A TradingView chart-data CSV export carries OHLCV **plus** the indicator's own
plot columns: ``Bot Sinal UP (YES)`` / ``Bot Sinal DOWN (NO)`` (the Pine
``sinalUP``/``sinalDOWN``) and the intermediate ``Plot`` (RSINorm) and EMA
columns. Running ``local_signal.guppy`` over the SAME OHLCV and diffing against
those columns is exact ALGORITHM parity on identical candles — feed-agnostic
(live uses Binance; this proves the math reproduces the Pine indicator).

Compared only AFTER our warmup (``min_warmup``) completes: TradingView computes
from the full chart history, so the first bars of the export are "more informed"
than ours until both RSI/EMA have converged.

Run:
    uv run python -m backtest guppy-parity --csv ".context/docs/COINBASE_BTCUSD, 15.csv"
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from local_signal.guppy import (
    DEFAULT_PARAMS,
    Candle,
    GuppyParams,
    ema,
    guppy_signal_series,
    wilder_rsi,
)

# Column-name candidates in a TradingView export (robust to accents/encoding).
_UP_CANDIDATES = ("Bot Sinal UP (YES)", "sinalUP_export", "sinalUP")
_DOWN_CANDIDATES = ("Bot Sinal DOWN (NO)", "sinalDOWN_export", "sinalDOWN")


@dataclass
class ParityReport:
    csv_path: str
    n_bars: int
    n_eval: int  # bars compared (post-warmup)
    tv_up: int
    tv_down: int
    local_up: int
    local_down: int
    agree_up: int
    agree_down: int
    agree_none: int
    local_extra: int  # local fired, TV did not (false positive)
    tv_extra: int  # TV fired, local did not (false negative)
    conflict: int  # both fired, opposite direction
    rsi_max_abs_err: float
    ma1_max_abs_err: float
    ma5_max_abs_err: float
    mismatches: list[tuple[int, str, str]] = field(default_factory=list)

    @property
    def matched(self) -> int:
        return self.agree_up + self.agree_down

    @property
    def tv_fired(self) -> int:
        return self.tv_up + self.tv_down

    @property
    def fired_match_rate(self) -> float:
        """Of the bars where TV fired, the fraction local reproduces exactly."""
        return self.matched / self.tv_fired if self.tv_fired else 1.0

    @property
    def overall_agree_rate(self) -> float:
        return (
            (self.agree_up + self.agree_down + self.agree_none) / self.n_eval
            if self.n_eval
            else 1.0
        )


def _find_col(columns: Sequence[str], candidates: Sequence[str]) -> str:
    cols = list(columns)
    for cand in candidates:
        if cand in cols:
            return cand
    # Fuzzy: case-insensitive contains on the distinctive token.
    for cand in candidates:
        key = cand.lower().split("(")[0].strip()
        for c in cols:
            if key and key in c.lower():
                return c
    raise ValueError(f"none of {candidates!r} found in columns {cols!r}")


def _candles_from_df(df: pd.DataFrame) -> list[Candle]:
    o = df["open"].astype(float).tolist()
    h = df["high"].astype(float).tolist()
    lo = df["low"].astype(float).tolist()
    c = df["close"].astype(float).tolist()
    v = df["Volume"].astype(float).tolist()
    return [
        {
            "open": o[i],
            "high": h[i],
            "low": lo[i],
            "close": c[i],
            "volume": v[i],
            "is_closed": True,
        }
        for i in range(len(c))
    ]


def run_parity(
    csv_path: str,
    params: GuppyParams = DEFAULT_PARAMS,
    up_col: str | None = None,
    down_col: str | None = None,
    max_mismatches: int = 25,
) -> ParityReport:
    df = pd.read_csv(csv_path)
    cols = list(df.columns)
    up_col = up_col or _find_col(cols, _UP_CANDIDATES)
    down_col = down_col or _find_col(cols, _DOWN_CANDIDATES)

    candles = _candles_from_df(df)
    n = len(candles)
    series = guppy_signal_series(candles, params)

    tv_up_flags = (df[up_col].fillna(0).astype(float) > 0).tolist()
    tv_down_flags = (df[down_col].fillna(0).astype(float) > 0).tolist()

    # Intermediate-value diff vs TV's own exported RSINorm / ma1 / ma5 columns.
    closes = [c["close"] for c in candles]
    rsi = wilder_rsi(closes, params.rsi_len)
    ma1 = ema(rsi, params.fast)
    ma5 = ema(rsi, params.slow)
    rsi_err = ma1_err = ma5_err = 0.0
    rsi_col = _maybe_col(cols, ("Plot.1",))
    ma1_col = _maybe_col(cols, ("EMA Gatilho 3",))
    ma5_col = _maybe_col(cols, ("EMA Refer", "21"))
    # Pre-extract TV's exported indicator columns once (scalar .iloc in a loop is
    # slow); None when the export lacks them.
    tv_rsi = df[rsi_col].astype(float).tolist() if rsi_col else None
    tv_ma1 = df[ma1_col].astype(float).tolist() if ma1_col else None
    tv_ma5 = df[ma5_col].astype(float).tolist() if ma5_col else None

    counts = dict(
        tv_up=0,
        tv_down=0,
        local_up=0,
        local_down=0,
        agree_up=0,
        agree_down=0,
        agree_none=0,
        local_extra=0,
        tv_extra=0,
        conflict=0,
    )
    mismatches: list[tuple[int, str, str]] = []
    n_eval = 0
    for i in range(params.min_warmup, n):
        tv = "UP" if tv_up_flags[i] else "DOWN" if tv_down_flags[i] else None
        local = series[i]
        n_eval += 1

        # Intermediate-value parity, every evaluated bar (not just mismatches).
        if tv_rsi is not None:
            rsi_err = max(rsi_err, abs(rsi[i] - tv_rsi[i]))
        if tv_ma1 is not None:
            ma1_err = max(ma1_err, abs(ma1[i] - tv_ma1[i]))
        if tv_ma5 is not None:
            ma5_err = max(ma5_err, abs(ma5[i] - tv_ma5[i]))
        if tv == "UP":
            counts["tv_up"] += 1
        elif tv == "DOWN":
            counts["tv_down"] += 1
        if local == "UP":
            counts["local_up"] += 1
        elif local == "DOWN":
            counts["local_down"] += 1

        if local == tv:
            if tv == "UP":
                counts["agree_up"] += 1
            elif tv == "DOWN":
                counts["agree_down"] += 1
            else:
                counts["agree_none"] += 1
            continue
        # disagreement
        if local is not None and tv is not None:
            counts["conflict"] += 1
        elif local is not None:
            counts["local_extra"] += 1
        else:
            counts["tv_extra"] += 1
        if len(mismatches) < max_mismatches:
            mismatches.append((i, str(local), str(tv)))

    return ParityReport(
        csv_path=csv_path,
        n_bars=n,
        n_eval=n_eval,
        rsi_max_abs_err=rsi_err,
        ma1_max_abs_err=ma1_err,
        ma5_max_abs_err=ma5_err,
        mismatches=mismatches,
        **counts,
    )


def _maybe_col(columns: Sequence[str], tokens: Sequence[str]) -> str | None:
    """First column whose lowercased name contains all `tokens` (None if absent)."""
    for c in columns:
        if all(t.lower() in c.lower() for t in tokens):
            return c
    return None
