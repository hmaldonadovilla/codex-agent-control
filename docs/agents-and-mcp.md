# Agents, existing chats and MCP

## Register existing chats

Create the desired chats in Codex yourself, then register their existing UUIDs in
Agent Desk. A native chat link has the form `codex://threads/<uuid>`. Agent Desk does
not create or fork chats for reviews, and it does not replace their histories or models.

A useful starting pair is:

| Profile | Role | Review on CI | Allowed peer |
| --- | --- | --- | --- |
| `review` | Reviewer | Yes | `development` |
| `development` | Developer | No | `review` |

Each profile has instructions, repository filters, allowed peers and separate
workspaces. A third specialist, documentation or security profile uses the same form.
Its tools/profile become discoverable after refreshing the MCP connection if needed.
Do not enable two agents on the same checkout. Human-approved deployment ownership
must also be coordinated separately from source worktrees.

## Private reporting credentials

Click **Connect reporting** on each agent. It writes `.local/agents/<agent-id>.json`
with owner-only permissions and adds reporting instructions to the profile. A key
can report/propose/message only through the agent API; it cannot approve actions.
Rotating it invalidates its previous credential. Never paste its value into a prompt.

The MCP server runs under the same trusted workstation account. It reads the private
credential file internally, never exposing its token in tool arguments/results.
Profile selection is not a cryptographic binding to a chat; see [SECURITY.md](../SECURITY.md).

## Install the stdio MCP server

From a Linux Codex CLI configuration:

```bash
codex mcp add agent_desk -- \
  /absolute/path/codex-agent-control/.venv/bin/python \
  -m agent_control.coordination_mcp \
  --credentials-dir /absolute/path/codex-agent-control/.local/agents
```

For Codex desktop on Windows, register a WSL command using the installed Windows CLI
(replace the distribution and paths):

```powershell
codex mcp add agent_desk -- wsl.exe -d Ubuntu `
  --cd /home/you/local-repos/codex-agent-control -- `
  .venv/bin/python -m agent_control.coordination_mcp `
  --credentials-dir /home/you/local-repos/codex-agent-control/.local/agents
```

Use the actual CLI executable if `codex` is not on PATH. Paths/credential directory
are local configuration, not repository settings. Reopen or refresh the **same**
desktop chats if their currently loaded tool list does not contain Agent Desk.
See the official [Codex CLI reference](https://learn.chatgpt.com/docs/cli/reference).

## Tools

| Tool | Purpose |
| --- | --- |
| `agent_context(agent_id)` | Confirm linked chat UUID, own workspaces and permitted peers |
| `get_run(agent_id, run_id)` | Read an owned run; automatic reviews must first inspect `review_gate.proceed` |
| `report_result(agent_id, run_id, summary)` | Record the actual outcome; a report is not an external-action approval |
| `propose_action(...)` | Submit exact comments, backlog edits or code scope for human approval |
| `send_peer_message(...)` | Send relevant information to an allowed peer from an owned, delivered run |

Before any automatic review reads files or runs tests, get its run. If the live
start guard says `proceed: false`, report that skip and stop. This catches merges or
revision changes after native queue acceptance. An unavailable guard is not permission
to proceed. The controller also checks before enqueue and delivery.

Peer messages have origin ownership checks, content deduplication and a four-hop
budget. A message is context, not permission to edit another agent's checkout or
write to ADO. Avoid acknowledgement-only loops; send substantive changes or blockers.

## Reporting CLI fallback

If MCP tools have not loaded, use this project's environment through WSL:

```bash
uv run python -m agent_control.agent_client --credentials .local/agents/review.json \
  get-run --job <delivered-run-id>

uv run python -m agent_control.agent_client --credentials .local/agents/review.json \
  report --job <delivered-run-id> --file /private/path/assessment.txt

uv run python -m agent_control.agent_client --credentials .local/agents/review.json \
  message --job <delivered-run-id> --target development --file /private/path/context.txt

uv run python -m agent_control.agent_client --credentials .local/agents/review.json \
  propose --job <delivered-run-id> --target development --kind adjust_code \
  --title 'Fix the reviewed defect' --file /private/path/exact-scope.txt
```

Supported proposal kinds: `post_comments`, `update_backlog`, `adjust_code`, `coordinate`.
Keep report/proposal files private; they can contain confidential review content.
Use the actual delivered run ID, never a fabricated ID or another profile's job.

## Human approval and completion

The human reviews the exact proposal in Agent Desk or explicitly approves its scope
in the desktop chat. The approval ledger creates a separate action run. For a connected
desktop target it queues that bounded instruction; paused/disconnected targets get a
manual handoff instead. Current revisions are checked again, including proposals
derived through peer coordination.

Approval and native queue receipts are not completion. The executing chat reports
its actual files, tests, external writes and limitations. The agent API and MCP do not
expose an approval tool. Codex permissions and the human's instructions still apply.

See [Desktop delivery](desktop-delivery.md) for active steering, inbox claiming, native receipt cleanup and compatibility limits.
