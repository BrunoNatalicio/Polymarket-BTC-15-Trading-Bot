"""Proxy intrabar: sinais do CSV de 5m contra a janela de 15m em que caem.

Aproxima a config real ("Once Per Bar" intrabar): um sinal no fechamento de
uma vela de 5m entra no mercado de 15m em andamento. Hit = a janela de 15m
que contém o momento do sinal fechou na direção do sinal (close > open da
janela => UP). Sinais no fechamento exato da janela entram na seguinte.
Também quebra por posição do sinal dentro da janela (1º/2º/3º terço) — só
o 1º e 2º terços são operáveis na prática (no 3º, faltam <5 min e o preço
do token já convergiu).

Uso: uv run python backtest/csv_hitrate_5m.py "<csv 5m>" "<col UP>" "<col DOWN>"
"""

import sys
from typing import cast

import pandas as pd

W = 900


def main() -> int:
    csv_path, up_col, down_col = sys.argv[1], sys.argv[2], sys.argv[3]
    df = pd.read_csv(csv_path)

    # Janela de 15m de cada barra de 5m e agregado open/close por janela
    df["ws"] = (df["time"] // W) * W
    win = cast(
        "pd.DataFrame",
        df.groupby("ws").agg(w_open=("open", "first"), w_close=("close", "last")),
    )
    win["w_up"] = win["w_close"] > win["w_open"]
    # janelas incompletas (menos de 3 barras de 5m) ficam fora
    complete = df.groupby("ws").size()
    win = win[complete == 3]

    print("=" * 60)
    print("PROXY INTRABAR: sinal 5m -> janela de 15m corrente")
    print("=" * 60)
    t0 = pd.to_datetime(df["time"].iloc[0], unit="s", utc=True)
    t1 = pd.to_datetime(df["time"].iloc[-1], unit="s", utc=True)
    print(f"Periodo : {t0:%Y-%m-%d %H:%M} -> {t1:%Y-%m-%d %H:%M} UTC")
    print(f"Barras 5m: {len(df)} | janelas 15m completas: {len(win)}")

    for direction, col in (("UP", up_col), ("DOWN", down_col)):
        sig = df[df[col].fillna(0) > 0].copy()
        # momento do sinal = fechamento da barra de 5m
        sig["sig_ts"] = sig["time"] + 300
        sig["target_ws"] = (sig["sig_ts"] // W) * W
        # posição dentro da janela: 1 = sinal no 1º terço (faltam 10 min)...
        sig["slice"] = cast(
            "pd.Series", (sig["sig_ts"] - sig["target_ws"]) // 300 + 1
        ).where(sig["sig_ts"] % W != 0, 1)
        sig = sig.join(win["w_up"], on="target_ws", how="inner")
        sig["hit"] = sig["w_up"] if direction == "UP" else ~sig["w_up"]

        print("-" * 60)
        total, hits = len(sig), int(sig["hit"].sum())
        rate = hits / total if total else float("nan")
        print(f"{direction}: {total} sinais | {hits} acertos | {rate:.1%}")
        for sl, label in (
            (1, "1o terco (faltam 10-15min)"),
            (2, "2o terco (faltam 5-10min)"),
            (3, "3o terco (faltam <5min)"),
        ):
            sub = sig[sig["slice"] == sl]
            if len(sub):
                r = sub["hit"].mean()
                print(f"   {label}: {len(sub):4} sinais | {r:6.1%}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
