"""Hit-rate histórico do setup a partir de um CSV export do TradingView.

Métrica: sinal avaliado no fechamento da barra N -> aposta na direção da
barra N+1 (a janela de 15 min que o mercado Polymarket cobre em seguida).
Hit = barra N+1 fechou na direção do sinal. Empate (close == open) conta
como acerto apenas para DOWN (convenção do settlement: close > open => UP).

Uso: uv run python backtest/csv_hitrate.py "<caminho do csv>" "<col UP>" "<col DOWN>"
"""

import os
import sys
from typing import cast

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    csv_path, up_col, down_col = sys.argv[1], sys.argv[2], sys.argv[3]
    df = pd.read_csv(csv_path)
    df["next_up"] = (df["close"] > df["open"]).shift(-1)
    df["entry_window_ok"] = df["close"].shift(-1).notna()

    rows = []
    for direction, col in (("UP", up_col), ("DOWN", down_col)):
        sig = df[(df[col].fillna(0) > 0) & df["entry_window_ok"]]
        next_up = cast("pd.Series", sig["next_up"]).astype("boolean")
        if direction == "UP":
            hits = int(next_up.fillna(False).sum())
        else:
            hits = int((~next_up.fillna(True)).sum())
        total = len(sig)
        rows.append((direction, total, hits))

    print("=" * 56)
    print("HIT RATE HISTORICO DO SETUP (proxima vela de 15m)")
    print("=" * 56)
    t0 = pd.to_datetime(df["time"].iloc[0], unit="s", utc=True)
    t1 = pd.to_datetime(df["time"].iloc[-1], unit="s", utc=True)
    print(f"Periodo : {t0:%Y-%m-%d %H:%M} -> {t1:%Y-%m-%d %H:%M} UTC")
    print(f"Barras  : {len(df)} (15m, {(len(df) / 96):.1f} dias)")
    print("-" * 56)
    tot_sig, tot_hit = 0, 0
    for direction, total, hits in rows:
        rate = hits / total if total else float("nan")
        print(f"{direction:5}: {total:4} sinais | {hits:4} acertos | {rate:6.1%}")
        tot_sig += total
        tot_hit += hits
    overall = tot_hit / tot_sig if tot_sig else float("nan")
    print(f"GERAL: {tot_sig:4} sinais | {tot_hit:4} acertos | {overall:6.1%}")
    print("-" * 56)
    for entry in (0.50, 0.55, 0.60, 0.65):
        be = entry  # binario $1: breakeven = preco de entrada
        verdict = "LUCRATIVO" if overall > be else "nao cobre"
        print(f"entrada media ${entry:.2f} -> breakeven {be:.0%}: {verdict}")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    sys.exit(main())
