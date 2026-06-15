---
name: multi-agent-debate
description: Use when a problem requires multiple independent perspectives, structured consensus-building, or when the user requests a multi-agent debate. Works for any domain: debugging, architecture, code review, design, security, strategy. Agents independently research with web search and codebase grounding, debate in rounds, and always resolve to 3 alternatives when divergent.
argument-hint: "[setup|start|evaluate|replay]"
user-invocable: true
allowed-tools: Read, Glob, Grep, WebSearch, Browser, Bash
---

# Multi-Agent Debate

Orchestrate N independent LLM agents that simultaneously solve a problem, debate across M rounds with advanced research grounding, and converge on a synthesized result. Works for **any domain**: debugging, code architecture, security analysis, design, strategic decisions — with a dedicated `code` domain tuned for Python/AI work that resolves complex problems with the *simplest solution that **fully solves** the problem and provably runs* (simplicity, not simplism: shed accidental complexity, keep essential complexity).

## Research Foundation

This skill operationalizes Du et al., *"Improving Factuality and Reasoning in Language Models through Multiagent Debate"* (MIT/Google, [arXiv:2305.14325](https://arxiv.org/abs/2305.14325); local copy in [.context/docs](../../../.context/docs/)). Findings applied here:

- **Long-form / "stubborn" prompts beat agreeable ones.** RLHF-tuned models converge too early on a worse answer; instructing agents to *hold their position unless shown executable counter-evidence* yields better final solutions. → default `stubbornness = long` (see Round loop).
- **Summarize, don't concatenate, with many agents.** For N≥4, summarizing other agents' responses into one block improves performance and avoids context blow-up. → SUMARIZE step.
- **Diverse persona initialization** raises accuracy. → domain-specific personas (`reference/debate-strategies.md`).
- **Debate ≠ majority vote.** Agents frequently reach the right answer even when *all* started wrong, via cross-examination of reasoning — so we never resolve by counting votes.
- **"Ease of persuasion" is a confidence signal.** Claims an agent drops the moment it's challenged (without evidence) are likely hallucinations → *drop-under-scrutiny* (see Manager Role).
- **Orthogonal to Chain-of-Thought**; debate composes with CoT. Rounds plateau around 4; more agents help monotonically (with summarization).

## Modes

| Mode | Invocation | Purpose |
|------|-----------|---------|
| **setup** | `/debate setup` | Configure agents, rounds, collect problem |
| **start** | `/debate start` | Execute full debate protocol |
| **evaluate** | `/debate evaluate` | Score and synthesize final output |
| **replay** | `/debate replay` | Re-run with modified parameter |

Default (no argument): run setup → start → evaluate in sequence.

---

## Setup Mode

When invoked with `setup` or first time:

**PAUSE 1** — Ask the user:
> How many agents? How many debate rounds?
> (Recommended: 3 agents, 2-3 rounds for complex problems)

**PAUSE 2** — Ask the user:
> What is the problem to solve? (Provide as much context as possible)

**Initialize placeholders:**
Create named memory slots: {Agente1}, {Agente2}, ... {AgenteN}

Each placeholder stores:
```
Agent: AgentN
Domain: [auto-detected]
Solution: [content]
Confidence: [1-10]
Sources: [list]
Round: [current round]
```

**Auto-detect domain** from the problem statement (first match wins, in this order):
- Code errors / stack traces / bugs → `debug`
- "security", "vulnerability", "auth", "CVE", "injection" → `security`
- "architecture", "scale", "system", "microservice" → `architecture`
- "UI", "design", "layout", "colors", "component" → `design`
- "python", ".py", "refactor", "simplify"/"simplificar", "type hint", "pyright", "ruff", "LLM", "prompt", "model"/"inference", "pipeline", "embedding", "AI"/"IA", "ML" → `code`
- "strategy", "decision", "tradeoff", "choose between" → `strategy`
- Default → `general`

`debug` and `security` keep priority over `code`: a stack trace or CVE routes to those even if the code is Python/AI. `code` is for *improving / simplifying* working code, not diagnosing a failure.

---

## Debate Protocol

### Round 0 — Independent Initial Solutions

Each agent generates its solution **independently** (no knowledge of others).

**REQUIRED for each agent before writing solution:**
> See `reference/research-protocol.md` — MANDATORY research phase

Each agent writes to its placeholder with full structured format.

**PAUSE** — Display all Round 0 solutions. Ask user to confirm continuation.

---

### Rounds 1..M — Reflection & Update Loop

For each round:

0. **SUMARIZE (N≥4 only)** — Instead of feeding every other placeholder raw, the manager first condenses all *other* agents' positions into one neutral summary block per reader (paper: summarization with many agents beats concatenation, Fig. 13). With N≤3, concatenate raw.
1. **READ** — Each agent reads the other agents' positions (summary block for N≥4, raw otherwise)
2. **REFLECT** — Each agent identifies: points of agreement, points of conflict, evidence gaps in others' arguments
3. **RESEARCH** — Each agent runs additional targeted research based on conflicts found
4. **UPDATE** — Each agent updates its own placeholder (may converge or diverge further)

**Stubbornness knob (default `long`).** Use the long-form consensus prompt — *"Using the opinion of other agents as additional advice, can you give an updated response..."* — which makes agents hold their own position unless shown stronger evidence, producing slower convergence but better final answers (paper §2.2, Fig. 3 & 12). Agents must NOT change a position merely because others disagree; they change only when presented with **executable counter-evidence** (code domain) or a **verified source** (other domains). Switch to the short-form prompt only when the user explicitly wants fast convergence.

**PAUSE after each round** — Display all updated solutions. Ask user:
> "Agents updated their positions. Continue to round N+1? (yes/no/skip to evaluate)"

Rounds plateau around 4 (paper §3.3): if positions are stable two rounds running, offer to skip to evaluate.

---

### Conflict Resolution — Always 3 Alternatives

When agents diverge after all rounds, **NEVER pick one winner**. Always present exactly 3 alternatives:

**Format:**
```
## Three Paths Forward

### 🔵 Alternative A: [Name]
Championed by: AgentX (confidence: N/10)
Summary: [what this proposes]
Strengths: [evidence-backed pros]
Tradeoffs: [honest cons]
Best for: [when to choose this]

### 🟢 Alternative B: [Name]
Championed by: AgentY (confidence: N/10)
[same structure]

### 🟡 Alternative C: [Name / Hybrid]
Synthesis of: [which agents contributed]
[same structure]

### Recommendation
[Manager agent recommendation with explicit reasoning]
```

The third alternative MUST be a synthesis/hybrid when the first two are opposing approaches.

**Resolution is never by majority vote.** Debate corrects reasoning — agents often reach the right answer even when all started wrong (paper §3.1). Weigh the *evidence*, not the head count.

---

## Manager Agent Role

You (the AI) act as the **debate manager**:
- Never take sides during debate rounds
- Ensure each agent follows the research protocol
- Flag when an agent makes unsupported claims
- In the final evaluation, provide a manager recommendation WITH reasoning
- The manager does NOT override — the user makes the final call

**Drop-under-scrutiny rule (anti-hallucination, paper §3.2).** Track each contested claim across rounds. Any claim that the agent who proposed it abandons under challenge *without* supplying evidence is marked **low-confidence and omitted from the final synthesis** — easily-abandoned claims are likely hallucinations. Conversely, a claim that survives repeated challenge (high "persuasion resistance") earns high confidence. The manager records this per claim and reports it in evaluation.

---

## Evaluate Mode

After all rounds complete, run domain-appropriate evaluation.
See `reference/evaluation-rubric.md` for full rubrics.

**Output format:**
```
## Debate Evaluation Report

### Summary
Problem: [restatement]
Agents: N | Rounds: M | Domain: [detected]

### Agent Performance
| Agent | Final Confidence | Key Contribution | Evidence Quality |
|-------|-----------------|-----------------|-----------------|
| Agente1 | N/10 | [summary] | [H/M/L] |

### Consensus Areas
[What all agents agreed on]

### Divergence Areas  
[What remained contested + the 3 alternatives]

### Evaluation Score
[Domain rubric scores]

### Manager Recommendation
[Explicit recommendation with reasoning]
```

---

## Replay Mode

Re-run the debate with one parameter changed:
- Different number of agents
- Different research depth
- Locked position for one agent (Devil's Advocate mode)
- Different domain adapter

---

## Companion Skills

This skill works best with:
- **impeccable** — For design domain evaluation (`/audit`, `/critique`)
- **awesome-design-md** — For design direction research (DESIGN.md files)

When domain = `design`: automatically reference impeccable for evaluation criteria.
When domain = `design` + agent needs brand reference: use awesome-design-md for DESIGN.md options.
When domain = `code`: treat the repo quality gate as the source of execution truth — `uv run ruff check .`, `uv run pyright`, and the relevant standalone `test_*.py` script (this repo has no pytest suite; see CLAUDE.md). A behavioral claim isn't "factual" until it's been run.

---

## Reference Files

- `reference/research-protocol.md` — MANDATORY research methodology
- `reference/evaluation-rubric.md` — Domain-specific scoring rubrics
- `reference/debate-strategies.md` — Debate configuration strategies
- `reference/domain-adapters.md` — Per-domain behavior rules
