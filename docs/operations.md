# Operations

## Foreground processes

Run from the checkout so the default private state is the expected `.local/`:

```bash
uv run agent-control --workspace-root "$HOME/local-repos"
uv run agent-control-ingress --port 8769 --control-url http://127.0.0.1:8768
```

These are separate long-running processes; use separate terminals. Host the dedicated
tunnel in another terminal. Closing the browser does not stop the services.
The systemd installer below uses the standard 8768/8769 ports. A customized foreground
control port also needs an explicit matching ingress URL; the dedicated tunnel helper
currently requires ingress port 8769.

## WSL/systemd user services

Confirm your WSL distribution supports systemd and its user session is healthy:

```bash
systemctl --user status
```

If this fails with a bus/session error, repair the user session or use the foreground
commands. Do not restart all WSL workloads just to restart Agent Desk. Enabling WSL
systemd can require a distribution restart; schedule it after other work is safe.
Follow [Microsoft's WSL systemd guide](https://learn.microsoft.com/en-us/windows/wsl/systemd).

Configure the private `.local/tunnel.json` first, then install:

```bash
.venv/bin/python -m agent_control.runtime install --workspace-root "$HOME/local-repos"
systemctl --user enable --now agent-desk.target
systemctl --user status agent-desk-control.service agent-desk-ingress.service agent-desk-tunnel.service
```

The installer writes only these `agent-desk` user units and refuses to overwrite
customized definitions. The tunnel service verifies that its saved remote resource
still exposes exactly the expected HTTP port and origin.

```bash
# Pause delivery in the dashboard before restarting; wait for dispatching jobs to finish.
systemctl --user restart agent-desk-control.service
journalctl --user -u agent-desk-control.service -n 50

# Stop or disable only this application's target.
systemctl --user stop agent-desk.target
systemctl --user disable --now agent-desk.target
```

User services start with the Linux user manager. Keeping that manager alive after
logout may require an administrator-approved linger configuration. No Windows startup
task is installed. WSL, the workstation, network, tunnel sign-in and Codex desktop must
remain available; enabling a unit does not prove reachability after a Windows reboot.

## Health and end-to-end checks

```bash
curl --fail http://127.0.0.1:8768/healthz
curl --fail http://127.0.0.1:8769/healthz
```

Check the public health URL and public-route restrictions from the tunnel guide.
Then verify one authorized message to the intended existing chat and its real report.
A successful public HTTP request or CLI version check does not prove agent execution.
Do not send repeated connectivity probes that create unnecessary model usage.

## State, backups and recovery

| Location under `.local/` | Contents |
| --- | --- |
| `state.sqlite3` | Profiles, events, jobs, proposals, audit and historical conversation records |
| `secrets.json` | UI/webhook credentials, ADO PAT and hashed agent keys |
| `agents/` | Owner-only per-agent reporting credentials |
| `tunnel.json` | Dedicated tunnel ID, port origin and CLI path |
| `ado-hook.json` | Non-secret service-hook registration metadata |

Pause automation and stop the application before taking a consistent private backup
of the whole state directory. Protect it separately; Agent Desk does not encrypt it.
Do not upload backups or SQLite files to a public issue or release. Restore file
permissions and original chat/profile bindings before restarting. Never delete the
database as a connection-repair shortcut: it holds deduplication and approval history.

On restart, an interrupted `dispatching` job becomes `unknown` because delivery may
already have happened. Inspect the original chat, then explicitly reconcile delivered
or not delivered in the dashboard. Only known pre-delivery failures can be retried.
No automatic retry follows uncertain native acceptance.

## Updates

1. Pause automation, finish/reconcile dispatching jobs and back up private state.
2. Review the new release and preserve unrelated local edits.
3. Update the checkout, run `uv sync --frozen`, then the documented checks.
4. Restart affected Agent Desk services; do not restart Codex or other projects.
5. Confirm health, unchanged chat/worktree bindings and the intended timing settings.
6. Re-enable automation. Scheduled reconciliation stays disabled unless you enable it.

Do not instantiate `Store` against the live state directory from an ad hoc inspection
script: its startup recovery changes interrupted dispatch states. Use the authenticated
API or a read-only SQLite connection instead.

## Common connection problems

- **CLI found but delivery unavailable:** select a CLI with `queue --thread --message`.
  Check the native queue command, rather than assuming an unrelated daemon is required.
- **Queue receipt but no review:** verify the exact chat UUID, desktop availability,
  sign-in, busy/approval state and start guard. Do not recreate the chat automatically.
- **MCP tools absent:** refresh the same chat's MCP connection or use the reporting CLI.
- **Reports refused:** check the selected profile, real owned/delivered run and current key.
- **Webhook failures:** inspect ADO notification history and local Activity; keep diagnostics
  sanitized. Basic webhook password and ADO PAT are separate credentials.
- **Merged PR review skipped:** expected when a queued message outlives its PR. No new
  code assessment, tests or peer notification are needed.
