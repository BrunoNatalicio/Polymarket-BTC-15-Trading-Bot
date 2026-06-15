# Domain Adapters

How the debate skill adapts its behavior per detected domain.
These rules override defaults for agent prompting, research focus, and output format.

---

## Auto-Detection Signals

| Domain | Trigger Keywords |
|--------|-----------------|
| `debug` | error, exception, bug, traceback, stacktrace, TypeError, not working, broken, failing test, crash, undefined |
| `architecture` | design system, scale, microservice, monolith, database schema, API design, refactor, structure, cloud |
| `security` | vulnerability, CVE, injection, XSS, CSRF, auth, JWT, password, exploit, attack, OWASP, pentest |
| `design` | UI, UX, layout, component, color, font, typography, spacing, CSS, frontend, visual, design system |
| `code` | python, .py, refactor, simplify/simplificar, type hint, pyright, ruff, LLM, prompt, model, inference, pipeline, embedding, AI/IA, ML |
| `strategy` | choose between, decide, tradeoff, compare, evaluate options, best approach |
| `general` | anything else |

> Priority: `debug` and `security` win over `code` when a stack trace / CVE is present. `code` is for *improving and simplifying working code* (Python / AI), not diagnosing a crash.

---

## Debug Adapter

**Agent prompting:**
> "You are a [Backend Dev / QA Engineer / Performance Engineer]. Your job is to find the ROOT CAUSE of this bug, not just the symptom. Do not propose a fix until you have identified the exact line and reason for the failure."

**Research focus:**
- PRIORITY 1: Codebase grounding (grep for error string, view affected files)
- PRIORITY 2: Web search for the exact error message + framework version
- Check recent git commits that might have introduced the bug
- Look for similar issues in the codebase (same pattern elsewhere)

**Placeholder template:**
```
## {AgentN} — Debug Analysis

### Hypothesis
[What I believe is causing the bug]

### Evidence
- File: [path], Line: [N] — [what I found]
- Pattern: [grep results]
- Web: [source] — [relevant info]

### Root Cause
[Precise statement of the root cause]

### Proposed Fix
[Code change with explanation]

### Regression Test
[How to test this never happens again]

### Confidence: N/10
```

**Output format:**
Evaluation uses Debug Rubric. Final output includes:
- Root cause statement
- Fix (with code diff)
- Test case

---

## Code Adapter (Python / AI)

For *improving and simplifying working code* — refactors, Pythonic cleanups, LLM/AI pipeline design.

**Bias: the simplest solution that *fully solves the problem* and provably runs wins.** This is *simplicity, not simplism.* Apparent simplicity that drops edge cases, narrows the problem, or handles only the happy path is **simplism and is disqualified** — it is "less code solving less problem." The goal is the elegant solution that an expert reaches *after* understanding the whole problem: it sheds **accidental** complexity but keeps the **essential** complexity the problem genuinely requires. Correctness and completeness come first; simplicity ranks competing *complete* solutions, it never excuses an incomplete one.

> **Distinguish the two kinds of complexity** (every agent must, before cutting anything):
> - **Essential** — inherent to the problem (real edge cases, concurrency, the actual input domain, failure modes). Removing it doesn't simplify; it under-solves. Keep it.
> - **Accidental** — incidental to *this* implementation (needless abstraction, premature generalization, dead branches, speculative config). This is what "simplify" targets.

**Agent prompting (personas — simple *and* intelligent):**
> **Agent1 — Minimalist (KISS/YAGNI):** "Push for the smallest solution that *fully* solves the problem and passes the tests — including its real edge cases. Argue against *speculative* and *accidental* complexity (premature abstraction, unused flexibility), never against essential complexity. If a peer's solution is shorter because it ignores a case, expose the gap."
> **Agent2 — Pythonic/Idiomatic:** "Optimize for readability and idiom: stdlib over deps, clear names, type hints, comprehensions/dataclasses where they clarify. The code should read like the surrounding code. Elegant, not merely short."
> **Agent3 — ML/Cost-Latency (AI flavor) or Performance/Profiler (general flavor):** "For AI: judge prompt/model/eval design by cost, latency, and token budget; prefer the cheapest model that *passes the eval at the required quality bar*. For perf: back every claim with a measurement, not a hunch."

Agents hold their stance (long-form/stubborn) and only concede to **executable counter-evidence** (a command + its output). A short solution is *not* counter-evidence if it solves a smaller problem.

**Research focus (execution = factuality):**
- PRIORITY 1 — **Run it.** `uv run ruff check <target>`, `uv run pyright`, and the relevant `test_*.py` script. Paste the command + output as evidence. (Repo has no pytest suite — tests are standalone scripts; see CLAUDE.md.)
- PRIORITY 2 — Codebase grounding: `grep`/`view_file` for call sites, existing idioms, and reusable utilities (never propose new code where a suitable one exists).
- PRIORITY 3 — Official docs / web for the language feature or library.

A behavioral claim with **no execution output is `[UNVERIFIED]`** and loses 20% confidence (see `research-protocol.md` Phase 0).

**Placeholder template:**
```
## {AgentN} — Code Proposal ([Minimalist / Pythonic / ML-Cost])

### Proposed Change (minimal *and complete*)
[Smallest diff or approach that FULLY solves it — name the edge cases / failure modes it covers]

### Execution Evidence
$ uv run ruff check <target>      → [output]
$ uv run pyright                  → [output]
$ uv run python <test_*.py>       → [output]

### Scope & Edge Cases Covered
[The full problem this addresses + the edge cases / inputs / failure modes handled — proof it isn't simplistic]

### Complexity Cost
LOC delta: [±N] | New deps: [list/none] | New concepts/abstractions: [list/none]
Essential vs accidental: [which complexity here is inherent to the problem vs incidental to this implementation]

### Trade-offs
| Pro | Con |
|-----|-----|
| [+] | [-] |

### Confidence: N/10  (Persuasion resistance: [how it held up under challenge])
```

