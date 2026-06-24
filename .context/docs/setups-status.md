---
type: doc
name: setups-status
description: Snapshot de registro completo dos dois setups (estratégias) do bot — Fusion (favorita-tardia) e TradingView/Guppy local — com estado, achados validados/reprovados, números de backtest/CPCV e próximos passos. Catch-up entre sessões.
category: strategy
generated: 2026-06-24
status: filled
scaffoldVersion: "2.0.0"
---

## Status dos Dois Setups do Bot — Snapshot 2026-06-24

> **Para que serve este doc:** ler antes de retomar o trabalho, para se atualizar rápido sobre
> onde cada setup está, o que já foi validado/reprovado e o que vem a seguir. É um **snapshot**
> (pode envelhecer); a mecânica detalhada vive nos docs linkados em §8.

O bot tem **duas estratégias mutuamente exclusivas** (`btc_trading:active_strategy`, "fusion" |
"tradingview" — nunca as duas). Ambas: apostam `MARKET_BUY_USD` (hoje $3), são **taker market
orders**, seguram até o settlement, e o `risk_engine` capa toda posição no mesmo valor.

| | **Setup 1 — Fusion** | **Setup 2 — TradingView/Guppy** |
| --- | --- | --- |
| Decide direção por | o preço Polymarket (favorita) | o sinal técnico (Guppy RSI) |
| Timing de entrada | min-13 da janela **atual** | bar close → janela **N+1** recém-aberta |
| Preço típico de entrada | $0,65–0,80 (favorita resolvida) | ~$0,50 (mercado recém-aberto) |
| Estado | **L0 ao vivo** (default) | **ativo agora** (Guppy local, dry-run) |
| Veredito atual | mantém L0; cérebro L1 reprovado | filtro de sessão 8-15 validado (CPCV) |

**Ativo neste snapshot:** `active_strategy=tradingview`, `simulation_mode=0` (live) **mas**
`tv_dry_run=1` → os trades do Guppy rodam o caminho live completo com `submit_order` pulado
(precedência do dry-run). Processos rodando: `bot.py` (tradingview), `local_signal_generator`
(+ supervisor), `backtest record` (recorder). Snapshot às 18:41 UTC.

---

## 1. Setup 1 — Fusion (favorita-tardia)

**O que faz:** apesar do nome "fusão de 6 sinais", **não** negocia a direção do voto. No
**min-13–14** de cada janela de 15m, se *qualquer* processador disparou (gate de atividade), compra
**o lado que o preço Polymarket já favorece** (YES se UP-mid > 0,60; NO se < 0,40; **pula** o miolo
0,40–0,60 — território de cara-ou-coroa, onde o bot perdia). O voto/`is_actionable`/`is_strong`
existem mas **não** decidem o trade. Detalhe completo: [fusion-strategy.md](./fusion-strategy.md).

### Estado e achados

- **L0 (regra fixa, ao vivo) — backtest in-sample (`fusion-replay`, 15m, 2026-06-16):** 300 fills,
  **278 W / 22 L (93%)**, **+$28,43** líquido do fee (+$35,54 bruto) sobre $897 apostados. → a
  favorita tardia paga depois do fee. Ressalva de seleção: quando a favorita é quase-certa (~$0,97+)
  não há asks para comprar, então esses mercados não enchem — os fills pendem para favoritas menos
  extremas (igual ao vivo).

- **Cérebro de calibração (L1/L2/L3) — L1 REPROVADO no CPCV:** o L1 é um gate de EV (calibrador
  Platt `P(win|preço@13)` → só negocia se `P_cal > breakeven(fee)`). No `fusion-cpcv` (C(6,2)=15
  paths): **mean Δ(L1−L0) = −$2,88** (L1 *pior* OOS), bate L0 em só **13%** dos paths, e
  **min losses/train = 11 ≪ 100** → veredito **INSUFFICIENT**. Causa: a ~93% de win há só ~22 perdas
  totais — a classe minoritária é fina demais para calibrar um gate de fee-edge; e o gate corta
  favoritas +EV. **Decisão: manter L0; não armar L1.** L2 (Kelly) / L3 (microestrutura) são
  design-only, travados até L1 passar.
  - **Re-trigger:** a trava é o nº de **perdas**, não de fills. Só revisitar L1 quando
    `min losses/train >= ~100` — a 93% win isso exige **~1400+ mercados settled**. Até lá, L0 *é* a
    estratégia.

