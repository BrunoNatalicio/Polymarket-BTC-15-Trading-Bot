---
type: skill
name: Documentation
description: Generate and update technical documentation. Use when Documenting new features or APIs, Updating docs for code changes, or Creating README or getting started guides
skillSlug: documentation
phases: [P, C]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Identify whether the change affects [CLAUDE.md](../../../CLAUDE.md) (commands, architecture, key files,
   environment) or one of the `.context/docs/*.md` files (architecture, data-flow, glossary, security,
   testing-strategy, tooling, project-overview, development-workflow).
2. For new signal processors, data sources, or Redis control-plane keys, update
   [.context/docs/architecture.md](../../docs/architecture.md) and
   [.context/docs/glossary.md](../../docs/glossary.md) with the new term/component.
3. For changes to the TradingView webhook flow, risk limits, or dry-run behavior, update
   [.context/docs/security.md](../../docs/security.md) and the "TradingView webhook strategy" section of
   [CLAUDE.md](../../../CLAUDE.md) - keep these two in sync.
4. For new/changed phase test scripts or commands, update the Commands section of
   [CLAUDE.md](../../../CLAUDE.md) and [.context/docs/testing-strategy.md](../../docs/testing-strategy.md).
5. Keep documentation grounded in this repo's actual structure - don't invent files, frameworks, or
   architecture (e.g. no ORM, no web frontend, no pytest suite) that don't exist here.
6. Verify any command or file path mentioned actually exists/works (`uv run ...`) before documenting it.

## Examples

**Updating CLAUDE.md after adding a signal processor:**
```markdown
Active processors wired into `bot.py`:
- ...existing processors...
- `FundingRateProcessor` — perpetual futures funding rate skew (new)
```

**Updating glossary.md for a new Redis key:**
```markdown
- `btc_trading:tv_last_traded_market` (DB 2) - per-15-minute-market dedup key for the
  TradingView strategy; survives the 90-minute auto-restart of `bot.py`.
```

## Quality Bar

- Cross-reference [CLAUDE.md](../../../CLAUDE.md) and `.context/docs/*.md` rather than nonexistent
  `README.md`/`AGENTS.md` files.
- Keep [CLAUDE.md](../../../CLAUDE.md) and `.context/docs/*.md` consistent - if one is updated for a behavior
  change, check the other for the same fact.
- Never document the dry-run path as anything other than "full live order path minus `submit_order`" - this is
  a hard invariant, not an implementation detail.
- Don't add documentation for hypothetical future components (mobile app, database, web frontend) beyond what
  already exists as honest placeholders in `.context/agents/`.
- Prefer updating existing docs over creating new ones.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
