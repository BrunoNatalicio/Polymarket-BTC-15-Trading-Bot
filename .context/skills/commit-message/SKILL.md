---
type: skill
name: Commit Message
description: Generate commit messages that follow conventional commits and repository scope conventions. Use when Creating git commits after code changes, Writing commit messages for staged changes, or Following conventional commit format for the project
skillSlug: commit-message
phases: [E, C]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

1. Review staged changes with `git diff --staged` and `git status`.
2. Run `git log --oneline -10` to match this repo's existing style - recent history shows short, descriptive
   subjects without a strict `type(scope):` prefix (e.g. "adjust on live signals and execution engine",
   "Initial release: Complete 7-phase Polymarket").
3. Identify which pipeline phase(s) or component(s) changed (e.g. signal processors, execution/risk engine,
   TradingView webhook, Redis control plane, monitoring).
4. Write a concise, imperative subject line summarizing the change at that level (e.g. "fix: enforce $1 cap on
   webhook trades" or "adjust DeribitPCR processor confidence scaling").
5. If the change affects runtime behavior (Redis keys, `.env` vars, dry-run/live dispatch, risk limits), mention
   that explicitly in the body so it's clear from `git log` alone.
6. Never include secrets, `.env` contents, or wallet keys in the commit message or diff.

## Examples

**Bug fix matching existing repo style:**
```
fix: enforce TradingView signal TTL before webhook trade execution

Signals older than TRADINGVIEW_SIGNAL_TTL_SECONDS (30s) were being
queued but not rejected, allowing stale UP/DOWN alerts to trigger
trades after a tunnel delay.
```

**Feature commit:**
```
feat: add Deribit PCR signal processor to fusion engine

Wires DeribitPCRProcessor into SignalFusionEngine with a 10% default
weight alongside the other minor signals.
```

## Quality Bar

- Use imperative mood ("add", "fix", "adjust"), matching this repo's existing commit history.
- Keep the subject line short and scannable; use the body for "why" when the change affects trading behavior,
  risk limits, or Redis/env contracts.
- Never mention `POLYMARKET_PK` or other secret values, even as examples of what changed.
- One logical change per commit - don't bundle an unrelated refactor with a risk-engine fix.
- Only commit when explicitly asked, and never use `--no-verify` to bypass hooks.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
