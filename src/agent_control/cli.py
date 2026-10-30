"""Local launch and parameterized setup; no account-specific data in source."""

import argparse
import fcntl
import os
from contextlib import contextmanager
from pathlib import Path

import uvicorn

from .app import create_app
from .models import Agent
from .store import Store


@contextmanager
def instance_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(directory / "server.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Agent Desk is already running with this state directory") from None
        yield
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description="Local Codex Agent Control")
    parser.add_argument("--state", type=Path, default=Path(".local"))
    parser.add_argument("--workspace-root", type=Path, default=Path.cwd().parent)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--codex")
    parser.add_argument("--review-thread")
    parser.add_argument("--development-thread")
    parser.add_argument("--organization")
    parser.add_argument("--project")
    args = parser.parse_args()
    with instance_lock(args.state):
        run(args)


def run(args):
    store = Store(args.state)
    settings = store.settings()
    if args.codex:
        settings.codex_command = args.codex
    if args.organization:
        settings.ado.organization = args.organization
    if args.project:
        settings.ado.project = args.project
    store.save_settings(settings)
    existing = {a.id for a in store.agents()}
    for name, title, role, thread, peer in [
        ("review", "PR reviewer", "reviewer", args.review_thread, "development"),
        ("development", "Development", "developer", args.development_thread, "review"),
    ]:
        if thread and name not in existing:
            store.save_agent(
                Agent(
                    id=name,
                    name=title,
                    role=role,
                    thread_id=thread,
                    review_on_ci=role == "reviewer",
                    peers=[peer],
                )
            )
    uvicorn.run(
        create_app(args.state, args.workspace_root),
        host="127.0.0.1",
        port=args.port,
        access_log=False,
    )


if __name__ == "__main__":
    main()