- **Cross-asset (Kaggle):** a **15m é BTC-only** — btc/sol dão +EV, **eth/xrp dão RUÍNA**; o win%
  ~81% é igual nos 4 (métrica-armadilha, não indica edge). Já a **4h GENERALIZA** (4 ativos +EV) e
  é **robusta a parâmetros** (oposto da 15m) → promissora. Na 4h o **DOWN é o lado fraco** (21/31
  perdas) + cauda btc-DOWN ≥0,91; mas só **31 perdas** (CPCV insuficiente) → recorder coletando
  pool 4h (btc/eth/sol/xrp) para validar.

- **Yes-Bias / gate de volatilidade:** scan estrutural (`backtest bias`) não achou **Yes-Bias
  direcional** (EV simétrico; DOWN não é fraco — o Yes-Bias dos relatórios não replica). O "lever de
  vol" parecia +EV mas era proxy **não-causal**; o gate causal (`--vol-gate`) foi **REPROVADO no
  CPCV** (15m NO GAIN, delta −$15; 5m insuficiente). Conclusão: manter L0; o trend filter já captura.

- **Capacidade / liquidez (sweep de stake) — comporta-se ao CONTRÁRIO do Setup 2:** ROI por trade é
  estruturalmente **baixo (~2%)** porque compra a favorita resolvida a $0,65–0,80 (cada win paga
  pouco), mas o win rate é 93% e há ~906 fills → **PnL absoluto alto**. Slippage é **baixo** (book da
  favorita perto do expiry é fundo: 32 bps @ $100 vs 124 bps do TV). O limitador de tamanho **não é o
  slippage — é a EXHAUSTION** (book esgota → fill parcial): <1% até $100, ~7% a $300. **Sweet spot
  $50–$100.** Tabela em §3.7. (`bankroll != staked`: capital é rotativo, ~5 posições concorrentes.)

### Próximos passos — Setup 1
1. Deixar o recorder acumular **mercados 15m settled** rumo a ~1400+ (losses≥100) e **re-rodar
   `fusion-cpcv`**; só então reconsiderar L1.
2. Validar a **estratégia 4h** quando o pool 4h tiver perdas suficientes (CPCV) — é a aposta mais
   promissora de generalização.

---

## 2. Setup 2 — TradingView/Guppy local

**O que faz:** um sinal técnico (Guppy RSI) dispara no **bar close** do 15m; o bot mapeia para a
janela **N+1** recém-aberta (seleção por wall-clock, não índice) e compra YES (UP) ou NO (DOWN)
nessa janela, a ~$0,50. Como é taker, paga o **fee de 15m crypto** (`C×0,07×p×(1−p)`, em shares;
pico perto de $0,50). Mecânica: [tradingview-runbook.md](./tradingview-runbook.md).

### Evolução: webhook TV → Guppy local (TRK-001)

O webhook do TradingView foi **substituído por um gerador local** do sinal Guppy RSI
(`local_signal_generator.py`, processo separado com supervisor auto-restart), que publica o **mesmo
JSON na mesma fila** (`btc_trading:tradingview_signals`) — o bot reaproveita 100% o caminho de trade
(N+1, gate de book, conviction sizing, dry-run) e os knobs `TV_*`. **Gate de paridade PASSOU EXATO**
(910/910 sinais idênticos ao export do próprio TV). Feed = Binance BTCUSDT 15m. **Exclusividade:**
rodar OU o gerador local OU o webhook — nunca os dois alimentando a fila. Detalhe:
[local-signal-runbook.md](./local-signal-runbook.md).

### Achados (post-mortem + validação 2026-06-24)

- **Post-mortem de losses — a perda é REGIME, não evento:** baixa-vol/madrugada perde; a **sessão
  EU carrega o lucro**. CoinDesk confirma (alta-vol = todo o lucro; funding/OI/news nulos).
  Hipóteses rejeitadas: momentum, volume, slippage, direção, funding, OI, news. Detalhe:
  [tv-loss-postmortem-findings.md](./tv-loss-postmortem-findings.md).

- **Filtro de sessão `TV_TRADE_HOURS=8-15` — PASSOU no CPCV purgado (2026-06-24):** **+23 pp de
  ROI**, positivo em **100% dos paths**, estável em toda a grade de parâmetros. É a **única das três
  alavancas** (EV gate, vol gate, sessão) que sobrevive ao CPCV. Tabelas em §3.

