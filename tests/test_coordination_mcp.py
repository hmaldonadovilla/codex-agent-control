import json

import httpx
import pytest

from agent_control.coordination_mcp import Coordination


def bridge_for(world, tmp_path):
    app, http, headers, *_ = world
    value = http.post("/api/agents/review/report-key", json={}, headers=headers).json()
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            {"agent_id": "review", "base_url": "http://localhost:8768", "token": value["token"]}
        )
    )
    path.chmod(0o600)

    def relay(request):
        result = http.request(
            request.method,
            request.url.raw_path.decode(),
            headers={
                "Authorization": request.headers["authorization"],
                "Content-Type": request.headers.get("content-type", "application/json"),
            },
            content=request.content,
        )
        return httpx.Response(result.status_code, json=result.json())

    return Coordination(tmp_path, transport=httpx.MockTransport(relay))


def test_coordination_has_scoped_context_but_no_approval_or_arbitrary_http_tool(world, tmp_path):
    bridge = bridge_for(world, tmp_path)
    result = bridge.call("agent_context", {"agent_id": "review"})
    assert result["id"] == "review"
    assert [p["id"] for p in result["peers"]] == ["development"]
    assert "token" not in result
    assert {t["name"] for t in bridge.tools()} == {
        "agent_context",
        "get_run",
        "next_run",
        "report_result",
        "send_peer_message",
        "propose_action",
    }
    with pytest.raises(ValueError):
        bridge.call("approve", {"agent_id": "review"})
    with pytest.raises(ValueError):
        bridge.call("agent_context", {"agent_id": "../review"})


def test_coordination_preserves_run_ownership_and_deduplicates_allowed_peer_notes(world, tmp_path):
    app, *_ = world
    bridge = bridge_for(world, tmp_path)
    store = app.state.store
    own = store.add_job("review", "message", {}, "test")
    other = store.add_job("development", "message", {}, "other")
    store.job_state(own, "queued_in_codex", "synthetic delivered run")
    with pytest.raises(ValueError):
        bridge.call("get_run", {"agent_id": "review", "run_id": other})
    args = {
        "agent_id": "review",
        "run_id": own,
        "target_agent_id": "development",
        "message": "Interface context only",
    }
    first = bridge.call("send_peer_message", args)
    assert bridge.call("send_peer_message", args) == first
    with pytest.raises(ValueError):
        bridge.call("send_peer_message", args | {"target_agent_id": "review"})
    bridge.call(
        "report_result", {"agent_id": "review", "run_id": own, "summary": "Actual assessment"}
    )
    assert store.job(own)["state"] == "reviewed"


def test_coordination_refuses_nonprivate_or_nonlocal_credentials(tmp_path):
    path = tmp_path / "review.json"
    path.write_text(
        json.dumps(
            {"agent_id": "review", "base_url": "https://external.invalid", "token": "synthetic"}
        )
    )
    path.chmod(0o600)
    bridge = Coordination(tmp_path)
    with pytest.raises(ValueError):
        bridge.config("review")
    path.chmod(0o644)
    with pytest.raises(ValueError):
        bridge.config("review")
