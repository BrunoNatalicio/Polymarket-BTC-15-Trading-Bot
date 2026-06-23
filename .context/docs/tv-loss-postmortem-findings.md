---
type: doc
name: tv-loss-postmortem-findings
description: Post-mortem dos losses da estratégia TradingView — scorecard, sizing, sessão/volatilidade, cruzamento CoinDesk e o achado-chave (a edge inverte por sessão × p_side); base do filtro EU+faixa opt-in
category: reference
generated: 2026-06-22
status: filled
scaffoldVersion: "2.0.0"
---

## Post-mortem dos LOSSES — Estratégia TradingView

Achados consolidados da análise de 2026-06-22 (universo all-time: 220 sinais, 219
resolvidos, 110 losses; replay contra o **CLOB gravado** + cruzamento com dados
externos da CoinDesk). Ferramenta: `uv run python -m backtest loss-postmortem`
(commit `fbafb40`) e cortes ad-hoc via `report`/`tune`.

> ⚠️ **Ressalva que vale para TUDO abaixo:** universo de **~11 dias num único regime
> de mercado** (BTC de lado, $62–66k). Os cortes por sessão/faixa são **in-sample**;
> escolher o melhor olhando o próprio dado é otimização. Nada virou regra de produção
> sem **validação out-of-sample** (n≥200 / walk-forward / CPCV). Ver
> `.context/docs/backtest-validation.md` e a memória `tv-loss-session-volatility`.

## 1. Scorecard atual (all-time, resolvido via CLOB)

| Visão | Trades | Win% | PnL (líq. fee) |
| --- | --- | --- | --- |
| Bot (dry-run real, `tv_dry_run_trades.json`) | 162 | 53% | +$15.42 |
| Estratégia "trade-tudo" (sem gate) | 219 | 50% | +$24.60 |
| Estratégia com gate 0,42 (produção) | 172 | 55% | +$58.70 |

O edge **comprimiu** vs 16/06 (era 55–62%): com a amostra dobrando (85→220), a
estratégia pura convergiu para perto de moeda jogada (50%). O lucro vem inteiramente
do gate de book (floor 0,42) — `backtest tune` confirma 0,42 como o config de PnL
máximo do grid (`NO DOWNGRADE`).

## 2. Conviction sizing (frac 1.0 / 0.5 / 0.33)

Sob o gate 0,42, variar `TV_SIZE_MIN_FRAC` **não muda trades nem win-rate** (55%) —
só o tamanho da aposta: 1.0 = +$58.70/$516; 0.5 = +$40.49/$347; 0.33 = +$34.28/$290.
Frac menor → menos capital e menos PnL absoluto, mas ROI/$ ~igual (11,4→11,8%) e
**menos variância**. Trade-off de risco, não de edge.

## 3. Post-mortem dos 110 losses — o que explica (e o que não)

- **Prob de entrada (`p_side`) — confirma a hipótese azarão:** faixa `<0,42` = 32%
  win, **único bucket com PnL negativo** (−$34). Relação prob→win monotônica. O gate
  0,42 já remove 32 dos 110 losses. Base do gate de book.
- **Sessão — o padrão mais forte:** Ásia 00–08 UTC 37%/−$48 · EU 08–16 61%/+$88 ·
  US 16–24 44%/−$15. (soma = +$24.60 = o PnL all-time inteiro.)
- **REJEITADOS** (não explicam os losses): momentum do BTC (z_mom — fadear o momentum
  até ganha de leve, n pequeno); slippage (~3 bps, plano); direção (UP=DOWN=50%,
  **DOWN não é fraco**).

## 4. Cruzamento CoinDesk (dados externos independentes)

- **Validação:** a série CoinDesk Coinbase BTC-USD **casa com a realidade** dos
  mercados — close do sinal `64334.01` vs close CoinDesk `64333.88` (mesma janela).
- **Volatilidade carrega o lucro:** tercil de **alta vol** (range 4h ~2,27%) = 54%
  win e **+$26.71** de +$24.60 total; vol baixa/média = coin-flip. O melhor bloco
  (12–16 UTC EU) é o de maior range (1,70%) e volume ($107M).
