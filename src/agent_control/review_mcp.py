"""Read-only MCP tools scoped to controller-selected review worktrees and commits."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path, PurePosixPath


class ReviewReader:
    def __init__(self, context):
        self.context = context
        self.roots = {name: Path(path).resolve() for name, path in context["roots"].items()}

    def root(self, name):
        if name not in self.roots:
            raise ValueError("Repository is outside this agent's assigned worktrees")
        return self.roots[name]

    def path(self, repository, value):
        relative = PurePosixPath(value)
        sensitive = {".git", ".local", ".codex", ".ssh", ".aws", ".azure", ".venv", "node_modules"}
        if (
            not value
            or relative.is_absolute()
            or ".." in relative.parts
            or sensitive.intersection(relative.parts)
        ):
            raise ValueError("Use a source-file path inside the assigned worktree")
        name = relative.name.lower()
        if (
            name in {"auth.json", "secrets.json", "credentials", "credentials.json"}
            or Path(name).suffix in {".key", ".pem", ".pfx", ".p12", ".keystore"}
            or (name.startswith(".env") and not name.endswith(("example", "sample", "template")))
        ):
            raise ValueError("Credential files are not available to the reviewer")
        root = self.root(repository)
        path = (root / str(relative)).resolve()
        if not path.is_relative_to(root):
            raise ValueError("Source-file symlinks must stay inside the assigned worktree")
        return path

    def git(self, repository, *args):
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root(repository)), *args],
                check=True,
                capture_output=True,
                timeout=20,
            )
            if len(result.stdout) > 1_000_000:
                return (
                    result.stdout[:150_000].decode(errors="replace")
                    + "\n[Output truncated; narrow the request]"
                )
            return result.stdout.decode(errors="replace")
        except (OSError, subprocess.SubprocessError):
            raise ValueError("The requested Git read could not be completed") from None

    def call(self, name, args):
        if name == "review_context":
            return json.dumps(self.context, indent=2)
        repository = args["repository"]
        if name == "list_files":
            match = args.get("contains", "")
            if not isinstance(match, str) or len(match) > 160:
                raise ValueError("Use a short filename filter")
            files = self.git(repository, "ls-files", "-z").split("\0")
            return "\n".join(p for p in files if p and match.lower() in p.lower())[:100_000]
        if name == "read_file":
            path = self.path(repository, args["path"])
            if not path.is_file() or path.stat().st_size > 512_000:
                raise ValueError("Select a text source file smaller than 512 KB")
            value = path.read_bytes()
            if b"\0" in value:
                raise ValueError("Binary source files are not available as text")
            start, count = args.get("start_line", 1), args.get("line_count", 250)
            if (
                not isinstance(start, int)
                or not isinstance(count, int)
                or start < 1
                or not 1 <= count <= 500
            ):
                raise ValueError("Use a positive start line and at most 500 lines")
            lines = value.decode(errors="replace").splitlines()
            return "\n".join(
                f"{i + 1}: {line}"
                for i, line in enumerate(lines)
                if start - 1 <= i < start - 1 + count
            )[:150_000]
        facts = self.context.get("facts", {})
        if repository != facts.get("repository_name"):
            raise ValueError("Pinned diff reads are available only for this PR's repository")
        if name == "git_diff":
            mode = args.get("view", "summary")
            if mode not in {"summary", "patch"}:
                raise ValueError("Choose summary or patch")
            path = args.get("path", "")
            if path:
                self.path(repository, path)
            flags = ["--stat", "--name-status"] if mode == "summary" else ["--unified=4"]
            return self.git(
                repository,
                "diff",
                "--no-ext-diff",
                "--no-textconv",
                *flags,
                facts["target"],
                facts["head"],
                "--",
                *([path] if path else []),
            )[:150_000]
        if name == "git_show":
            self.path(repository, args["path"])
            side = args.get("side", "target")
            if side not in {"source", "target"}:
                raise ValueError("Choose source or target")
            return self.git(
                repository,
                "show",
                f"{facts['head' if side == 'source' else 'target']}:{args['path']}",
            )[:150_000]
        raise ValueError("Unknown read tool")

    def tools(self):
        repository = {"type": "string", "enum": list(self.roots)}
        definitions = [
            (
                "review_context",
                "Read the controller-verified PR revisions and assigned worktrees.",
                {},
                [],
            ),
            (
                "list_files",
                "List tracked source paths in an assigned worktree.",
                {"repository": repository, "contains": {"type": "string"}},
                ["repository"],
            ),
            (
                "read_file",
                "Read a bounded, numbered text-file excerpt from an assigned worktree.",
                {
                    "repository": repository,
                    "path": {"type": "string"},
                    "start_line": {"type": "integer"},
                    "line_count": {"type": "integer"},
                },
                ["repository", "path"],
            ),
            (
                "git_diff",
                "Read this PR's pinned source/target diff, optionally narrowed to a file.",
                {
                    "repository": repository,
                    "view": {"type": "string", "enum": ["summary", "patch"]},
                    "path": {"type": "string"},
                },
                ["repository"],
            ),
            (
                "git_show",
                "Read a file at the pinned source or target commit.",
                {
                    "repository": repository,
                    "path": {"type": "string"},
                    "side": {"type": "string", "enum": ["source", "target"]},
                },
                ["repository", "path"],
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
                    "readOnlyHint": True,
                    "destructiveHint": False,
                    "openWorldHint": False,
                },
            }
            for name, description, properties, required in definitions
        ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True, type=Path)
    args = parser.parse_args()
    reader = ReviewReader(json.loads(args.context.read_text()))
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "id" not in request:
            continue
        method = request.get("method")
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": request.get("params", {}).get(
                        "protocolVersion", "2025-06-18"
                    ),
                    "serverInfo": {"name": "agent-desk-review-reads", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": reader.tools()}
            elif method == "tools/call":
                params = request["params"]
                try:
                    value = reader.call(params["name"], params.get("arguments", {}))
                    result = {"content": [{"type": "text", "text": value}]}
                except (ValueError, KeyError, TypeError, OSError):
                    result = {
                        "isError": True,
                        "content": [
                            {
                                "type": "text",
                                "text": "Read denied or unavailable. Use an assigned repository, ordinary source-file path and supported line range.",
                            }
                        ],
                    }
                descriptor = os.open(
                    args.context.parent / "read-audit.jsonl",
                    os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                    0o600,
                )
                with os.fdopen(descriptor, "a") as audit:
                    audit.write(
                        json.dumps(
                            {
                                "tool": params["name"],
                                "view": params.get("arguments", {}).get("view"),
                                "ok": not result.get("isError", False),
                            }
                        )
                        + "\n"
                    )
            elif method == "ping":
                result = {}
            else:
                raise ValueError
            response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        except (ValueError, KeyError, TypeError):
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32601, "message": "Unsupported read request"},
            }
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
