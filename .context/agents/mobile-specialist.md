---
type: agent
name: Mobile Specialist
description: Develop native and cross-platform mobile applications
agentType: mobile-specialist
phases: [P, E]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---

## Mission

This repository is a headless Python trading bot with no mobile application or mobile codebase. This agent has no
active surface in this project today. It is documented here so that, if a future mobile companion app (e.g. a
push-notification client for trade alerts or a remote sim/live toggle) is proposed, there is a clear pointer to
where that decision should be made and what existing operator-facing surfaces it would need to integrate with.

## Responsibilities

- None at present - there is no mobile codebase in this repository.
- If a mobile feature is proposed (e.g. push notifications for fills, remote `redis_control.py`-equivalent
  toggles), escalate to `architect-specialist` first: it would need a secure API surface in front of the Redis
  control plane and Polymarket credentials, which does not currently exist.

## Best Practices

- Do not add mobile project scaffolding (e.g. React Native, Flutter, Swift/Kotlin projects) without an explicit,
  scoped request - this is a single-operator, headless bot.
- If asked to expose bot state/controls to a mobile client, treat it as a security-sensitive change: any new
  network-exposed endpoint touching `btc_trading:*` Redis keys or trading credentials must go through
  `security-auditor` and `architect-specialist` first.

## Key Project Resources

- [CLAUDE.md](../../CLAUDE.md)
- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/security.md](../docs/security.md)

## Repository Starting Points

- None - no mobile-related directories exist in this repository.

## Key Files

- None - if mobile work is scoped, the relevant integration points would be `redis_control.py` (state/control) and
  `monitoring/grafana_exporter.py` (read-only metrics).

## Key Symbols for This Agent

- [`display_status`](../../redis_control.py) @ redis_control.py:107 - current operator-facing state surface a
  mobile client might need to mirror
- [`get_grafana_exporter`](../../monitoring/grafana_exporter.py) @ grafana_exporter.py:408 - existing read-only
  metrics surface

## Documentation Touchpoints

- [.context/docs/architecture.md](../docs/architecture.md)
- [.context/docs/security.md](../docs/security.md)

## Collaboration Checklist

1. Confirm with the user/operator that a mobile surface is genuinely needed before scaffolding anything.
2. Route any proposal through `architect-specialist` (new API surface) and `security-auditor` (credential
   exposure) before implementation.
3. Do not duplicate `redis_control.py` logic in a mobile client without a proper API boundary.

## Hand-off Notes

If this agent is ever engaged, document the proposed mobile surface, what backend API it would require, and the
security review outcome before any implementation begins.
