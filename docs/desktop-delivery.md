# Desktop steering and the review inbox

Agent Desk links each agent to one existing Codex desktop chat UUID. It never
creates a chat or launches an independent app-server to deliver work.

## Delivery behavior

1. A successful ADO service-hook event is persisted and independently checked in
   ADO. Only an active, eligible PR at its current CI-qualified source and target
   gets a review run. Merges and non-PR builds do not create reviews.
2. A desktop IPC client discovers the running owner of the linked chat. A relevant
   message steers its active turn through the owner's native steering operation.
   The desktop resolves and checks the active turn; Agent Desk does not interrupt it.
3. If the owner explicitly reports an ended/idle turn, or cannot be discovered
   before admission, the installed `codex queue --thread ...` provides a fallback
   for the same UUID. A timeout, malformed receipt or ambiguous steering result
   does **not** fall back or automatically retry.
4. One outstanding review notification is allowed per agent. Additional PRs stay
   in Agent Desk's durable inbox. Peer messages can still steer the active work.
   Exact agent/PR/source/target duplicates retain the original run, including its
   completed result.
5. At useful work boundaries and before finishing, agents call MCP `next_run` or
   CLI `next-run`. This atomically claims retained work without another native
   prompt. Stop when `run` is null. This is explicit inbox draining, not scheduled
   model polling. A review still requires its live `review_gate` before work.
6. Events revalidate retained reviews, including already-delivered requests. A
   merged, abandoned, draft, excluded or changed PR retires the stale request.
   Completed reports are immutable to duplicate agent reports, so a delayed skip
   cannot replace the real assessment. A concurrent completion wins over stale
   retirement.
7. Native receipt IDs and linked UUIDs are stored separately from run reports.
   Completion, supersession or an explicit inbox claim removes only that exact
   Agent Desk-owned queued copy. Messages already consumed are treated as absent.
   Human messages and unrelated queue entries are never cleared. Unavailable
   cleanup stays visible as `cleanup_pending`, retried on the next event or an
   explicit **Dispatch next**; there is no background model check.

The ADO event path and agent reporting wake the worker. A one-shot grace timer
remains available to avoid starting a review immediately before a merge. Neither
steering nor this inbox requires scheduled Codex wake-ups or changes to CI YAML.

## Setup and capability checks

Install Node.js 22 alongside the existing signed-in Codex desktop/CLI. On Windows
with Agent Desk in WSL, a Windows `node.exe` must reach the Windows named pipe.
Agent Desk auto-detects it with Windows `Get-Command node.exe`. Alternatively set
**Connections > Desktop bridge Node executable** to its WSL-accessible path,
for example `/mnt/c/Users/you/tools/node/node.exe`. Linux uses `node` from PATH and
the owner-only `$CODEX_HOME/ipc/ipc.sock` (default `~/.codex/ipc/ipc.sock`).

**Test agent** performs read-only owner discovery and reports steering capability
separately from native CLI queue readiness. It sends no test message. Queue-only
mode still works if Node/the owner is unavailable. A missing shared-daemon control
socket does not disable native queuing and is not a reason to create another session.

This IPC adapter is **experimental and version-dependent**. It uses the installed
desktop's versioned owner-discovery, follower-steer and single-message-removal
operations (Windows named pipe `codex-ipc`). The first verified Windows build was
Codex desktop `26.1023.6621.0` with CLI `0.165.0-alpha.1.2`. This is not a stable
public HTTP/MCP endpoint. Protocol upgrades may disable steering/cleanup; capability
checks and matching native receipts must remain in place. The adapter exposes no
arbitrary RPC, configuration, approval, thread creation or shell execution method.
Do not expose the pipe, Node helper or controller UI through the dev tunnel.

## Agent tools

```sh
python -m agent_control.agent_client --credentials .local/agents/review.json next-run
```

This returns `{"run": null}` or one actual run with its ID, prompt, registered
context and (for reviews) live gate. Report that run's real outcome normally.
Credentials remain owner-only. Agent authentication can claim/report only its own
profile's work; existing peer allowlists and hop limits still apply. Agent messages
remain information, not human authorization.

After upgrading Agent Desk, running chats can use this CLI immediately. An already
loaded MCP tool catalogue may require the normal client refresh before `next_run`
appears; the CLI fallback does not require a Codex restart.

## Validation and operational limits

Tests cover real Node framing against a synthetic owner socket, active/idle/racing
turn outcomes, matching receipts, targeted deletion, disconnections, multiple PRs,
peer delivery, atomic worker/inbox claims and immutable completed reports. CI never
sends to a live desktop chat or changes ADO hooks. A real deployment capability
check is distinct from a real steering receipt.

A queued copy created by an older release has no structured delivery attribution;
its report field may also have overwritten the original receipt. Agent Desk cannot safely identify or withdraw
such copies. Do not edit Codex's private databases to remove them. Use the desktop
queue controls if necessary; old run preflight still suppresses repeated reviews.
An accepted steer is already input to the turn and cannot be withdrawn like an
unconsumed queued message. Review gates and exact revisions remain required before
any review/approval action.
