---
type: doc
name: microstructure-edge-research
description: Síntese de dois relatórios de deep research sobre microestrutura e extração de edge em mercados de previsão de curtíssimo prazo (Polymarket/Kalshi) — convergências de alta confiança, a contradição FLB×Yes-Bias, descobertas novas (Resolved Markets L2 API, Tick Rule enviesada), pool de 12 setups testáveis e implicações pro código fusion
category: reference
generated: 2026-06-23
status: filled
scaffoldVersion: "2.0.0"
---

## Pesquisa de Microestrutura & Edge — Síntese de Dois Relatórios

Consolidação de **dois relatórios independentes de deep research** (2026-06-23) sobre
como extrair vantagem em mercados de previsão de curtíssimo prazo atrelados a cripto
(Polymarket CLOB, análogos Kalshi). Os relatórios convergem fortemente; este doc
mineira o que é acionável pro nosso sistema **fusion** e marca o que ainda precisa de
validação nossa.

Fontes:
- R1 — *"Microestrutura e Extração de Vantagem em Mercados de Previsão de Curtíssimo
  Prazo"* (`~/Documents/polyhermes/analise_polymarket/`)
- R2 — *"Da Intuição à Arquitetura: Construindo um Sistema Robusto para Mercados
  Preditivos da Polymarket"* (`~/Documents/polymarket/`)

> ⚠️ **Ressalva:** os relatórios distinguem **[FATO COM FONTE]** de **[HIPÓTESE]** — esta
> síntese preserva a distinção. Nada aqui virou regra de produção sem nossa própria
> validação (CPCV / OOS / custos reais). Ver `backtest-validation.md` e
> `tv-loss-postmortem-findings.md`.

## 1. Convergências de alta confiança (R1 ∩ R2)

Pontos onde dois estudos independentes chegaram à mesma conclusão — maior prioridade.

| Tema | Achado | Implicação pro nosso sistema |
| --- | --- | --- |
| **Taxa côncava** | Fee taker = pico em p=0.50 (~3.5% do preço); colapsa pra <1.4% em p≥0.80 e <0.7% em p≥0.90. R2 cita ainda a fórmula 5m `fee/share = price × 0.25 × (price·(1−price))²` (~$0.0156 @ 0.50). | Filtro 0.40–0.60 é robusto porém tímido. **A edge do taker mora nos extremos** (p>0.75/0.80). Apertar o gate aumenta o EV líquido. |
| **OFI/MLOFI > volume** | Volume e trade-imbalance simples têm **fraco poder preditivo OOS**. OFI (variação das filas L2) e MLOFI (5 níveis) capturam a pressão real — RMSE até −75% em VAR. OFI tem long-memory e cauda pesada. | Trocar sinais baseados em volume/tick por **MLOFI do book L2 da Binance** como proxy antecipador da prob UP. |
| **Regime em 2 camadas** | Portão grosso = **sessão EU (08–16 UTC) + alta volatilidade (RV/ATR > 80–90º percentil)**. OFI vira ruído branco em baixa-vol/madrugada. | Confirma nosso achado empírico (`tv-loss-session-volatility`). Formalizar como filtro antes de qualquer sinal fino. |
| **Validação 2 estágios** | **CPCV + Deflated Sharpe** = pré-filtro estatístico (mata lookahead/overfit). **Custos reais** (fee côncava + slippage 2–4¢ ≈ 4% + latência Polygon 2–5s) = veto final. | Exatamente o funil que adotamos. Estatística = porta de entrada; custo = veto. |
| **Win rate é armadilha** | 80% das perdas líquidas concentram nos **piores 5% dos trades** (stale quotes durante stress do spot). Comprar favorito @0.90 dá ~90% win e ainda assim EV negativo se a cauda for tóxica. | Bate com nosso "win% ~81% igual nos 4 ativos mascarou ruína". Avaliar por PnL líquido/distribuição, nunca por win%. |
| **Auto-reflexão → referência externa** | Seguir a própria prob da Polymarket trata-a como oráculo; ela é só reflexo agregado com viés/lag/custo. | Reformular fusion de "segue o preço PM no min-13" para **modelar a prob futura UP = f(features externas)** — classificação supervisionada. |
| **Latência transacional** | Ping ICMP ≠ latência real. Ciclo WS/REST = 25–85ms normal, mas p95 escala pra **múltiplos segundos** durante quebra de S/R no spot. Confirmação Polygon 2–5s = limite físico p/ last-second. | Formalizar **gate de freshness de quote** (já temos cache N+1); o p95 de latência é condição de exaustão, não alvo. |

