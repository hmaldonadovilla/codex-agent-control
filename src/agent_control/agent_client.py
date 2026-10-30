"""Reporting/coordination client with no approval endpoint or human UI token."""

import argparse
import json
import stat
from pathlib import Path
from urllib.parse import urlparse

import httpx


def main():
    parser = argparse.ArgumentParser(description="Agent Desk report and coordination client")
    parser.add_argument("--credentials", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("get-run")
    inspect.add_argument("--job", required=True)
    report = commands.add_parser("report")
    report.add_argument("--job", required=True)
    report.add_argument("--file", type=Path, required=True)
    message = commands.add_parser("message")
    message.add_argument("--target", required=True)
    message.add_argument("--job", required=True)
    message.add_argument("--file", type=Path, required=True)
    proposal = commands.add_parser("propose")
    proposal.add_argument("--target", required=True)
    proposal.add_argument(
        "--kind",
        required=True,
        choices=["post_comments", "update_backlog", "adjust_code", "coordinate"],
    )
    proposal.add_argument("--title", required=True)
    proposal.add_argument("--job")
    proposal.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    if args.credentials.is_symlink() or stat.S_IMODE(args.credentials.stat().st_mode) & 0o077:
        parser.error("Credentials must be an owner-only regular file")
    config = json.loads(args.credentials.read_text())
    url = urlparse(config["base_url"])
    if (
        url.scheme != "http"
        or url.hostname not in {"127.0.0.1", "localhost"}
        or url.username
        or url.password
    ):
        parser.error("Agent reports may be sent only to the local controller")
    method = "POST"
    if args.command == "get-run":
        from uuid import UUID

        method, path, body = "GET", "runs/" + str(UUID(args.job)), None
    elif args.command == "report":
        content = args.file.read_text()
        path, body = "report", {"job_id": args.job, "summary": content}
    elif args.command == "message":
        content = args.file.read_text()
        path, body = (
            "message",
            {"target_agent_id": args.target, "body": content, "parent_job_id": args.job},
        )
    else:
        content = args.file.read_text()
        path, body = (
            "propose",
            {
                "agent_id": config["agent_id"],
                "target_agent_id": args.target,
                "kind": args.kind,
                "title": args.title,
                "job_id": args.job,
                "body": content,
            },
        )
    try:
        response = httpx.request(
            method,
            config["base_url"] + "/agent/" + path,
            json=body,
            headers={"Authorization": "Bearer " + config["token"]},
            timeout=15,
            trust_env=False,
            follow_redirects=False,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        raise SystemExit(
            "Agent Control refused the operation or is unavailable; raw response suppressed"
        ) from None
    result = response.json()
    if args.command == "get-run":
        result = {
            k: result[k] for k in ("id", "kind", "state", "value", "review_gate") if k in result
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
