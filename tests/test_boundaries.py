import base64
import copy
import hashlib
import json
import subprocess

import httpx
import pytest

from agent_control.adapters import BUILD_POLICY, Ado, CodexQueue, ConnectionUnavailable
from agent_control.models import AdoSettings

from .conftest import FakeAdo


def test_missing_queue_command_is_not_reported_as_available(monkeypatch):
    monkeypatch.setattr(CodexQueue, "executable", lambda self: "synthetic-codex")

    def run(args, **kwargs):
        return subprocess.CompletedProcess(
            args,
            0 if "--version" in args else 1,
            "codex synthetic" if "--version" in args else "",
            "private diagnostic",
        )

    monkeypatch.setattr(subprocess, "run", run)
    result = CodexQueue("synthetic-codex").diagnose()
    assert result["cli"] is True
    assert result["ready"] is False
    assert "existing-thread queue command is unavailable" in result["detail"]
    assert "private diagnostic" not in result["detail"]


def test_existing_thread_queue_does_not_require_a_shared_daemon(monkeypatch):
    monkeypatch.setattr(CodexQueue, "executable", lambda self: "synthetic-codex")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[1:] == ["--version"]:
            return subprocess.CompletedProcess(args, 0, "synthetic version", "")
        if args[1:] == ["queue", "--help"]:
            return subprocess.CompletedProcess(args, 0, "--thread <UUID> --message <TEXT>", "")
        pytest.fail("Readiness must not require or start an unrelated daemon")

    monkeypatch.setattr(subprocess, "run", run)
    assert CodexQueue("synthetic-codex").diagnose()["ready"] is True
    assert all("daemon" not in args for args in calls)


@pytest.mark.parametrize(
    "output",
    [
        "",
        "Queued message 22222222-2222-4222-8222-222222222222 for thread 99999999-9999-4999-8999-999999999999.",
    ],
)
def test_cli_exit_without_matching_receipt_does_not_claim_delivery(monkeypatch, output):
    from agent_control.adapters import DeliveryUnknown

    monkeypatch.setattr(CodexQueue, "executable", lambda self: "synthetic-codex")
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, output, "")
    )
    with pytest.raises(DeliveryUnknown):
        CodexQueue("synthetic-codex").send("11111111-1111-4111-8111-111111111111", "test")


def test_native_receipt_confirms_only_queueing_to_the_exact_thread(monkeypatch):
    monkeypatch.setattr(CodexQueue, "executable", lambda self: "synthetic-codex")
    output = "Queued message 22222222-2222-4222-8222-222222222222 for thread 11111111-1111-4111-8111-111111111111."
    monkeypatch.setattr(
        subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 0, output, "")
    )
    result = CodexQueue("synthetic-codex").send("11111111-1111-4111-8111-111111111111", "test")
    assert result["message_id"] == "22222222-2222-4222-8222-222222222222"


def test_connection_check_uses_preview_version_for_identity():
    seen = []

    def respond(request):
        seen.append(request.url.path)
        if request.url.path.endswith("connectionData"):
            assert request.url.params["api-version"] == "7.1-preview.1"
            assert request.url.params["connectOptions"] == "1"
            return httpx.Response(200, json={"authenticatedUser": {"id": "own-author"}})
        assert request.url.params["api-version"] == "7.1"
        return httpx.Response(200, json={"id": "project-id", "name": "Example Project"})

    ado = Ado(
        AdoSettings(organization="example", project="Example Project"),
        "synthetic",
        httpx.MockTransport(respond),
    )
    try:
        assert ado.test()["identity"] == "own-author"
        assert len(seen) == 2
    finally:
        ado.close()


def test_unrelated_build_does_not_schedule_other_ready_prs():
    def respond(request):
        if "/build/builds/" in request.url.path:
            return httpx.Response(
                200, json={"repository": {"id": "unrelated"}, "sourceVersion": "d" * 40}
            )
        return httpx.Response(200, json={"value": [FakeAdo.pr]})

    ado = Ado(
        AdoSettings(organization="example", project="example"),
        "synthetic",
        httpx.MockTransport(respond),
    )
    try:
        assert ado.candidates({"build": 99}) == []
    finally:
        ado.close()


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_ado_failure_names_operation_and_status_without_echoing_response(status):
    secret = "synthetic-secret-never-display"
    ado = Ado(
        AdoSettings(organization="example", project="example"),
        secret,
        httpx.MockTransport(lambda _: httpx.Response(status, json={"message": secret})),
    )
    try:
        with pytest.raises(ConnectionUnavailable) as result:
            ado.test()
        assert f"HTTP {status}" in str(result.value)
        assert "project lookup" in str(result.value)
        assert secret not in str(result.value)
    finally:
        ado.close()


