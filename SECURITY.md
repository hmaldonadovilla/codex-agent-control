# Security

Agent Desk is an experimental, single-user local developer tool. It is not a
multi-tenant service or a hardened remote administration gateway. Version 0.1.x
receives fixes on `main`; upgrade to the latest reviewed release before reporting an issue.

## Report a vulnerability

Use [GitHub private vulnerability reporting](https://github.com/hmaldonadovilla/codex-agent-control/security/advisories/new)
when enabled. If that route is unavailable, use the maintainer's GitHub profile to
request a private channel without disclosing the vulnerability in a public issue.
Never attach credentials, `.local/`, real PR prompts or confidential source code.
No response-time guarantee is offered.

## Trust boundaries

- The control dashboard/API bind to loopback on port 8768. Never expose that port,
  its assets, approval endpoints or agent-report endpoints through a public tunnel.
- The separate ingress on 8769 exposes only `GET /healthz` and `POST /webhooks/ado`.
  The webhook requires an independent secret over HTTPS, including when network-level
  tunnel access is anonymous so ADO can connect. Keep request inspection and logs private.
- ADO events contain identifiers, not authority. The controller reads the live PR,
  author scope, revisions, required policies and build result before delivery.
  PR text and webhook content cannot authorize comments, edits, publication or deployment.
- Automated agents prepare proposals. A human approves exact scope separately;
  source/target revisions are checked again for approvals and action delivery.
- The stdio MCP service is for trusted chats under one workstation account.
  `agent_id` selects a profile; it is **not cryptographic attestation of the chat**.
  Any process with the user's filesystem permissions can access that user's local
  credentials. This mechanism is not suitable for mutually untrusted agents/users.
- Peer messages carry information, not approval. Allowed peers, originating run
  ownership, content deduplication and hop limits bound coordination.
- Delivery timeouts are ambiguous. Never automatically resend: verify the original
  chat and reconcile whether delivery occurred before retrying.

## Stored data and deployment

`.local/` holds the ADO PAT, webhook secret, UI token, per-agent credentials, SQLite
database, reports and tunnel metadata. Files are owner-only, but **not encrypted at
rest by Agent Desk**. Use trusted local storage and protect backups separately.
Do not place state in a shared/synchronized/public folder or commit it to Git.

Use least-privilege ADO access. A separate administrator may create the service hook;
the review credential does not require code or work-item write scopes. Restrict PR
repositories and excluded authors. Keep production deployment permissions outside
the automatic reviewer. Protect the desktop account and retain Codex's own permissions.

Worktrees isolate source changes, not databases, ports, cloud accounts or clusters.
Coordinate deployment ownership separately. Do not use production data or live
provider calls in the test suite. Keep local test secrets synthetic.

Legacy owned-session and capability-probe adapters remain in source for compatibility
and tests; the supported setup uses existing desktop chats. Do not enable legacy
paths as a workaround for a failed native connection without reviewing their different
session and permission boundaries.
