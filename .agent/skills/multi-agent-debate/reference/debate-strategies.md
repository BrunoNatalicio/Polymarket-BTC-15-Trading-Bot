# Debate Strategies

Choose a strategy during setup or let the manager auto-select based on problem type.

---

## Available Strategies

### 1. Round Robin (default)
**Best for:** General problems, balanced exploration

Each agent presents independently, then all read and reflect symmetrically.
No agent is assigned a special role.

Round structure:
- Round 0: Each agent presents full solution
- Rounds 1..M: Read all → Reflect → Update

**Auto-selected when:** Domain is `general` or `strategy`

---

### 2. Devil's Advocate
**Best for:** Avoiding groupthink, stress-testing a leading solution

One agent (Agente1 by default, or last agent) is assigned to **challenge** — even if they privately agree with the consensus.

Devil's Advocate rules:
- Must find at least 3 objections per round
- Must research counter-evidence even against their own position
- Explicitly labeled as "Devil's Advocate" in placeholder header
- Does NOT need to present their own full alternative solution

Round structure:
- Round 0: All agents present INCLUDING Devil's Advocate's challenge
- Rounds 1..M: Regular agents refine; DA escalates challenges based on new evidence

**Auto-selected when:** Problem has an "obvious" solution or user asks to stress-test

---

### 3. Socratic
**Best for:** Deep analysis, unclear problem definitions, root cause discovery

Agents question each other's **assumptions** rather than proposing competing solutions.

Round structure:
- Round 0: Each agent lists their TOP 3 ASSUMPTIONS about the problem
- Round 1: Each agent challenges ONE assumption from each other agent (with evidence)
- Round 2+: Revised solutions after assumption clarification

**Auto-selected when:** Domain is `architecture` or the problem statement is vague

---

### 4. Convergent
**Best for:** Decision-making with a deadline, must pick ONE solution

Agents start diverse, each round forces them toward consensus:
- Each round: Agents must adopt ONE point from another agent's solution
- Final round: Each agent proposes a unified solution
- Manager scores similarity; most-converged solution wins

**Auto-selected when:** User says "we need to decide", "pick one", "final answer"

---

### 5. Domain Expert Panel
**Best for:** Multi-faceted problems needing different lens

Each agent is assigned a ROLE based on the domain:

| Domain | Agent Roles |
|--------|------------|
| `debug` | Agent1=Backend Dev, Agent2=QA Engineer, Agent3=Performance Engineer |
| `architecture` | Agent1=Architect, Agent2=Security Engineer, Agent3=SRE/Ops |
| `security` | Agent1=Red Team, Agent2=Blue Team, Agent3=Compliance |
| `design` | Agent1=UX Designer, Agent2=Frontend Dev, Agent3=Brand Strategist |
| `code` | Agent1=Minimalist (KISS/YAGNI), Agent2=Pythonic/Idiomatic, Agent3=ML-Cost/Latency (or Performance/Profiler) |
| `general` | Agent1=Optimist, Agent2=Pessimist, Agent3=Pragmatist |

Each agent argues from their assigned perspective even if they'd personally disagree.

**Auto-selected when:** N >= 3 AND domain is specialized

---

### 6. Simplicity-Convergent
**Best for:** `code` domain — refactors and "solve this complex thing, but simply" requests

Like Convergent, but the forcing function is **removing *accidental* complexity, not just agreement** — and never at the expense of solving the full problem. Each round, an agent may only update its solution if the update keeps the checks green (`ruff`/`pyright`/`test_*.py`), keeps the same scope/edge-case coverage, AND does not add complexity cost (LOC, deps, abstractions, new concepts) without measured justification. Simplifying is allowed only when it removes accidental complexity, never essential complexity.

Round structure:
- Round 0: Each agent proposes its minimal *complete* solution + execution evidence + the edge cases it covers
- Rounds 1..M: Each agent must either (a) adopt a simpler peer solution that still passes *and covers the same cases*, or (b) defend why its complexity is essential (or earned with executed evidence). A peer solution that is shorter because it covers fewer cases is rejected, not adopted.
- Resolution: the **Simplicity Gate** (see `domain-adapters.md`) breaks ties toward fewer moving parts — but only among equally-correct, equally-complete solutions

**Auto-selected when:** `domain = code` AND the user asks for "simple"/"simplify"/"simplificar"/"cleanest"

---

## Debate Knobs (paper-derived)

These tune *how* a debate runs, independent of strategy.

### Stubbornness (short vs long-form prompt) — paper §2.2, Fig. 3 & 12
The consensus prompt fed to agents each round controls how readily they abandon their own answer:

| Setting | Prompt | Effect |
|---------|--------|--------|
| **`long` (default)** | "Using the opinion of other agents as additional **advice**, can you give an updated response..." | Agents stay stubborn → slower convergence, **better final answer** |
| `short` | "Based off the opinion of other agents, can you give an updated response..." | Agents defer quickly → fast convergence, weaker answer |

Default to `long` for `code` and any reasoning task. Use `short` only when the user explicitly wants a fast decision. Agents must change a position only on **executable counter-evidence** (code) or a **verified source** (other domains) — never on disagreement alone.

### Summarization (N≥4) — paper §3.3, Fig. 13
With four or more agents, concatenating every placeholder blows the context window and *lowers* quality. Instead, the manager summarizes all *other* agents' positions into one neutral block per reader before each round. With N≤3, concatenate raw. Summarization also enables scaling to 5+ agents (which improves accuracy monotonically).

---

## Strategy + 3 Alternatives Interaction

No matter which strategy is used, if agents do not fully converge by the final round:
- **Always present exactly 3 alternatives** (see SKILL.md Conflict Resolution section)
- Alternative C is always a synthesis/hybrid
- Manager recommendation is always included

Strategy affects HOW agents arrive at their positions, not the resolution format.

---

## Auto-Selection Logic

```
if problem is vague or needs clarification → Socratic
elif domain == code AND user asks for "simple"/"simplify" → Simplicity-Convergent
elif user wants final decision → Convergent
elif N >= 3 AND specialized domain → Domain Expert Panel
elif obvious solution exists → Devil's Advocate
else → Round Robin (default)
```

Manager announces chosen strategy during setup confirmation, along with the knob settings (stubbornness, summarization).