## 2. A contradição: FLB clássico × "Yes Bias"

Os relatórios **discordam sobre o lado DOWN/NO** — e isso colide com nosso próprio dado:

- **FLB (Favorite-Longshot Bias)** [FATO, R1+R2, baseado em 300k+ contratos Kalshi e
  Polymarket]: longshots (p<0.10) perdem >60% do capital; favoritos (p>0.50) vencem com
  cadência ligeiramente acima da prob implícita. Em tese, **comprar o favorito tardio é
  +EV** e o lado barato é −EV. R2 nota que isso *contradiz* nosso "DOWN é fraco".
- **Resolução via "Yes Bias"** [R1]: em cripto de curto prazo, um *Yes Bias* se sobrepõe
  ao FLB — retalho paga prêmio pelo lado afirmativo (UP/YES) em picos de incerteza perto
  da resolução. Isso **explicaria** o DOWN fraco como **estrutural** (não de execução) e
  abre a estratégia de **comprar o lado contrário quando o YES está irracionalmente
  inflado**.
- **Tensão com nosso dado:** o `tv-loss-postmortem-findings` concluiu **"DOWN não é
  fraco" (UP=DOWN=50%)** no universo TV (~11 dias, regime único). A memória
  `tv-up-validated-down-dropped` dropou DOWN por timing/conviction, não por viés
  estrutural.

→ **Ponto em aberto a validar com dados nossos:** o DOWN-fraco que observamos é (a) Yes
Bias estrutural, (b) artefato de execução/timing, ou (c) ruído de amostra pequena?
Testar com L2 histórico antes de assumir qualquer lado.

## 3. Descobertas novas e acionáveis (o que NÃO tínhamos)

🥇 **Resolved Markets API** (`resolvedmarkets.com/data/crypto`) — fornece **histórico L2
completo da Polymarket**. Mata diretamente nossa dor central ("Polymarket não tem
histórico L2; só coletamos daqui pra frente" — `validate-via-clob-orderbook`,
`data-collection-philosophy`). Permite treinar/validar modelos de proxy com histórico
real. **Avaliar custo/cobertura.**

🥇 **Tick Rule falha (~50% de acerto) na Polymarket** [FATO, R1] por autocorrelação
direcional positiva. Classificação heurística de agressor é enviesada → VPIN e spread
efetivo saem errados. Exige **dados on-chain determinísticos** (liquidação + ordens em
descanso). → Auditar `OrderBookImbalanceProcessor` e `TickVelocityProcessor`.

Outras descobertas:
- **CLOBv2 corrigiu "ghost fills"** (quantvps) — relevante p/ confiabilidade da execução.
- **Maker rebates** existem (ordem a ±3¢ do midpoint isenta a fee), mas sem colocation a
  **seleção adversa / toxic flow** destrói. Híbrido maker+cancel reativo a VPIN é
  **roadmap futuro, não agora** — reforça manter taker.
- **Por que 4h generaliza e 15m não** [HIPÓTESE fundamentada, R1]: em 15m o cross-asset é
  dominado por arbitragem de market makers robóticos nas pernas BTC/fiat; em 4h há
  *relaxation time* p/ o fluxo orgânico da altcoin se expressar. Fundamenta nosso achado
  (`fusion-15m-not-cross-asset`, `fusion-4h-promising`).
- **Funding rate em percentil extremo → mean-reversion forçado** (filtro barato novo).
- **Deribit DTE-0 skew/gamma** como sinal de quebra institucional.
- **Bots reais de referência** (estudar): `aulekator/Polymarket-BTC-15-Minute-Trading-Bot`,
  `txbabaxyz/polyrec`, `FrondEnt/PolymarketBTC15mAssistant` + artigos Medium "5-minute
  last-second dynamics".

## 4. Pool consolidado de setups testáveis (12)

Priorizados pelos critérios do projeto (4h cross-asset; fee-aware; sobrevive a custo).
Os **negritos** aparecem nos dois relatórios ou casam diretamente com a tese fusion.

