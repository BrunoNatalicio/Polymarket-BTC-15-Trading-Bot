"""Avalia os trades que o bot REALMENTE executou contra a CLOB gravada.

Distinto do replay (que mede o sinal mapeado para a janela N+1 — a intenção da
estratégia): este módulo lê o que o bot comprou de fato
(``tv_dry_run_trades.json``) e resolve cada trade pelo orderbook CLOB do mercado
que ele negociou. Também mede a conversão sinal->trade, expondo sinais recebidos
que o bot dropou (ex.: o caminho DOWN que falha quando o instrumento NO não está
carregado).
"""

import datetime
import json
import os
import sqlite3
from typing import Any

from backtest.settlement import clob_outcome

DEFAULT_BOT_TRADES_PATH = "tv_dry_run_trades.json"
WINDOW_SECONDS = 900
# Tolerância para casar o timestamp do sinal (DB) com o do trade (JSON).
MATCH_TOLERANCE_S = 5.0


def load_bot_trades(path: str = DEFAULT_BOT_TRADES_PATH) -> list[dict[str, Any]]:
    """Carrega os trades dry-run gravados pelo bot. Lista vazia se não existir."""
    if not os.path.exists(path):
        return []
    with open(path) as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _window_start_from_slug(slug: str) -> int | None:
    """`btc-updown-15m-1781308800` -> 1781308800."""
    try:
        return int(slug.rsplit("-", 1)[1])
    except (IndexError, ValueError):
        return None


def evaluate_bot_trades(
    con: sqlite3.Connection,
    trades: list[dict[str, Any]],
    fee_rate: float = 0.0,
) -> dict[str, Any]:
    """Resolve cada trade do bot via CLOB e agrega WIN/LOSS + PnL.

    PnL segue o modelo de ``settlement.settle_fill``: comprar ``usd`` ao preço
    ``price`` rende ``usd/price`` tokens; cada token vencedor paga $1.

    Taker fee (15m/5m crypto): ``fee = C × fee_rate × p × (1−p)``, cobrado em
    SHARES na compra — derruba o payout da vitória (menos shares) e não afeta a
    derrota (perde-se o ``usd`` cheio). ``fee_rate=0.0`` desliga (mercados sem
    fee). O preço logado pelo bot é o avg pago, usado como ``p``.
    """
    wins = losses = unresolved = 0
    pnl = staked = 0.0
    rows: list[dict[str, Any]] = []
    for t in trades:
        slug = str(t.get("market_slug", ""))
        ws = _window_start_from_slug(slug)
        side = "YES" if "UP" in str(t.get("trade_label", "")) else "NO"
        price = float(t.get("price", 0) or 0)
        usd = float(t.get("usd_amount", 0) or 0)
        outcome = (
            clob_outcome(con, slug, ws + WINDOW_SECONDS) if ws is not None else None
        )
        if outcome is None or price <= 0:
            unresolved += 1
            result: str = "UNRESOLVED"
            row_pnl: float | None = None
        else:
            won = side == outcome
            tokens = usd / price
            # Taker fee skimmed in shares: net = gross − fee_usd/price.
            fee_usd = tokens * fee_rate * price * (1.0 - price)
            net_tokens = tokens - (fee_usd / price if price > 0 else 0.0)
            payout = net_tokens if won else 0.0
            row_pnl = payout - usd
            pnl += row_pnl
            staked += usd
            wins += 1 if won else 0
            losses += 0 if won else 1
            result = "WIN" if won else "LOSS"
        rows.append(
            {
                "timestamp": t.get("timestamp"),
                "trade_label": t.get("trade_label"),
                "price": price,
                "market_slug": slug,
                "outcome": outcome,
                "result": result,
                "pnl": row_pnl,
            }
        )
    settled = wins + losses
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "unresolved": unresolved,
        "settled": settled,
        "win_rate": (wins / settled) if settled else None,
        "total_pnl": pnl,
        "total_staked": staked,
        "rows": rows,
    }


def conversion_stats(
    con: sqlite3.Connection,
    start_ts: float,
    end_ts: float,
    source_like: str = "tradingview",
    trades: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compara sinais recebidos (DB) com trades executados (JSON).

    Casa cada sinal a um trade por proximidade de timestamp; sinais sem trade
    correspondente são ``dropped`` (recebidos mas não negociados pelo bot).
    """
    if trades is None:
        trades = load_bot_trades()
    trade_ts: list[float] = []
    for t in trades:
        raw = t.get("timestamp")
        if not raw:
            continue
        try:
            trade_ts.append(datetime.datetime.fromisoformat(str(raw)).timestamp())
        except ValueError:
            continue
    sigs = con.execute(
        "SELECT direction, ts FROM signals "
        "WHERE source LIKE ? AND ts >= ? AND ts < ? ORDER BY ts",
        (source_like, start_ts, end_ts),
    ).fetchall()
    dropped: list[dict[str, Any]] = []
    converted = 0
    for direction, ts in sigs:
        if any(abs(ts - tt) <= MATCH_TOLERANCE_S for tt in trade_ts):
            converted += 1
        else:
            dropped.append({"direction": direction, "ts": ts})
    return {
        "received": len(sigs),
        "converted": converted,
        "dropped": dropped,
    }
