# Azure Dev Tunnels

Use a **dedicated persistent tunnel with exactly one port: 8769**. The control
dashboard remains on loopback port 8768. Do not reuse a tunnel that exposes another
application or publish the dashboard.

The tunnel receives HTTPS and forwards HTTP to the restricted local ingress.
ADO cannot complete an interactive tunnel sign-in, so network-level anonymous
access is needed. The webhook still requires its separate application credential.
See [Microsoft's quick start](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/get-started)
and [CLI reference](https://learn.microsoft.com/en-us/azure/developer/dev-tunnels/cli-commands).
Commands/options are version-sensitive; check `devtunnel --help` after installing.

## 1. Install and sign in on the hosting workstation

Install the official Linux CLI inside WSL. Download it into ignored local tooling,
not a synchronized folder, then inspect its help before use:

```bash
mkdir -p .tools
curl --fail --location https://aka.ms/TunnelsCliDownload/linux-x64 --output .tools/devtunnel
chmod 700 .tools/devtunnel
.tools/devtunnel --help
.tools/devtunnel user login -d
```

Select Microsoft's appropriate download for another CPU/OS. Device-code sign-in is
user-controlled; do not paste its codes or tokens into a chat. Use the same account
and environment when hosting later. The CLI manages its own cached sign-in.

## 2. Create the named tunnel and its single port

```bash
.tools/devtunnel create agent-desk --allow-anonymous --host-header 127.0.0.1
```

Copy the full tunnel ID returned, including its cluster suffix. Replace the
placeholder in these commands:

```bash
.tools/devtunnel port create <full-tunnel-id> \
  --port-number 8769 --protocol http --host-header 127.0.0.1
.tools/devtunnel show <full-tunnel-id> --json
```

Confirm there is only one HTTP port, 8769. Rewriting the upstream Host to loopback
is required by the application's host boundary. Do not pass public Host/Origin
headers through to the control service unchanged.

## 3. Start the restricted ingress and host the tunnel

Keep Agent Desk running in one terminal. Start ingress in another:

```bash
uv run agent-control-ingress --port 8769 --control-url http://127.0.0.1:8768
```

Then host the dedicated tunnel in a third:

```bash
.tools/devtunnel host <full-tunnel-id>
```

Record the port-specific HTTPS origin it prints, for example
`https://<generated-host>-8769.<cluster>.devtunnels.ms`. Obtain the current `portUri`
with `devtunnel show <full-tunnel-id> --json` after hosting if needed. A tunnel ID and
its generated hostname need not be identical.

## 4. Save private configuration

From the project directory:

```bash
mkdir -p .local
chmod 700 .local
cp examples/tunnel.example.json .local/tunnel.json
chmod 600 .local/tunnel.json
```

Edit the file locally: replace `id`, `url` and `executable` with the full tunnel ID,
actual HTTPS origin and absolute CLI path (`realpath .tools/devtunnel`). Keep `port`
equal to 8769. This file contains no access token; it still belongs in ignored local
state. The runtime validates the saved resource against the remote tunnel before hosting.

The Connections page shows saved configuration, not a continuous reachability check.
Changing a hostname later also requires checking the ADO subscription URL; the app
does not silently rewrite unrelated or conflicting service hooks.

## 5. Verify the public boundary

```bash
curl --fail https://<public-origin>/healthz
curl --output /dev/null --write-out '%{http_code}\n' https://<public-origin>/api/state
curl --output /dev/null --write-out '%{http_code}\n' \
  --request POST https://<public-origin>/webhooks/ado
```

Expected: health **200**, control API **404**, unauthenticated webhook **401**.
Replace `<public-origin>` with the hostname only; do not add another `https://`.
The only public routes are health and the authenticated webhook. Never expose
`/api`, `/agent`, dashboard assets or approvals through another forwarding rule.

Keep any tunnel inspection interface restricted to the owner. It may contain
authentication headers or private event data. Do not publish inspector captures.

Continue with [ADO service-hook setup](azure-devops.md). For persistent hosting,
use the three separate [systemd user services](operations.md).

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Public URL asks ADO to sign in | Anonymous network access is enabled on the dedicated tunnel |
| Host rejected / HTTP 403 | Port Host rewrite is `127.0.0.1` or `localhost` |
| HTTP 502 | Control service and ingress are listening on 8768 and 8769 |
| Webhook HTTP 401 | Basic password is Agent Desk's webhook secret, not the ADO PAT |
| Runtime refuses to host | Saved ID, origin, single-port topology and HTTP protocol match `devtunnel show` |
| Tunnel stopped after some time | Workstation/WSL availability, CLI login, tunnel expiry and service logs |

Dev Tunnels is a development transport. Availability depends on your workstation,
network, sign-in and Microsoft's service; it is not an always-on hosted review service.
