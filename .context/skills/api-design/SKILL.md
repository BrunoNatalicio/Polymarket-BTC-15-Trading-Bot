---
type: skill
name: Api Design
description: Design RESTful APIs following best practices. Use when Designing new API endpoints, Restructuring existing APIs, or Planning API versioning strategy
skillSlug: api-design
phases: [P, R]
generated: 2026-06-11
status: filled
scaffoldVersion: "2.0.0"
---
## Workflow

This repository has only one HTTP API surface: `tradingview_webhook_receiver.py`, a stdlib `http.server` on port
8001 that accepts a single `POST /webhook` (or root) endpoint for TradingView alerts. There is no REST resource
model, ORM, or framework (FastAPI/Flask) to extend - "API design" here means extending this single webhook
contract or, rarely, the Prometheus `/metrics` endpoint in `monitoring/grafana_exporter.py`.

1. Confirm whether the change is to the TradingView webhook payload (`WebhookHandler` in
   `tradingview_webhook_receiver.py`) or the Prometheus metrics endpoint (`MetricsHandler` in
   `monitoring/grafana_exporter.py`) - these are the only two HTTP surfaces.
2. For webhook payload changes, update `parse_alert` to handle the new/changed JSON field, keeping
   `validate_secret` as the first check before any parsing.
3. Keep the response contract minimal (plain HTTP status codes + small JSON body) - do not introduce a new
   response envelope or versioning scheme for a single-consumer internal webhook.
4. If adding a new Prometheus metric, follow `GrafanaMetricsExporter`'s existing naming convention
   (`btc_trading_*`) and update `grafana/dashboard.json` if it should be visualized.
5. Update `test_tradingview_webhook.py` (`test_parse_alert`, `test_http_end_to_end`) for any webhook contract
   change.
6. Document the new field/endpoint in [.context/docs/architecture.md](../../docs/architecture.md) and
   [CLAUDE.md](../../../CLAUDE.md) if it changes the documented webhook flow.

## Examples

**TradingView webhook payload (current contract):**
```json
{
  "secret": "shared-secret-from-env",
  "direction": "UP",
  "market": "btc-15m-...",
  "timestamp": 1718000000
}
```

**Webhook response (current contract):**
```
200 OK   -> {"status": "queued"}
401      -> {"error": "invalid secret"}
400      -> {"error": "invalid payload"}
```

## Quality Bar

- Never weaken or bypass `validate_secret` - it is the only authentication on the only externally-reachable
  endpoint in this system.
- Keep the webhook payload backward compatible where possible - TradingView alert templates are configured
  externally and are not version-controlled here.
- New fields should be optional with safe defaults so existing TradingView alert configurations keep working.
- Do not introduce a web framework (FastAPI/Flask/etc.) for a single endpoint - the stdlib `http.server`
  implementation is intentional (see [.context/docs/tooling.md](../../docs/tooling.md)).
- Any new Prometheus metric must be cheap to compute on every `/metrics` scrape - this endpoint is polled
  frequently.

## Resource Strategy

- Add `scripts/` only when the task is fragile, repetitive, or benefits from deterministic execution.
- Add `references/` only when details are too large or too variant-specific to keep in `SKILL.md`.
- Add `assets/` only for files that will be consumed in the final output.
- Keep extra docs out of the skill folder; prefer `SKILL.md` plus only the resources that materially help.