- **Banda de probabilidade:**
  - **Floor `TV_MIN_BOOK_PROB=0.42`** — já em produção, validado antes (não apostar contra a prob
    implícita; remove os azarões `p<0,42` que perdem).
  - **Teto `TV_MAX_BOOK_PROB=0.50`** — passou o CPCV com **convicção MODERADA** (+6,9 pp ROI, 73%
    dos paths). Corta entradas em favorita cara (≥0,50); ganho é de **eficiência/ROI**, não de
    volume (em ~1/3 dos folds reduz o PnL total). Fundamentado (fee côncavo + pouco upside ao pagar
    caro). **Não ligado** — ver decisão abaixo.

- **Capacidade / liquidez (sweep de stake):** **sem cliff** — slippage cresce ~linear (~1,1 bps por
  $1). **Sweet spot $50–$100** (ROI ~30–31%, slippage gerenciável). Acima de $150 paga pedágio
  crescente. Os ROIs são **teto** (slippage modelado sobre book gravado; ao vivo é pior). Tabela §3.

- **1º dia de dry-run do Guppy local:** ~20 sinais → 17 trades (3 pulados pelo gate de book); o
  conjunto live faz parte da base de 271 trades dos replays. O recorte 8-15 desse período rende
  ROI muito maior que operar 24h (confirma a tese de sessão ao vivo).

### Pendências conhecidas — Setup 2
- **Gate de concordância com book / confirmation layer (Fase 2):** posterior + z_mom; prior UP real
  ~52,5%; trava **n>=200** em código antes de ir ao vivo. [tv-confirmation-layer.md](./tv-confirmation-layer.md).
- **UP validado, DOWN dropado:** UP ~64% via CLOB (edge de continuação); DOWN é execução, não
  timing — aguardando mais coleta antes de reativar.
- **API key Polymarket 401:** credenciais inválidas; o **dry-run mascara** o erro. Regenerar
  **antes** de ir a live real (`tv_dry_run=0`).

### Config de produção ATUAL (`.env`)
```
TV_TRADE_HOURS=8-15        # sessão EU — validada no CPCV
TV_MIN_BOOK_PROB=0.42      # floor da banda — já validado
TV_MAX_BOOK_PROB=1.0       # teto OFF (banda superior desligada)
```
**DECISÃO (2026-06-24):** rodar **`1.0` ao vivo primeiro** para validar a sessão com dados reais;
só **depois** considerar mudar para `0.5` (capturar +7 pp de ROI / metade do drawdown). O bot lê
estes valores no `on_start` — toda mudança no `.env` só vale no próximo restart (o `15m_bot_runner`
recicla ~a cada 90 min).

### Próximos passos — Setup 2
1. **Ledger ao vivo** do recorte 8-15 (cada sinal pós-mudança: hora UTC, passou/descartou, outcome).
2. **Re-rodar o CPCV** com os dados ao vivo + recorder — idealmente sobre **outro regime** de
   mercado (o atual é 12 dias de BTC $59–66k).
3. Se a sessão segurar e o **teto 0,50 reconfirmar**, ligar `TV_MAX_BOOK_PROB=0.5`.
4. Corrigir a **API key 401** antes de qualquer live real.

---

## 3. Tabelas de resultado (registro completo)

Base dos replays: série 15m, fonte `tradingview` (12–24/jun, inclui webhook TV + 1º dia do Guppy
local — mesmo algoritmo), fee 0,07. A base CSV abr–jun (955 sinais) **não** é replayável (sem
book/outcome gravado).

### 3.1 Replay do filtro de sessão `8-15` (271 trades settled)
| | Sem filtro | Com `8-15` | Fora do filtro |
| --- | --- | --- | --- |
| Trades | 271 | 129 | 142 |
| Win rate | 52,4% | **62,8%** | ~43% |
| Staked | $813 | $387 | $426 |
| **PnL líquido** | +$73,36 | **+$124,35** | **−$50,99** |
| ROI | +9,0% | **+32,1%** | **−12,0%** |

### 3.2 CPCV purgado — sessão `8-15` (regra fixa; sensibilidade a parâmetros)
| Config | Paths | min kept | ΔPnL méd | ΔPnL>0 | ΔROI méd | ΔROI>0 |
| --- | --- | --- | --- | --- | --- | --- |
| g=6, k=2, emb=1w | 15 | 33 | +$17,00 | 14/15 | +23,4 pp | **15/15** |
| g=6, k=2, emb=2w | 15 | 33 | +$17,00 | 14/15 | +23,4 pp | **15/15** |
| g=6, k=3, emb=1w | 20 | 54 | +$25,50 | 17/20 | +23,3 pp | **20/20** |
| g=8, k=2, emb=1w | 28 | 24 | +$12,75 | 22/28 | +23,4 pp | **28/28** |
| g=8, k=3, emb=1w | 56 | 40 | +$19,12 | 48/56 | +23,2 pp | **56/56** |
| g=10, k=2, emb=1w | 45 | 14 | +$10,20 | 37/45 | +23,4 pp | **45/45** |