@pytest.mark.parametrize(
    "case",
    [
        "valid",
        "old",
        "old_merge_same_source",
        "failed",
        "pending",
        "no_policy",
        "missing_run",
        "other_review_policy",
    ],
)
def test_ci_is_read_back_for_current_revision_instead_of_trusting_events(case):
    pr = copy.deepcopy(FakeAdo.pr)
    evaluation = {
        "status": "approved",
        "context": {"buildId": 99},
        "configuration": {"type": {"id": BUILD_POLICY}, "isBlocking": True, "isEnabled": True},
    }
    build = {"status": "completed", "result": "succeeded", "sourceVersion": "c" * 40}
    if case == "old":
        build["sourceVersion"] = "d" * 40
    if case == "old_merge_same_source":
        build["sourceVersion"] = "d" * 40
        build["triggerInfo"] = {"pr.sourceSha": "a" * 40}
    if case == "failed":
        build["result"] = "failed"
    if case == "pending":
        evaluation["status"] = "queued"
    if case == "missing_run":
        evaluation["context"] = {}
    if case == "other_review_policy":
        evaluation["configuration"]["type"]["id"] = "minimum-reviewers"

    def respond(request):
        if "policy/evaluations" in request.url.path:
            assert "artifactId" in request.url.params
            assert request.url.params["api-version"] == "7.1-preview.1"
            return httpx.Response(200, json={"value": [] if case == "no_policy" else [evaluation]})
        return httpx.Response(200, json=build)

    ado = Ado(
        AdoSettings(organization="example", project="Example Project"),
        "synthetic",
        httpx.MockTransport(respond),
    )
    try:
        assert ado.ci(pr)[0] is (case == "valid")
    finally:
        ado.close()


def test_all_required_builds_must_pass():
    def respond(request):
        if "policy/evaluations" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "status": status,
                            "context": {"buildId": n},
                            "configuration": {
                                "type": {"id": BUILD_POLICY},
                                "isEnabled": True,
                                "isBlocking": True,
                            },
                        }
                        for n, status in [(1, "approved"), (2, "queued")]
                    ]
                },
            )
        return httpx.Response(
            200, json={"status": "completed", "result": "succeeded", "sourceVersion": "c" * 40}
        )

    ado = Ado(
        AdoSettings(organization="example", project="example"),
        "synthetic",
        httpx.MockTransport(respond),
    )
    try:
        assert ado.ci(FakeAdo.pr)[0] is False
    finally:
        ado.close()


def test_events_are_authenticated_deduplicated_and_store_only_identifiers(world):
    app, http, _, *_ = world
    payload = {
        "id": "event-1",
        "eventType": "git.pullrequest.created",
        "resource": {
            "pullRequestId": 42,
            "repository": {"id": "repo-id"},
            "description": "untrusted instructions and secret-like text",
        },
    }
    assert http.post("/webhooks/ado", json=payload).status_code == 401
    authorization = {
        "Authorization": "Basic "
        + base64.b64encode(("ado:" + app.state.vault.get("webhook")).encode()).decode()
    }
    first = http.post("/webhooks/ado", json=payload, headers=authorization)
    assert first.status_code == 202 and first.json()["duplicate"] is False
    assert (
        http.post("/webhooks/ado", json=payload, headers=authorization).json()["duplicate"] is True
    )
    stored = app.state.store.pending_events()
    assert stored[0]["value"] == {"pr": 42, "repository": "repo-id"}
    assert "untrusted instructions" not in json.dumps(stored)


