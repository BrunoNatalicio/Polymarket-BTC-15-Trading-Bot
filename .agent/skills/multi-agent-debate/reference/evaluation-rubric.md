# Evaluation Rubric

Domain-specific scoring after all debate rounds complete.

---

## Domain Auto-Detection

The manager agent detects domain from problem keywords:
- `debug` → Debug Rubric
- `architecture` → Architecture Rubric
- `security` → Security Rubric
- `design` → Design Rubric (uses Impeccable)
- `code` → Code Rubric
- `strategy` or `general` → Generic Rubric

---

## Generic Rubric (all non-specialized domains)

| Criterion | Weight | Score 1-10 | Notes |
|-----------|--------|-----------|-------|
| Technical Correctness | 30% | | Is the solution factually/technically sound? |
| Completeness | 20% | | Does it address all aspects of the problem? |
| Clarity | 15% | | Is the explanation clear and actionable? |
| Elegance | 15% | | Is the solution clean, not over-engineered? |
| Evidence Quality | 10% | | Are claims backed by sources? |
| Consensus Reach | 10% | | Did agents converge or stayed fully divergent? |

**Final Score = weighted average**

---

## Debug Rubric

| Criterion | Weight |
|-----------|--------|
| Root cause correctly identified | 35% |
| Evidence quality (logs, traces, code) | 25% |
| Fix correctness + completeness | 20% |
| Regression prevention | 10% |
| Side-effect awareness | 10% |

**Good debug output includes:**
- Exact file + line causing the issue
- Reproduction steps
- Proposed fix with code
- Test to prevent regression

---

## Architecture Rubric

| Criterion | Weight |
|-----------|--------|
| Scalability | 25% |
| Maintainability & code clarity | 20% |
| Security posture | 20% |
| Performance characteristics | 15% |
| Cost efficiency | 10% |
| Team/operational alignment | 10% |

**Good architecture output includes:**
- ADR (Architecture Decision Record) format
- Explicit trade-off analysis
- Alternative approaches considered
- Migration path if changing existing system

---

## Security Rubric

| Criterion | Weight |
|-----------|--------|
| Vulnerability correctly identified | 30% |
| OWASP/CVE reference accuracy | 25% |
| Remediation completeness | 20% |
| Risk severity assessment | 15% |
| False positive rate | 10% |

**Good security output includes:**
- OWASP category or CVE reference
- Attack vector explanation
- CVSS severity score when applicable
- Remediation code or configuration
- Verification steps

---

## Code Rubric (Python / AI)

Rewards *simplicity, not simplism*: the elegant solution that fully solves the problem with the least accidental complexity. **Correctness and completeness gate the score — a solution that looks simple because it solves a smaller problem cannot win.** Simplicity is weighted heavily, but only ranks solutions that are already correct and complete.

| Criterion | Weight | Notes |
|-----------|--------|-------|
| Correctness (verified by execution) | 25% | Backed by `ruff`/`pyright`/`test_*.py` output, not assertion |
| Completeness (full problem + edge cases) | 20% | Covers the real scope, edge cases, and failure modes — not just the happy path. **A simplistic solution scores low here regardless of how clean it looks.** |
| Simplicity / smallest *essential* surface | 20% | Fewest deps, abstractions, new concepts — *after* essential complexity is kept. Penalize over-engineering AND simplism. |
| Idiomatic & readable | 15% | Pythonic, type-hinted, reads like surrounding code; elegant, not merely short |
| Test / regression coverage | 10% | Is the behavior pinned by a test that would catch regressions? |
| Evidence quality (real execution) | 5% | Command + output pasted; `[UNVERIFIED]` claims score low |
| Consensus reach | 5% | Did agents converge? (never a majority vote) |

**Good code output includes:**
- The minimal diff that *fully* solves the problem (the simplest *complete* change that passes)
- An explicit list of the scope / edge cases / failure modes it covers (proof it isn't simplistic)
- Pasted execution evidence (`ruff`, `pyright`, test script)
- A complexity-cost line (LOC delta, new deps, new concepts) labeled essential vs accidental
- A note on any complexity that was *earned* by measured evidence

**Simplicity Gate (precedence: correctness → completeness → simplicity):** the gate fires only among solutions that are equally correct *and* equally complete; then fewer moving parts wins. Both over-engineering (unearned accidental complexity) and simplism (dropping essential complexity to look small) are penalized.

---

## Design Rubric (uses Impeccable)

**Step 1** — Run Impeccable `/audit` on the proposed design
**Step 2** — Score each Impeccable dimension:

| Dimension | Weight | Impeccable Reference |
|-----------|--------|---------------------|
| Typography quality | 20% | `reference/typography.md` |
| Color & contrast | 20% | `reference/color-and-contrast.md` |
| Spatial design | 15% | `reference/spatial-design.md` |
| Motion & interaction | 15% | `reference/motion-design.md` + `interaction-design.md` |
| AI Slop Test | 20% | Would someone immediately say "AI made this"? |
| Brand alignment | 10% | Match to DESIGN.md if provided |

**Absolute fails (score 0 for Visual Details):**
- Side-stripe borders (`border-left` > 1px as accent)
- Gradient text (`background-clip: text`)
- Pure black/white (`#000` or `#fff`)
- Gray text on colored backgrounds
- Cards nested inside cards

---

## Scoring Output Format

```
### Evaluation Scores

Domain: [detected]
Rubric: [which rubric applied]

| Criterion | Weight | Score | Weighted |
|-----------|--------|-------|---------|
| [C1] | 30% | 8/10 | 2.4 |
| [C2] | 20% | 7/10 | 1.4 |
| ... | ... | ... | ... |
| **TOTAL** | 100% | | **8.1/10** |

Evidence Quality Summary:
- Agente1: HIGH (codebase + web sources)
- Agente2: MEDIUM (web sources only)
- Agente3: HIGH (codebase + web sources)

Persuasion Resistance (paper §3.2 — "ease of persuasion" as confidence):
- [Claim X]: survived N challenges with evidence → HIGH confidence, kept
- [Claim Y]: abandoned under challenge without evidence → LOW confidence, OMITTED from synthesis

Consensus Reached: [YES / PARTIAL / NO → 3 alternatives presented]
```
