# Contributing

Use Python 3.12 on Linux or Ubuntu WSL and keep development files on a native
filesystem. The web interface is buildless HTML, CSS and JavaScript.

```bash
uv sync --frozen
uv run ruff check .
uv run ruff format --check .
uv run pytest
node --check src/agent_control/static/app.js
uv build
```

Use Node.js 22 for the syntax check. Run `uv run ruff format .` to format Python.
Keep `uv.lock` committed. Test documentation commands against the installed CLI;
Codex and Dev Tunnels interfaces can vary by version.

Add behavior tests for changes to event routing, revision freshness, queue timing,
authorization and uncertain-delivery recovery. Inject fake adapters or use HTTPX
mock transports; do not access your real ADO project, tunnel or chats in tests.
Never commit `.local/`, personal profile registrations, logs, credentials or PR data.

Keep agents configurable rather than adding role-specific execution branches.
Preserve existing chats and worktrees. Do not introduce background model polling,
automatic retries after an uncertain send, or automatic approval of external writes.
The public MCP server deliberately has no approval endpoint or arbitrary HTTP tool.

Open an issue for a bug or proposed behavior change. Include platform, relevant CLI
versions, sanitized reproduction steps and expected/actual outcomes. Check that logs
and screenshots contain no private URLs, prompts, user identities or credentials.
Report security issues privately as described in [SECURITY.md](SECURITY.md).

Use focused pull requests and Conventional Commit messages, for example
`fix(events): ignore post-merge main builds`. Explain behavior changes and validation.
Do not include unrelated formatting or local environment changes.

## Release checklist

1. Run the checks above and verify the built wheel/source distribution excludes local state.
2. Check the staged tree and complete Git history for secrets and personal configuration.
3. Review setup docs, security boundaries and the changelog for the release.
4. Publish only the reviewed commit. Confirm public visibility, license detection and CI.
5. Tag the verified commit and create release notes. Do not upload private test evidence.

By contributing, you agree to license your contribution under this repository's MIT license.
