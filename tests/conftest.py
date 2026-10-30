import copy

import pytest
from fastapi.testclient import TestClient

from agent_control.app import create_app
from agent_control.models import Agent

THREAD = "11111111-1111-4111-8111-111111111111"


class FakeAdo:
    pr = {
        "pullRequestId": 42,
        "status": "active",
        "isDraft": False,
        "createdBy": {"id": "external-author"},
        "repository": {"id": "repo-id", "name": "example", "project": {"id": "project-id"}},
        "lastMergeSourceCommit": {"commitId": "a" * 40},
        "lastMergeTargetCommit": {"commitId": "b" * 40},
        "lastMergeCommit": {"commitId": "c" * 40},
    }
    ready = True

    def __init__(self, settings, pat):
        self.settings = settings

    def test(self):
        return {"project": "example", "project_id": "project-id", "identity": "own-author"}

    def candidates(self, event):
        return [copy.deepcopy(self.pr)]

    def ci(self, pr):
        return self.ready, "CI ready" if self.ready else "Waiting for CI"

    def pull_request(self, repo, number):
        return copy.deepcopy(self.pr)

    def close(self):
        pass


class FakeCodex:
    sent = []
    failure = None

    def __init__(self, command):
        pass

    def diagnose(self):
        return {"ready": True, "cli": True, "detail": "Synthetic test connection"}

    def send(self, thread, text):
        if self.failure:
            raise self.failure
        self.sent.append((thread, text))


@pytest.fixture
def world(tmp_path):
    ado = type("IsolatedAdo", (FakeAdo,), {"pr": copy.deepcopy(FakeAdo.pr), "ready": True})
    codex = type("IsolatedCodex", (FakeCodex,), {"sent": [], "failure": None})
    app = create_app(
        tmp_path / "state", tmp_path, start_worker=False, ado_factory=ado, codex_factory=codex
    )
    store = app.state.store
    settings = store.settings()
    settings.review_grace_seconds = 0
    settings.ado.organization = "example"
    settings.ado.project = "example"
    settings.ado.own_identity = "own-author"
    settings.ado.enabled = True
    store.save_settings(settings)
    app.state.vault.set("ado_pat", "synthetic-not-a-real-pat")
    for name, peer in [("review", "development"), ("development", "review")]:
        store.save_agent(
            Agent(
                id=name,
                name=name,
                role=name,
                thread_id=THREAD,
                enabled=True,
                review_on_ci=name == "review",
                peers=[peer],
            )
        )
    http = TestClient(app)
    headers = {"X-Control-Token": app.state.vault.get("ui")}
    return app, http, headers, ado, codex
