---
source: local-signal-runbook.md
type: generic
---

# Local Signal Runbook — Gerador Guppy RSI (substitui o TradingView)

`local_signal_generator.py` gera os sinais UP/DOWN **localmente** a partir de
klines 15m da Binance, eliminando a dependência do TradingView. Ele replica o
indicador Pine "Guppy RSI Polymarket Bot" e publica o **mesmo JSON** na **mesma
fila Redis** que o webhook usava — então todo o caminho de trade já validado
(seleção da janela N+1, gate de concordância com book, conviction sizing, dedup,
filtro de sessão/banda, dry-run) é reaproveitado sem alteração.

## Como funciona

```
Binance WS klines 15m ─► on_closed (candle fechado) ─► guppy_signal()
        ▲                                                     │ UP/DOWN
   seeding REST (/api/v3/klines, warmup ~250)                 ▼
                                   RPUSH btc_trading:tradingview_signals
                                   (+ cópia em btc_trading:tv_signal_log)
                                                              │
                            bot.py _handle_tradingview_signal ◄┘  (BLPOP)
                            └─ janela N+1 · gate de book · sizing · dry-run/live
```

- **Lógica do sinal** (`local_signal/guppy.py`, função pura): `RSINorm =
  RSI(close,10)` (Wilder/RMA) → `ma1 = EMA(RSINorm,3)`, `ma5 = EMA(RSINorm,21)`;
  `volMA = SMA(volume,20)`. `UP = change(ma1)>=0 & ma1>ma5 & volume>volMA &
  close>open`; `DOWN` é o espelho com `close<open`. Avaliado no candle 15m
  **fechado**.
- **Timing (janela N+1):** o candle fecha em `:00/:15/:30/:45` e o gerador emite
  logo em seguida; `floor(received_at/900)*900` cai na janela Polymarket
  recém-aberta — idêntico ao alerta TV e ao `attach_target_tokens` do backtest.
- **Resiliência:** re-semeia via REST a cada (re)conexão do WebSocket, então um
  gap nunca gera sinal sobre dado defasado.

## Pré-requisitos

- `.env` com `REDIS_*` (igual ao bot). Opcional: `LOCAL_SIGNAL_SYMBOL`
  (default `BTCUSDT`), `LOCAL_SIGNAL_INTERVAL` (default `15m`).
- Os knobs `TV_*` (floor/sizing/sessão/banda) continuam valendo — são aplicados
  a jusante por `_handle_tradingview_signal`, não pelo gerador.
- Redis acessível (neste setup roda no WSL, `localhost:6379` DB 2).

## ⚠️ Exclusividade (regra de ouro)

Rode **ESTE gerador OU** o `tradingview_webhook_receiver.py` — **nunca os dois**
alimentando a fila ao mesmo tempo (disparo duplicado na mesma janela). Para
migrar do TradingView para o local:

1. Pare o `tradingview_webhook_receiver.py` (e o túnel cloudflared/ngrok).
2. Suba o gerador local (abaixo).
3. `active_strategy` deve continuar `tradingview` (é o gate de consumo no bot):
   `uv run python redis_control.py strategy tradingview`.

## Subir o gerador

Direto (uma vez):

```bash
uv run python local_signal_generator.py
```

Supervisionado (auto-restart em crash, como o `15m_bot_runner.py` faz com o bot —
use isto em produção, no lugar do receiver):

```bash
uv run python local_signal_runner.py
```

Log esperado no boot: `Seeded N closed 15m candles for BTCUSDT; streaming…`.
A cada candle gatilho: `Local Guppy signal queued: UP|DOWN (close=…, vol=…)`.

## Validação de paridade (gate go/no-go)

A função pura é validada **barra a barra** contra as colunas que o **próprio
TradingView** exporta (`Bot Sinal UP/DOWN`, RSINorm, ma1, ma5):

```bash
uv run python -m backtest guppy-parity --csv ".context/docs/COINBASE_BTCUSD, 15.csv"
```

Resultado de referência: **910/910** sinais idênticos, zero miss/extra/conflito;
erro vs as colunas do TV a nível de float (≈1e-8). A paridade é de **algoritmo**
(feed-agnóstica): o CSV é Coinbase, o live é Binance — a mesma matemática.

## Checklist de go-live (E2E manual)

O caminho gerador→fila→Redis é validado nos testes; o E2E completo até o trade
exige a stack do bot rodando e um fechamento de candle real:

1. `redis_control.py dryrun on` (caminho live completo, só `submit_order`
   pulado) e `redis_control.py strategy tradingview`.
2. Suba `bot.py` (via `15m_bot_runner.py`) e o `local_signal_generator.py`.
3. Aguarde o próximo fechamento de 15m. Se o Guppy disparar e o book N+1 estiver
   quente, o trade aparece em `tv_dry_run_trades.json`.
4. Confira os logs: `Local Guppy signal queued` (gerador) e
   `TRADINGVIEW SIGNAL TRADE` (bot). Ausência de quote fresca/janela N+1 =
   sinal descartado (esperado), não um bug.
5. Só então `dryrun off` para ir ao vivo.

## Testes

```bash
uv run python local_signal/test_guppy.py          # 12 testes (indicadores + sinal)
uv run python test_local_signal_generator.py      # 4 testes (gerador, FakeRedis)
uv run python -m backtest guppy-parity --csv "..." # gate de paridade
```

## Troubleshooting

| Sintoma | Causa provável | Ação |
|---------|----------------|------|
| Nenhum sinal por horas | mercado lateral (volume < média ou ma1≈ma5) | normal; confira o log de candles |
| `Cannot start without Redis` | Redis fora do ar (WSL) | suba o Redis no WSL |
| Sinal enfileirado mas sem trade | book N+1 não quente / fora de sessão / abaixo do floor | esperado (gates a jusante); ver log do bot |
| Disparo duplicado | webhook receiver ainda rodando | pare o receiver/túnel (exclusividade) |