| # | Setup | Regra (resumo) | Horizonte/Lado | Dados | Modo de falha |
| --- | --- | --- | --- | --- | --- |
| 1 | **MLOFI Late-Burst** | aos 13m: prob PM > 0.75 **e** MLOFI L2 > 1.5σ a favor nos últimos 3m | 15m / favorito taker | Binance L2 100ms; WS PM | reversão algorítmica no min 14 |
| 2 | **Lag Dinâmico** | spot > VWAP(5m)+X·RV(1h) **e** prob PM < 0.45 → YES | 4h (entrada na metade) | Binance L2/OHLCV; prob PM | correlação spot↔prob se deteriora |
| 3 | **Regime Sessão+Vol** | metade da janela: EU 08–16 UTC **e** RV(1h) > 90º pct → segue direção | 4h | OHLCV; prob PM | mudança estrutural de vol |
| 4 | **Momentum CVD** | CVD acumulado em tendência **e** prob PM no extremo oposto | 4h | CryptoQuant CVD; prob PM | CVD perde força de tendência |
| 5 | **Yes Bias Fader** | Deribit skew normal mas prob de queda PM superaquece >0.85 por pânico → compra contra | 4h/diário / longshot | superfície vol Deribit; prob PM | spread insustentável em p<0.15 |
| 6 | **Fee-Bypass Sniper** | só p>0.90 / p<0.10 perto do min 14, confirmado por OBI spot | 15m / micro-favorito | motor de execução rápido | não-preenchimento, capital travado |
| 7 | Liquidity Vacuum Mean-Rev | primeiros 5m: prob salta 0.50→0.85 por stop-run + VPIN exaustão → fade | 15m / contra-tendência | quotes PM + tick velocity | breakout verdadeiro (não choque) |
| 8 | CVD Divergence Cap | abster de YES @0.65 se CVD 30m diverge negativo | 1h/15m | trades Binance | preço sustentado por ordens passivas |
| 9 | Cross-Session Liquidation Follow | clusters de liquidação massivos na transição 08:00 UTC → continuação | 4h / favorito | Coinglass/Bybit liq streams | defasagem fecho H4 × liquidações |
| 10 | Structural DOWN Decay | BTC lateral (ATR<mediana) → compra UP/YES <0.30 | 4h | ATR spot; spread UP/DOWN | spike de liquidez asiática |
| 11 | Last-Second Momentum | últimos 30s: spot > p_inicial + Y·RV(1m) **e** prob PM < 0.50 → YES | 15m | tick data; prob PM | latência Polygon 2–5s impede entrada |
| 12 | Deribit Skew Fader | Put skew DTE-0 vs prob PM superaquecida | diário/4h | Deribit | esgotamento do book |

## 5. Implicações concretas pro código

- **Gate fusion → classificador supervisionado**: prob UP = f(MLOFI, OBI, CVD, funding,
  regime), treinado com L2 histórico (Resolved Markets) em vez de heurística multi-sinal.
- **Apertar thresholds** do trend-filter de 0.60/0.40 → **0.75/0.25** (zona de fee baixa).
- **Auditar classificação de agressor** em `OrderBookImbalanceProcessor` /
  `TickVelocityProcessor` (Tick Rule ~50% na PM).
- **Filtro de freshness de quote** formal (estender o cache N+1 com gate de p95 latência).
- **Camada de regime** explícita (sessão EU + RV/ATR percentil) como portão grosso antes
  de tudo.

## 6. Próximos passos sugeridos

1. Validar o **Yes Bias** (DOWN fraco é estrutural?) com L2 histórico — resolve a
   contradição da §2.
2. Avaliar **custo/cobertura da Resolved Markets API** (destrava treino supervisionado).
3. Backtest **MLOFI vs sinais atuais** sob CPCV + Deflated Sharpe + custos reais.
4. Priorizar setups **4h cross-asset** (#2, #3, #4) pela tese de generalização.

## Memórias relacionadas

`fusion-l0-backtest` · `fusion-15m-not-cross-asset` · `fusion-4h-promising` ·
`tv-loss-session-volatility` · `tv-up-validated-down-dropped` · `polymarket-15m-taker-fee`
· `validate-via-clob-orderbook` · `data-collection-philosophy`