**Veredito: ROBUSTO OOS** — ROI delta preso em ~+23 pp, positivo em 100% dos paths. (L0 ROI médio
9,0% → L1 32,4%.) Ressalva: elimina sorte-de-partição, **não** risco de regime (12 dias, 1 mercado).

### 3.3 Cenários floor/teto @ stake $3 (sempre com horas 8-15)
| Cenário | Teto | Floor | Trades | Win | PnL | ROI |
| --- | --- | --- | --- | --- | --- | --- |
| A | 1.0 | 0.42 | 103 | 67,0% | +$116,34 | 37,7% |
| **B** (config "banda") | **0.5** | 0.42 | 93 | 69,9% | +$124,77 | **44,7%** |
| C (validado no CPCV) | 1.0 | — | 129 | 62,8% | +$124,35 | 32,1% |
| D | 0.5 | — | 119 | 64,7% | +$132,78 | 37,2% |

### 3.4 CPCV purgado — teto `0.50` (gate sobre o baseline A; corta 10 de 103)
| Config | Paths | min kept | ΔPnL méd | ΔPnL>0 | ΔROI méd | ΔROI>0 |
| --- | --- | --- | --- | --- | --- | --- |
| g=6, k=2, emb=1w | 15 | 29 | +$2,81 | 10/15 | +6,9 pp | 11/15 |
| g=6, k=3, emb=1w | 20 | 45 | +$4,21 | 13/20 | +7,0 pp | **20/20** |
| g=8, k=2, emb=1w | 28 | 22 | +$2,11 | 21/28 | +6,6 pp | 22/28 |
| g=8, k=3, emb=1w | 56 | 33 | +$3,16 | 42/56 | +6,8 pp | 53/56 |
| g=10, k=2, emb=1w | 45 | 16 | +$1,68 | 33/45 | +6,2 pp | 37/45 |

**Veredito: PASSA, convicção MODERADA** — +6,9 pp, 73% dos paths; bem mais fraco que a sessão
(+23 pp, 100%). Ganho de eficiência, não de volume (ΔPnL>0 só 10/15).

### 3.5 Sweep de stake — sessão `8-15` (cenário C; ROI vs slippage)
| Stake | PnL | ROI | ROI marginal | Slippage médio |
| --- | --- | --- | --- | --- |
| $10 | +$411,83 | 31,9% | — | 11 bps |
| $25 | +$1.017,26 | 31,5% | 31,3% | 33 bps |
| $50 | +$2.006,31 | 31,1% | 30,7% | 66 bps |
| $75 | +$2.976,57 | 30,8% | 30,1% | 96 bps |
| $100 | +$3.928,02 | 30,4% | 29,5% | 124 bps |
| $150 | +$5.787,03 | 29,9% | 28,8% | 174 bps |
| $200 | +$7.563,29 | 29,3% | 27,5% | 227 bps |
| $300 | +$10.918,64 | 28,2% | 26,0% | 326 bps |

### 3.6 Bankroll / drawdown @ stake $100
| Cenário | Max drawdown | Maior seq. de perdas | Bankroll sugerido |
| --- | --- | --- | --- |
| Sem filtro (24h) | $1.377 | 7 | $3.000–4.000 |
| Só sessão `8-15` (C) | $400 | 4 | $1.000–1.200 |
| Banda `0,42–0,50` (B) | $205 | 2 | $600–800 |

Regra: dimensionar para ~2,5–3× o drawdown histórico (o pior futuro excede o passado). Cada trade
binário pode perder o stake inteiro.

### 3.7 Sweep de stake — Fusion L0 (~906 fills; ROI vs slippage vs exhaustion)
| Stake | PnL | ROI | ROI marginal | Slippage médio | Books esgotados | Fills parciais |
| --- | --- | --- | --- | --- | --- | --- |
| $10 | +$178,94 | 2,0% | — | 2 bps | 2 | 2 |
| $25 | +$432,90 | 1,9% | 1,9% | 9 bps | 3 | 3 |
| $50 | +$826,81 | 1,8% | 1,7% | 18 bps | 6 | 6 |
| $100 | +$1.521,29 | 1,7% | 1,5% | 32 bps | 10 | 9 |
| $150 | +$2.099,86 | 1,6% | 1,3% | 46 bps | 18 | 16 |
| $200 | +$2.603,75 | 1,5% | 1,1% | 58 bps | 35 | 33 |
| $300 | +$3.404,24 | 1,3% | 0,9% | 81 bps | 65 | 62 |

