# Research Protocol

Every agent MUST complete this protocol before writing any solution or update.
**Non-negotiable. Unsourced claims are flagged by the manager agent.**

---

## Phase 0: Execution Grounding (`code` domain) — MANDATORY before any behavioral claim

For the `code` domain, *factuality means it runs* (the code equivalent of the paper's multi-source fact-checking). Before claiming any behavior, an agent MUST execute and paste the output:

```
$ uv run ruff check <target>      → [output]
$ uv run pyright                  → [output]
$ uv run python <relevant test_*.py>  → [output]
```

(This repo has no pytest suite — tests are standalone `test_*.py` scripts; see CLAUDE.md.)

Rules:
- A behavioral claim with **no execution output** is marked `[UNVERIFIED]` and its agent's confidence drops 20%.
- An agent may only overturn a peer's position by showing **executable counter-evidence** — output that contradicts the peer's claim. Disagreement alone is not enough.
- Record the complexity cost alongside (LOC delta, new deps, new concepts) so the Simplicity Gate can rank passing solutions.

---

## Phase 1: Codebase Grounding (code-related domains)

For domains: `debug`, `architecture`, `security`

Run BEFORE generating any solution:

1. **Map the problem area**
   - `list_dir` on affected directories
   - `grep_search` for relevant patterns, function names, error strings
   - `view_file` on key files (entry points, configs, affected modules)

2. **Find related evidence**
   - Search for the exact error message, if any
   - Find all call sites of affected functions
   - Check test files for expected behavior
   - Look for existing comments/TODOs related to the issue

3. **Document findings in placeholder**
   ```
   Codebase Evidence:
   - File: path/to/file.py, Line: N — [what was found]
   - Pattern: [grep result summary]
   ```

---

## Phase 2: Web Search Grounding (all domains)

Run for EVERY agent, EVERY domain:

1. **Best practices search**
   - Search: "[problem keywords] best practices [year]"
   - Search: "[technology] [problem type] solution"

2. **Known issues / CVEs (security domain)**
   - Search: "[library/framework] CVE [year]"
   - Search: "[vulnerability type] [technology]"

3. **Design references (design domain)**
   - Fetch DESIGN.md from awesome-design-md: `getdesign.md/{brand}/design-md`
   - Search: "[ui pattern] design system [brand]"

4. **Document sources in placeholder**
   ```
   Web Sources:
   - URL: [link] — [what was learned]
   - Confidence impact: +/- N points because [reason]
   ```

---

## Phase 3: Cross-Validation (rounds 1..M only)

During reflection rounds, each agent MUST:

1. **Verify opponent sources** — If an agent claims "X is best practice", verify the cited source
2. **Challenge unsupported claims** — Flag any claim without a source in reflection
3. **Upgrade/downgrade own confidence** — If opponent evidence is stronger, acknowledge it
4. **Search for counter-evidence** — Actively search for evidence AGAINST your own position

```
Reflection Notes:
- AgentX claimed [Y] — I verified: [TRUE/FALSE/PARTIALLY TRUE] — Source: [URL/file/execution output]
- My confidence adjusted: [N → M] because [reason]
- Counter-evidence found: [what I searched/ran, what I found]
- Persuasion resistance: [did I hold my own claim under challenge? on what evidence?]
```

**Drop-under-scrutiny (paper §3.2).** When challenged on a claim, an agent either (a) defends it with evidence (source or execution output) or (b) drops it. A claim dropped *without* evidence is flagged low-confidence and the manager **omits it from the final synthesis** — easily-abandoned claims are likely hallucinations. Claims that survive repeated challenge earn high confidence. The manager logs persuasion resistance per contested claim for the evaluation report.

**Stay stubborn (long-form, paper §2.2).** Do not abandon your position merely because peers disagree; convergence should be driven by evidence, not social pressure. This produces slower but better-converged answers.

---

## Summarization for Many Agents (N≥4, paper §3.3)

When the debate has four or more agents, the manager summarizes all *other* agents' positions into one neutral block per reader instead of concatenating raw placeholders. This improves debate quality and prevents context-length blow-up, and is what enables scaling to 5+ agents. With N≤3, feed positions raw.

---

## Graceful Degradation

If search tools are unavailable:
- Mark all claims as `[UNVERIFIED - tool unavailable]`
- Request user to provide relevant documentation or context
- Reduce confidence scores by 20% across the board
- Still complete the debate with knowledge-based reasoning

---

## Evidence Quality Scoring

| Level | Criteria |
|-------|----------|
| **High** | Direct code reference + verified web source |
| **Medium** | One source (code OR web), not both |
| **Low** | Knowledge-based, no external verification |
| **Flag** | Claim made without any support |