**Simplicity Gate (tie-breaker — *subordinate to correctness & completeness*):**
The gate fires **only among solutions that fully solve the problem** — same passing checks AND the same scope/edge-case coverage. It is a tie-breaker, never a reason to prefer a solution that does less. Order of precedence: **(1) correctness → (2) completeness → (3) simplicity.** A solution that is simpler because it covers fewer cases loses at step 2 and never reaches the gate.

Among genuinely-equivalent solutions, the one with **fewer moving parts wins** — fewer dependencies, fewer abstractions, fewer new concepts, smaller diff. Both failure modes are penalized: **over-engineering** (accidental complexity that earns nothing) *and* **simplism** (dropping essential complexity to look small). A more complex solution must *earn* its complexity with measured, executed evidence (a real latency/correctness/robustness gain), not speculation — and a simpler one must *prove* it lost nothing essential.

**Output format:**
Evaluation uses the Code Rubric. Final output includes the minimal diff, the execution evidence that backs it, and an explicit complexity-cost line.

---

## Architecture Adapter

**Agent prompting:**
> "You are a [Architect / Security Engineer / SRE]. Analyze this architectural decision from your domain lens. Use evidence from the codebase and documented best practices. Present explicit trade-offs."

**Research focus:**
- PRIORITY 1: Current codebase structure (list_dir, read config files)
- PRIORITY 2: Official documentation for the technology stack
- Search for performance benchmarks, scalability case studies
- Look for known anti-patterns in the proposed approach

**Placeholder template:**
```
## {AgentN} — Architecture Analysis

### Proposed Approach
[Clear description of the architecture]

### Evidence Base
- Codebase: [what current structure shows]
- Best practice source: [URL] — [what it recommends]

### Trade-offs
| Pro | Con |
|-----|-----|
| [+] | [-] |

### ADR Summary
Context: [problem]
Decision: [what I propose]
Consequences: [implications]

### Confidence: N/10
```

**Output format:**
Final output in ADR format when consensus reached.

---

## Security Adapter

**Agent prompting:**
> "You are a [Red Team / Blue Team / Compliance] expert. Your job is to [find/remediate/assess] this security issue with OWASP and CVE references. Evidence-based only — no speculation."

**Research focus:**
- PRIORITY 1: OWASP Top 10 category match
- PRIORITY 2: CVE database search if library/framework is involved
- Codebase grounding: find all instances of the vulnerable pattern
- Search for known exploits and PoC code (for risk assessment)

**Placeholder template:**
```
## {AgentN} — Security Analysis

### Finding
[What the vulnerability is]

### Classification
OWASP Category: [e.g., A03:2021 - Injection]
Severity: [Critical/High/Medium/Low]
CVSS Score: [N.N] (if applicable)
CVE: [CVE-YYYY-XXXXX] (if applicable)

### Evidence
- File: [path], Line: [N] — [vulnerable code pattern]
- Web: [OWASP/CVE source]

### Attack Vector
[How an attacker would exploit this]

### Remediation
[Code fix or configuration change]

### Verification
[How to confirm the fix works]

### Confidence: N/10
```

**Output format:**
Final output as security advisory with severity + remediation steps.

---

## Design Adapter

**Agent prompting:**
> "You are a [UX Designer / Frontend Dev / Brand Strategist]. Propose a design direction for this problem. Use a DESIGN.md from awesome-design-md as your reference, or generate a custom one. Your proposal must pass the Impeccable AI Slop Test."

**Research focus:**
- PRIORITY 1: Fetch a relevant DESIGN.md from getdesign.md/{brand}/design-md
- PRIORITY 2: Read existing `.impeccable.md` or `DESIGN.md` in project if present
- Check existing component patterns in the codebase (CSS vars, design tokens)
- Web search for UI patterns in this design domain

**Placeholder template:**
```
## {AgentN} — Design Proposal

### Brand Direction
[3-word aesthetic description]
Reference: [DESIGN.md brand used or "custom"]

### Design Tokens
Primary color: oklch(...)
Font: [Display font] + [Body font]
Spacing unit: 4px

### Key Design Decisions
[What makes this proposal non-generic]

### AI Slop Test
Would someone immediately say "AI made this"? [YES/NO + why]

### Impeccable Compliance
- No side-stripe borders: [YES/NO]
- No gradient text: [YES/NO]
- No pure black/white: [YES/NO]
- No cards in cards: [YES/NO]

### Confidence: N/10
```

**Output format:**
Final output as a DESIGN.md file + Impeccable `/audit` score.

---

## Strategy / General Adapter

**Agent prompting:**
> "You are a [Optimist / Pessimist / Pragmatist]. Analyze this decision from your assigned perspective. Back every claim with evidence. Be willing to update your position with better evidence."

**Research focus:**
- Web search for case studies of similar decisions
- Look for documented failures and successes of each approach
- Cost/benefit analysis with real numbers where possible

**Placeholder template:**
```
## {AgentN} — Strategic Analysis

### Position
[Clear stance on the decision]

### Key Arguments
1. [Argument + source]
2. [Argument + source]
3. [Argument + source]

### Risk Assessment
Main risk: [what could go wrong]
Mitigation: [how to address it]

### Recommendation
[What I recommend and why]

### Confidence: N/10
```
