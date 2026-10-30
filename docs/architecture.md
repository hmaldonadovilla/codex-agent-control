# Architecture and limits

The supported path uses existing desktop chats as agents. Agent Desk handles control,
events, persistence and coordination; it does not host a replacement chat frontend.

| Module | Responsibility |
| --- | --- |
| `models.py` | Validated configuration, profiles, proposals and input shapes |
| `store.py` | SQLite queue, deduplication, audit and private local state |
| `adapters.py` | Authoritative ADO reads and native CLI queue delivery |
| `service.py` | Eligibility, CI/revision checks, start guard, dispatch and approval scope |
| `app.py` / `ingress.py` | Loopback control API/UI and separate restricted public listener |
| `hooks.py` / `runtime.py` | Explicit service-hook setup, dedicated tunnel checks and user services |
| `coordination_mcp.py` / `agent_client.py` | Scoped agent context, reports, proposals and peer messages |
| `static/` | Buildless control interface, not a conversation editor |

## Event and review lifecycle

Authenticated events are bounded, normalized and persisted before acknowledgement.
The event worker waits on a local signal. It resolves identified PR-validation builds,
not arbitrary event URLs or instructions. Main/release builds are excluded. Incomplete
events do not trigger a project-wide scan.

Author, draft, repository, required-build and exact-revision checks precede queueing.
Job identity includes agent/repository/PR/source/target; duplicate notifications do
not produce duplicate review jobs. A grace deadline is calculated from the persisted
creation time, so restart does not replace it with scheduled model polling. Due reviews
are revalidated before delivery; immediate messages/actions are not blocked behind them.

Native delivery requires a matching `Queued message <id> for thread <uuid>` receipt.
This establishes **queued**, not **review completed**. The agent reports its actual
outcome under its delivered run ID. Before analysis it rechecks the controller's
live `review_gate`; a merged, ineligible or changed PR is skipped.

The narrow interval between checking an active PR and native acceptance cannot be
eliminated transactionally across ADO and Codex. The installed queue command has no
withdrawal option. Consequently a stale message can still cause a brief desktop wake;
the start guard prevents proceeding into a stale review. Future changes must preserve
this distinction rather than claiming exactly-once agent execution.

## Approvals and peers

Automatic review grants no external-write authority. An exact human-approved proposal
creates an action run. Its originating PR facts remain traceable through peer-message
ancestry and must still be current before approval and dispatch. Changed or completed
PRs invalidate stale actions. Disconnected/paused targets get a manual handoff.

Agent keys are separate from the UI credential and cannot reach the human approval API.
Own-run checks, peer allowlists, content deduplication and hop budgets constrain
coordination, but trusted same-user profiles are not a multi-user security boundary.
See [SECURITY.md](../SECURITY.md).

## Failure behavior

- `blocked`: known pre-delivery connection failure; a human can request a retry.
- `unknown`: delivery may have happened; inspect the original chat and reconcile first.
- `superseded`: PR state/revisions no longer satisfy delivery conditions.
- `queued_in_codex`: native acceptance recorded; actual assessment not yet confirmed.
- `reviewed`: actual report received, including a skipped or blocked assessment.
- `awaiting_handoff`: an approved action requires manual delivery.

No automatic resend follows an uncertain outcome. Credentials and raw provider responses
are not included in public error messages. Manual/optional reconciliation reads ADO;
it is not a recurring AI queue-check job.

## Legacy code and non-goals

`owned.py`, `review_mcp.py` and `mcp_probe.py` retain an earlier experimental managed
runtime/probe path and tests. Historical owned-session mappings are not migrated into
desktop chats. Normal UI setup and native delivery do not use those paths. They are
not a replacement for a working native desktop queue connection.

The project does not implement a cloud review farm, multi-tenant isolation, durable
offline cloud ingress, production deployment approval or a direct OpenAI API client.
It does not infer that local test acceptance proves cloud/production readiness.
