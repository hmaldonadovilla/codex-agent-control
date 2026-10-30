"""Persistent WSL services and a narrowly scoped Dev Tunnel host."""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def load_tunnel(directory: Path) -> dict | None:
    path = directory / "tunnel.json"
    if not path.exists():
        return None
    value = json.loads(path.read_text())
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{2,127}", value["id"]):
        raise ValueError("Invalid dedicated tunnel ID")
    url = urlsplit(value["url"])
    if (
        url.scheme != "https"
        or not url.hostname
        or not url.hostname.endswith(".devtunnels.ms")
        or url.username
        or url.password
        or url.port not in {None, 443}
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or value["port"] != 8769
    ):
        raise ValueError("Use a dedicated HTTPS Dev Tunnel for ingress port 8769")
    if not Path(value["executable"]).is_absolute():
        raise ValueError("The Dev Tunnels executable needs an absolute path")
    return value


def tunnel_status(directory: Path) -> dict:
    value = load_tunnel(directory)
    return (
        {"configured": True, "id": value["id"], "url": value["url"], "port": value["port"]}
        if value
        else {"configured": False}
    )


def validate_remote_tunnel(value: dict, remote: dict):
    tunnel = remote.get("tunnel", remote)
    ports = tunnel.get("ports", [])
    if (
        tunnel.get("tunnelId") != value["id"]
        or len(ports) != 1
        or ports[0].get("portNumber") != 8769
        or ports[0].get("protocol") != "http"
        or ports[0].get("portUri", "").rstrip("/") != value["url"].rstrip("/")
    ):
        raise ValueError("Dedicated tunnel changed; it must expose only ingress port 8769")


def unit_quote(value: str | Path) -> str:
    value = str(value)
    if any(c in value for c in "\r\n\0"):
        raise ValueError("Invalid systemd argument")
    return (
        '"'
        + value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
        + '"'
    )


def service_units(project: Path, directory: Path, workspace_root: Path) -> dict[str, str]:
    python = project / ".venv/bin/python"

    def service(description, command, dependency=""):
        return (
            f"[Unit]\nDescription={description}\nPartOf=agent-desk.target\n"
            + (f"After={dependency}\nWants={dependency}\n" if dependency else "")
            + f"\n[Service]\nType=simple\nWorkingDirectory={str(project).replace('%', '%%')}\n"
            + "ExecStart="
            + " ".join(unit_quote(v) for v in [python, *command])
            + "\nRestart=on-failure\nRestartSec=10\nUMask=0077\n\n[Install]\nWantedBy=agent-desk.target\n"
        )

    return {
        "agent-desk-control.service": service(
            "Agent Desk local control",
            ["-m", "agent_control.cli", "--state", directory, "--workspace-root", workspace_root],
        ),
        "agent-desk-ingress.service": service(
            "Agent Desk restricted webhook ingress",
            ["-m", "agent_control.ingress"],
            "agent-desk-control.service",
        ),
        "agent-desk-tunnel.service": service(
            "Agent Desk dedicated Azure Dev Tunnel",
            ["-m", "agent_control.runtime", "host", "--state", directory],
            "agent-desk-ingress.service",
        ),
        "agent-desk.target": (
            "[Unit]\nDescription=Agent Desk event receiver\n"
            "Wants=agent-desk-control.service agent-desk-ingress.service agent-desk-tunnel.service\n"
            "\n[Install]\nWantedBy=default.target\n"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "host"])
    parser.add_argument("--state", type=Path, default=Path(".local"))
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd().parent)
    args = parser.parse_args()
    directory = args.state.resolve()
    value = load_tunnel(directory)
    if not value:
        parser.error("Configure the dedicated tunnel in the private state directory first")
    if args.action == "host":
        result = subprocess.run(
            [value["executable"], "show", value["id"], "--json"],
            capture_output=True,
            timeout=45,
        )
        if result.returncode:
            sys.exit("Cannot read the dedicated tunnel; check Dev Tunnels sign-in")
        validate_remote_tunnel(value, json.loads(result.stdout))
        os.execv(value["executable"], [value["executable"], "host", value["id"]])
    else:
        project = Path(__file__).resolve().parents[2]
        units = service_units(project, directory, args.workspace_root.resolve())
        target = Path.home() / ".config/systemd/user"
        target.mkdir(parents=True, exist_ok=True)
        # Never overwrite another service or a locally customized unit silently.
        for name, content in units.items():
            path = target / name
            if path.is_symlink() or (path.exists() and path.read_text() != content):
                parser.error(f"Existing service differs: {path}")
        for name, content in units.items():
            path = target / name
            path.write_text(content)
            path.chmod(0o600)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        print("Services installed. Start with: systemctl --user enable --now agent-desk.target")


if __name__ == "__main__":
    main()
