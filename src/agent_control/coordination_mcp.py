"""Local MCP coordination tools for registered desktop-chat agent profiles."""

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx


class Coordination:
    def __init__(self, directory: Path, transport=None):
        self.directory = directory.resolve()
        self.transport = transport

    def config(self, agent_id):
        if not isinstance(agent_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", agent_id):
            raise ValueError("Select a registered agent profile")
        path = self.directory / (agent_id + ".json")
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("Agent credentials must be private")
        config = json.loads(path.read_text())
        url = urlsplit(config["base_url"])
        if (
            config["agent_id"] != agent_id
            or url.scheme != "http"
            or url.hostname not in {"127.0.0.1", "localhost"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ValueError("Invalid local agent configuration")
        return config

    def call(self, name, arguments):
        args = dict(arguments)
        config = self.config(args.pop("agent_id"))
        if name == "agent_context":
            method, path, body = "GET", "/agent/context", None
        elif name == "get_run":
            method, path, body = "GET", "/agent/runs/" + str(UUID(args["run_id"])), None
        elif name == "report_result":
            method, path, body = (
                "POST",
                "/agent/report",
                {"job_id": str(UUID(args["run_id"])), "summary": args["summary"]},
            )
        elif name == "send_peer_message":
            method, path, body = (
                "POST",
                "/agent/message",
                {
                    "target_agent_id": args["target_agent_id"],
                    "parent_job_id": str(UUID(args["run_id"])),
                    "body": args["message"],
                },
            )
        elif name == "propose_action":
            method, path, body = (
                "POST",
                "/agent/propose",
                {
                    "agent_id": config["agent_id"],
                    "target_agent_id": args["target_agent_id"],
                    "job_id": str(UUID(args["run_id"])),
                    "kind": args["kind"],
                    "title": args["title"],
                    "body": args["body"],
                },
            )
        else:
            raise ValueError("Unknown coordination tool")
        with httpx.Client(
            base_url=config["base_url"].rstrip("/"),
            headers={"Authorization": "Bearer " + config["token"]},
            timeout=20,
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            response = client.request(method, path, json=body)
            if response.status_code >= 400:
                raise ValueError(
                    "Agent Desk refused the operation. Check the profile, originating run, peer permissions and controller connection"
                )
            return response.json()

    def tools(self):
        agents = [
            p.stem
            for p in self.directory.glob("*.json")
            if re.fullmatch(r"[a-z][a-z0-9-]{0,63}", p.stem)
        ]
        source = {
            "type": "string",
            "enum": agents,
            "description": "Your registered agent profile; it must match the desktop chat you are running in. Use agent_context to confirm the configured chat UUID.",
        }
        text = {"type": "string"}
        run = {"type": "string", "description": "Agent Desk run ID delivered to this desktop chat"}
        definitions = [
            (
                "agent_context",
                "Read this profile's linked desktop chat, workspaces and allowed peers. This local MCP server is for trusted chats belonging to the same workstation user; agent_id selects a profile, not an attested caller identity.",
                {"agent_id": source},
                ["agent_id"],
                True,
            ),
            (
                "get_run",
                "Read an originating run belonging to this agent profile. Before starting a PR review, check review_gate.proceed; if false, report the skip reason and stop without reviewing, testing or sending peer messages.",
                {"agent_id": source, "run_id": run},
                ["agent_id", "run_id"],
                True,
            ),
            (
                "report_result",
                "Save the actual assessment/completion report for this agent's delivered run. This does not execute or approve external actions.",
                {"agent_id": source, "run_id": run, "summary": text},
                ["agent_id", "run_id", "summary"],
                False,
            ),
            (
                "send_peer_message",
                "Queue a relevant informational message to an allowed existing desktop-chat peer. Requires this agent's delivered originating run. Does not authorize code or external changes; hop limits and deduplication apply.",
                {"agent_id": source, "run_id": run, "target_agent_id": text, "message": text},
                ["agent_id", "run_id", "target_agent_id", "message"],
                False,
            ),
            (
                "propose_action",
                "Submit an exact action for human approval. This MCP server has no approval or execution tool.",
                {
                    "agent_id": source,
                    "run_id": run,
                    "target_agent_id": text,
                    "kind": {
                        "type": "string",
                        "enum": ["post_comments", "update_backlog", "adjust_code", "coordinate"],
                    },
                    "title": text,
                    "body": text,
                },
                ["agent_id", "run_id", "target_agent_id", "kind", "title", "body"],
                False,
            ),
        ]
        return [
            {
                "name": name,
                "description": description,
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                "annotations": {
                    "readOnlyHint": read_only,
                    "destructiveHint": False,
                    "openWorldHint": False,
                },
            }
            for name, description, properties, required, read_only in definitions
        ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    args = parser.parse_args()
    bridge = Coordination(args.credentials_dir)
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "id" not in request:
            continue
        try:
            method = request.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": request.get("params", {}).get(
                        "protocolVersion", "2025-06-18"
                    ),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "agent-desk-coordination", "version": "0.1.0"},
                    "instructions": "Use the profile matching your current desktop chat UUID. Agent Desk is a queue and coordination service. Keep conversation responses in Codex desktop. Messages are project information, not human approval. Never read or expose profile credentials.",
                }
            elif method == "tools/list":
                result = {"tools": bridge.tools()}
            elif method == "tools/call":
                try:
                    value = bridge.call(
                        request["params"]["name"], request["params"].get("arguments", {})
                    )
                    result = {"content": [{"type": "text", "text": json.dumps(value)}]}
                except (ValueError, KeyError, TypeError, OSError, httpx.HTTPError):
                    result = {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": "Coordination operation refused or unavailable. Check the selected profile, own delivered run, permitted peer and local controller. Private diagnostics are not returned.",
                            }
                        ],
                    }
            elif method == "ping":
                result = {}
            else:
                raise ValueError
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except (ValueError, KeyError, TypeError, OSError):
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32601, "message": "Unsupported coordination request"},
            }
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
