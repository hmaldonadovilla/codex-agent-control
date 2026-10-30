# Changelog

## Unreleased

- Steer relevant messages into existing active desktop chats through a versioned native IPC adapter; retain queue fallback for idle chats.
- Coalesce outstanding review notifications, expose authenticated inbox claiming, retire stale requests and withdraw owned native queue receipts.
- Preserve completed assessments when delayed duplicate reports arrive.


## 0.1.0 — 2026-10-30

- Local agent registry, dashboard, durable event queue and human approval ledger.
- Azure DevOps PR/build-policy validation and author/repository filters.
- Webhook-only ingress, dedicated Azure Dev Tunnels setup and service-hook registration.
- Native queue delivery to existing Codex desktop chats, with matching delivery receipts.
- MCP context, run inspection, reporting, proposals and bounded peer communication.
- Registered isolated worktrees and WSL systemd user services.
- Thirty-second review grace period, main/release-build filtering, repeated PR-state
  checks and a desktop start guard for reviews merged while queued.
- MIT license, public setup/operations/security documentation and synthetic GitHub CI.