- **Volume:** sem sinal limpo (padrão em U — ruído).
- **Funding rate / Open Interest:** **nulos** — regime calmo o período todo (funding
  ~0,005%/8h, OI plano $6,2–6,8B). Pior dia (06-19, −$19) e melhor (06-21, +$23)
  tiveram funding igualmente positivo. Posicionamento não explica os losses.
- **News:** sem evento-gatilho; pano de fundo = BTC de lado low-$60k, "sem fundo
  confirmado" (Wintermute). Regime indeciso → resultado de moeda jogada.

**Síntese:** a perda é **regime (baixa-vol/madrugada), não evento**.

## 5. Tune por sessão (gate 0,42 + filtro de horário)

| Cenário | Trades | Win% | PnL | Δ vs base |
| --- | --- | --- | --- | --- |
| Baseline (todas as horas) | 172 | 55% | +$58.70 | — |
| Corta Ásia (00–08) | 121 | 60% | +$77.57 | +$18.87 |
| **Mantém 08–20** (corta 00–08 e 20–24) | 102 | 64% | **+$94.30** | +$35.60 |
| EU only (08–16) | 81 | 65% | +$84.44 | +$25.74 |

Blocos: Ásia 43%/−$18.87 (EV −0,37) · **EU 65%/+$84.44 (EV +1,04)** · US 48%/−$6.86.
A EU é o motor; Ásia e US perdem dinheiro.

## 6. ★ Achado-chave — a edge INVERTE por sessão × p_side

Cortando cada sessão por faixa de `p_side` (stake $3, sem gate):

| Faixa | EU 08–16 | Ásia 00–08 | US 16–24 |
| --- | --- | --- | --- |
| 0,42–0,50 | **68% · +$90** (n=72) | 35% · −$30 (n=40) | 39% · −$16 (n=31) |
| 0,50–0,60 | 44% · −$5 (n=9) | **73% · +$11** (n=11) | **71% · +$6** (n=7) |
| ≥ 0,60 | — (n=0) | — (n=0) | 100% · +$3 (n=2) |

- **Na EU o dinheiro está no "azarão-leve" (0,42–0,50)** — e ≥0,60 nem existe (o sinal
  entra na janela N+1 recém-aberta, perto de $0,50, então `p_side` se aglomera aí).
- **Na Ásia/US é o OPOSTO:** 0,42–0,50 é armadilha (perde), mas 0,50–0,60 ganha.
- **Hipótese:** EU líquida → fadear um book indeciso funciona (o sinal TA adiciona
  edge); Ásia/US finas → o fluxo informado já está certo, só vale apostar quando o
  book concorda (favorito). **Amostras >0,50 são pequenas (n=7–11): hipótese, não fato.**

## 7. Conclusão e ação

A alavanca nº1 de melhoria é um **filtro de horário/volatilidade**, não um novo gate
de prob. O combo robusto (n=72) é **EU 08–16 UTC + faixa `p_side` 0,42–0,50**: 68%
win, +$89.86 (vs +$58.70 do gate sozinho).

**Implementado como filtro opt-in (default OFF)** — knobs de env espelhados
bot↔backtest (`tv_market_select.passes_session_band`):

- `TV_TRADE_HOURS` — whitelist de horas UTC, ex. `8-15` (vazio = todas).
- `TV_MAX_BOOK_PROB` — teto da faixa de entrada, ex. `0.50` (1.0 = off).
- (floor existente: `TV_MIN_BOOK_PROB=0.42`.)

Quantificar: `uv run python -m backtest report --signal-source tradingview --stake 3
--min-entry-prob 0.42 --max-entry-prob 0.50 --trade-hours 8-15`.

**Não ligar no `.env` antes da validação OOS** (n≥200 / walk-forward). O teto 0,50 é a
parte mais fraca (poucos trades acima dele na EU); o peso está em EU + floor 0,42.

## Related Resources
- [backtest-validation.md](backtest-validation.md) — resolução de outcome via CLOB, `report`/`tune`
- [tradingview-runbook.md](tradingview-runbook.md) — operar a estratégia, go-live
- [tv-confirmation-layer.md](tv-confirmation-layer.md) — camada de confirmação calibrada (alternativa ao floor)