**Leitura:** oposto do Setup 2 — ROI estrutural baixo (~2%, favorita cara) mas alto volume/win-rate
(93%) → PnL absoluto alto; slippage baixo (book fundo). O gargalo é a **exhaustion** (book esgota),
não o slippage: <1% até $100, ~7% a $300. **Sweet spot $50–$100.** ROI decai monotônico (sem pico):
eficiência pede stake pequeno; PnL absoluto sobe até a exhaustion morder (~$100–150). In-sample,
slippage/exhaustion sobre book gravado = teto.

---

## 4. Comparativo dos dois setups

| | **Fusion** | **TradingView/Guppy** |
| --- | --- | --- |
| Edge validado | L0 in-sample +$28,43 (93% win) | sessão 8-15 robusta no CPCV (+23 pp) |
| Reprovado no CPCV | L1 EV gate; gate de vol | EV gate; gate de vol (mesmos) |
| Promissor não-validado | estratégia **4h** (generaliza) | **teto 0,50** (convicção moderada) |
| Trava de validação | losses≥100 (~1400+ settled) | mais dias/regimes ao vivo |
| Liquidez/stake | preços skewed (fee menor) | ~$0,50 (fee máximo); sweet spot $50–100 |
| Risco aberto | — | API key 401; DOWN dropado |

---

## 5. Gotchas transversais
- **Logs em BRT (UTC-3), não UTC** — `bot.log`/`local_signal_generator.log`/`recorder.log` carimbam
  hora local; **+3h para UTC** ao cruzar com trades/janelas/filtro de horas.
- **Dry-run = caminho live com só `submit_order` pulado** — fidelidade 100% é invariante; nunca
  adicionar branches mais cedo.
- **`MARKET_BUY_USD` é o knob único de stake** (dry-run + live + caps); hoje $3 no `.env`.
- **Recorder roda sempre** (coleta = ouro; Polymarket não tem histórico L2). Não parar.
- **Resultado confiável = orderbook CLOB gravado** (vencedor com bid ~0,99 no expiry); candle é
  proxy que bateu 100%. Settlement validado 26/26 vs API oficial (por `condition_id`).
- **`fusion` e `tradingview` são mutuamente exclusivos** (`btc_trading:active_strategy`).

---

## 6. Estado operacional (snapshot 2026-06-24 18:41 UTC)
- `active_strategy = tradingview` · `simulation_mode = 0` (live) · `tv_dry_run = 1` (dry-run vence
  → caminho live, `submit_order` pulado).
- Processos: `bot.py` (tradingview) + `local_signal_generator` (+ supervisor) + `backtest record`.
- `.env`: `TV_TRADE_HOURS=8-15`, `TV_MIN_BOOK_PROB=0.42`, `TV_MAX_BOOK_PROB=1.0`, `MARKET_BUY_USD=3`.

---

## 7. Próximos passos priorizados (ambos)
1. **Setup 2:** abrir o **ledger ao vivo** do recorte 8-15 e acumular trades reais.
2. **Setup 2:** **re-rodar o CPCV** com dados live + outro regime; se segurar, ligar teto `0.5`.
3. **Setup 2:** corrigir a **API key 401** antes de qualquer live real.
4. **Setup 1:** deixar o recorder acumular (15m rumo a losses≥100; pool 4h) e **re-rodar
   `fusion-cpcv`**; validar a **4h**.

---

## 8. Referências
- [fusion-strategy.md](./fusion-strategy.md) — mecânica do Fusion, brain ladder L0/L1/L2/L3, `fusion-replay`/`fusion-cpcv`.
- [tradingview-runbook.md](./tradingview-runbook.md) — setup do webhook, dry-run, go-live, troubleshooting.
- [local-signal-runbook.md](./local-signal-runbook.md) — gerador Guppy local, gate de paridade, exclusividade.
- [tv-loss-postmortem-findings.md](./tv-loss-postmortem-findings.md) — post-mortem de losses, regime de sessão/volatilidade.
- [tv-confirmation-layer.md](./tv-confirmation-layer.md) — camada de confirmação calibrada (Fase 2).
- [backtest-validation.md](./backtest-validation.md) — settlement CLOB, modelo de fee, formato dos relatórios.
- [microstructure-edge-research.md](./microstructure-edge-research.md) — síntese de deep-research; fee côncava, MLOFI, CPCV+DSR.
