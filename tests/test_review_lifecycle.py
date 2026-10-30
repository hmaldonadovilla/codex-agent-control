import copy
import json
import sys
import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_control import agent_client
from agent_control.adapters import Ado
from agent_control.app import create_app
from agent_control.models import AdoSettings, Agent, Message

from .conftest import THREAD, FakeAdo, FakeCodex


@pytest.mark.parametrize(
    "branch", ["refs/heads/main", "refs/heads/release/v1", "refs/heads/feat/example", ""]
)
def test_non_pr_builds_never_trigger_reviews_even_with_retained_pr_metadata(branch):
    paths = []

    def respond(request):
        paths.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "result": "succeeded",
                "sourceBranch": branch,
                "sourceVersion": "c" * 40,
                "repository": {"id": "repo-id"},
                "triggerInfo": {"pr.number": "42"},
            },
        )

    ado = Ado(
        AdoSettings(organization="example", project="example"),
        "synthetic",
        httpx.MockTransport(respond),
    )
    try:
        assert ado.candidates({"build": 99}) == []
        assert len(paths) == 1  # no PR lookup or project-wide scan
    finally:
        ado.close()


@pytest.mark.parametrize("revision", ["c" * 40, "old-build"])
def test_only_current_pr_validation_builds_can_be_candidates(revision):
    def respond(request):
        if "/build/builds/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "result": "succeeded",
                    "sourceBranch": "refs/pull/42/merge",
                    "sourceVersion": revision,
                    "repository": {"id": "repo-id"},
                },
            )
        assert request.url.path.endswith("/pullRequests/42")
        return httpx.Response(200, json=FakeAdo.pr)

    ado = Ado(
        AdoSettings(organization="example", project="example"),
        "synthetic",
        httpx.MockTransport(respond),
    )
    try:
        assert ado.candidates({"build": 99}) == ([FakeAdo.pr] if revision == "c" * 40 else [])
        assert ado.candidates({"unrelated": True}) == []
    finally:
        ado.close()


def test_merge_during_ci_check_cannot_enqueue_a_review(world, monkeypatch):
    app, _, _, ado, codex = world

    def ci(_self, _pr):
        ado.pr["status"] = "completed"
        return True, "CI passed"

    monkeypatch.setattr(ado, "ci", ci)
    assert app.state.control.reconcile()["jobs"] == []
    assert app.state.store.jobs() == []
    assert codex.sent == []


def test_merge_in_grace_window_suppresses_delivery_without_blocking_peer_messages(world):
    app, _, _, ado, codex = world
    store, control = app.state.store, app.state.control
    settings = store.settings()
    settings.review_grace_seconds = 30
    store.save_settings(settings)
    review = control.reconcile()["jobs"][0]
    assert control.dispatch_one() is False
    assert 0 < store.review_delay(30) <= 30
    message = control.message(Message(target_agent_id="development", body="Human message"))
    assert control.dispatch_one()
    assert store.job(message)["state"] == "queued_in_codex"
    assert store.job(review)["state"] == "pending"
    ado.pr["status"] = "completed"
    with store.db() as db:
        db.execute(
            "UPDATE jobs SET created_at=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(seconds=31)).isoformat(), review),
        )
    assert control.dispatch_one()
    assert store.job(review)["state"] == "superseded"
    assert len(codex.sent) == 1
    assert store.review_delay(30) is None


def test_merge_during_final_delivery_ci_check_is_rejected(world, monkeypatch):
    app, _, _, ado, codex = world
    review = app.state.control.reconcile()["jobs"][0]

    def ci(_self, _pr):
        ado.pr["status"] = "completed"
        return True, "CI passed"

    monkeypatch.setattr(ado, "ci", ci)
    assert app.state.control.dispatch_one()
    assert app.state.store.job(review)["state"] == "superseded"
    assert codex.sent == []


@pytest.mark.parametrize("change", ["none", "merged", "revision", "draft"])
def test_desktop_start_guard_checks_live_state_even_after_native_acceptance(world, change):
    app, http, headers, ado, _ = world
    job = app.state.control.reconcile()["jobs"][0]
    app.state.control.dispatch_one()
    key = http.post("/api/agents/review/report-key", json={}, headers=headers).json()["token"]
    if change == "merged":
        ado.pr["status"] = "completed"
    elif change == "revision":
        ado.pr["lastMergeSourceCommit"]["commitId"] = "d" * 40
    elif change == "draft":
        ado.pr["isDraft"] = True
    auth = {"Authorization": "Bearer " + key}
    response = http.get("/agent/runs/" + job, headers=auth)
    assert response.status_code == 200
    gate = response.json()["review_gate"]
    assert gate["proceed"] is (change == "none")
    assert (
        http.post(
            "/agent/report",
            json={"job_id": job, "summary": "Skipped or completed as observed"},
            headers=auth,
        ).status_code
        == 200
    )
    assert http.get("/agent/runs/" + job, headers=auth).json()["review_gate"]["proceed"] is False


def test_fallback_can_read_start_guard_without_a_report_file(tmp_path, monkeypatch, capsys):
    credentials = tmp_path / "review.json"
    credentials.write_text(
        json.dumps(
            {"base_url": "http://127.0.0.1:8768", "agent_id": "review", "token": "synthetic"}
        )
    )
    credentials.chmod(0o600)
    job = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(
        sys, "argv", ["agent_client", "--credentials", str(credentials), "get-run", "--job", job]
    )

    def request(method, url, **kwargs):
        assert method == "GET" and url.endswith("/agent/runs/" + job)
        return httpx.Response(
            200,
            request=httpx.Request(method, url),
            json={
                "id": job,
                "review_gate": {"proceed": False, "reason": "PR is closed"},
                "prompt": "unnecessary duplicate prompt",
            },
        )

    monkeypatch.setattr(httpx, "request", request)
    agent_client.main()
    output = capsys.readouterr().out
    assert json.loads(output)["review_gate"]["proceed"] is False
    assert "synthetic" not in output and "unnecessary" not in output


def test_grace_timer_delivers_without_polling_or_another_event(tmp_path):
    sent = threading.Event()

    class Codex(FakeCodex):
        def send(self, thread, text):
            sent.set()

    ado = type("Ado", (FakeAdo,), {"pr": copy.deepcopy(FakeAdo.pr)})
    app = create_app(tmp_path / "state", tmp_path, ado_factory=ado, codex_factory=Codex)
    store = app.state.store
    settings = store.settings()
    settings.ado.organization = settings.ado.project = "example"
    settings.ado.own_identity = "own-author"
    settings.ado.enabled = settings.monitor_enabled = True
    settings.review_grace_seconds = 1
    assert settings.reconcile_periodically is False
    store.save_settings(settings)
    app.state.vault.set("ado_pat", "synthetic")
    store.save_agent(
        Agent(
            id="review",
            name="review",
            role="review",
            thread_id=THREAD,
            enabled=True,
            review_on_ci=True,
        )
    )
    with TestClient(app) as http:
        response = http.post(
            "/webhooks/ado",
            json={"id": "ci-grace", "eventType": "build.complete", "resource": {"id": 99}},
            headers={"Authorization": "Bearer " + app.state.vault.get("webhook")},
        )
        assert response.status_code == 202
        assert not sent.wait(0.1)
        assert sent.wait(3)