def test_oversized_and_unknown_hooks_are_rejected(world):
    app, http, *_ = world
    headers = {"Authorization": "Bearer " + app.state.vault.get("webhook")}
    assert http.post("/webhooks/ado", content=b"x" * 262145, headers=headers).status_code == 413
    assert (
        http.post("/webhooks/ado", json={"eventType": "arbitrary"}, headers=headers).status_code
        == 400
    )


def test_loopback_api_rejects_missing_ui_token_foreign_origin_and_host(world):
    _, http, headers, *_ = world
    assert http.get("/api/state").status_code == 401
    assert (
        http.get(
            "/api/state", headers={**headers, "Origin": "https://attacker.invalid"}
        ).status_code
        == 403
    )
    assert (
        http.get("/api/state", headers={**headers, "Host": "attacker.invalid"}).status_code == 403
    )
    assert http.get("/").headers["X-Frame-Options"] == "DENY"


def test_validation_errors_never_echo_a_submitted_credential(world):
    _, http, headers, *_ = world
    secret = "not-a-real-secret-" * 100
    response = http.post(
        "/api/connections/ado", json={"settings": {}, "pat": secret}, headers=headers
    )
    assert response.status_code == 422 and secret not in response.text


def test_agent_credentials_can_report_and_propose_but_cannot_approve(world):
    app, http, headers, *_ = world
    key = http.post("/api/agents/review/report-key", json={}, headers=headers).json()["token"]
    agent_headers = {"Authorization": "Bearer " + key}
    assert app.state.vault.get("agent:review") == hashlib.sha256(key.encode()).hexdigest()
    job = app.state.control.reconcile()["jobs"][0]
    app.state.store.job_state(job, "queued_in_codex")
    assert (
        http.post(
            "/agent/report",
            json={"job_id": job, "summary": "A verified review"},
            headers=agent_headers,
        ).status_code
        == 200
    )
    proposal = http.post(
        "/agent/propose",
        json={
            "agent_id": "review",
            "target_agent_id": "development",
            "kind": "adjust_code",
            "title": "Fix",
            "body": "Exact change",
            "job_id": job,
        },
        headers=agent_headers,
    )
    assert proposal.status_code == 201
    assert (
        http.post(
            f"/api/proposals/{proposal.json()['id']}/approve", json={}, headers=agent_headers
        ).status_code
        == 401
    )
    assert app.state.store.proposal(proposal.json()["id"])["state"] == "pending"


def test_reporting_bootstrap_keeps_credentials_in_owner_only_file(world):
    app, http, headers, *_ = world
    result = http.post("/api/agents/review/bootstrap", json={}, headers=headers)
    assert result.status_code == 200
    from pathlib import Path

    path = Path(result.json()["path"])
    data = json.loads(path.read_text())
    assert path.stat().st_mode & 0o077 == 0
    assert data["token"] not in result.text
    assert str(path) in app.state.store.agent("review").instructions


def test_automation_stays_off_until_connection_and_agent_configuration(world):
    app, http, headers, *_ = world
    settings = app.state.store.settings()
    settings.ado.own_identity = ""
    settings.monitor_enabled = True
    response = http.put("/api/settings", json=settings.model_dump(), headers=headers)
    assert response.status_code == 409
    assert app.state.store.settings().monitor_enabled is False


def test_changed_ado_credential_requires_identity_retest(world):
    app, http, headers, *_ = world
    settings = app.state.store.settings()
    settings.monitor_enabled = True
    app.state.store.save_settings(settings)
    response = http.post(
        "/api/connections/ado",
        json={"settings": settings.ado.model_dump(), "pat": "synthetic-new-pat"},
        headers=headers,
    )
    assert response.status_code == 200
    assert app.state.store.settings().ado.own_identity == ""
    assert app.state.store.settings().monitor_enabled is False


def test_automation_cannot_start_without_chat_delivery(world):
    app, http, headers, *_ = world
    app.state.control.codex_factory = lambda _: type(
        "Disconnected", (), {"diagnose": lambda self: {"ready": False}}
    )()
    settings = app.state.store.settings()
    settings.monitor_enabled = True
    response = http.put("/api/settings", json=settings.model_dump(), headers=headers)
    assert response.status_code == 409
    assert "Chat delivery" in response.json()["detail"]
    assert app.state.store.settings().monitor_enabled is False
