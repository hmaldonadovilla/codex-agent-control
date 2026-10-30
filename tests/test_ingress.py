import httpx
import pytest
from fastapi.testclient import TestClient

from agent_control.ingress import create_ingress


def test_tunnel_surface_contains_only_health_and_authenticated_webhook():
    calls = []

    def upstream(request):
        calls.append(request)
        return httpx.Response(202, json={"accepted": True, "duplicate": False})

    with TestClient(create_ingress(transport=httpx.MockTransport(upstream))) as client:
        for path in [
            "/",
            "/api/state",
            "/api/webhook/setup",
            "/agent/report",
            "/assets/app.js",
            "/docs",
            "/openapi.json",
        ]:
            assert client.get(path).status_code == 404
        assert client.get("/healthz").json()["service"] == "agent-control-webhook-ingress"
        assert client.get("/webhooks/ado").status_code == 405
        assert client.post("/webhooks/ado", json={}).status_code == 401
        response = client.post(
            "/webhooks/ado",
            json={"id": "event"},
            headers={
                "Host": "example.devtunnels.ms",
                "Authorization": "Basic synthetic",
                "X-Control-Token": "never-forward",
            },
        )
        assert response.status_code == 202
    assert len(calls) == 1
    assert calls[0].url.host == "127.0.0.1"
    assert calls[0].headers["authorization"] == "Basic synthetic"
    assert "x-control-token" not in calls[0].headers


@pytest.mark.parametrize(
    "url",
    [
        "https://example.invalid",
        "http://example.invalid",
        "http://127.0.0.1/api",
        "http://user:pass@localhost",
        "http://localhost?x=1",
    ],
)
def test_ingress_does_not_accept_an_arbitrary_forward_target(url):
    with pytest.raises(ValueError, match="loopback"):
        create_ingress(url)


def test_oversized_hooks_are_not_forwarded():
    def upstream(_):
        pytest.fail("Oversized request reached the local receiver")

    with TestClient(create_ingress(transport=httpx.MockTransport(upstream))) as client:
        assert (
            client.post(
                "/webhooks/ado",
                content=b"x" * 262145,
                headers={"Authorization": "Bearer synthetic"},
            ).status_code
            == 413
        )


def test_redirects_and_upstream_error_bodies_are_not_exposed():
    with TestClient(
        create_ingress(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    302, headers={"Location": "https://untrusted.invalid"}, text="private"
                )
            )
        )
    ) as client:
        response = client.post(
            "/webhooks/ado", json={}, headers={"Authorization": "Bearer synthetic"}
        )
        assert response.status_code == 502
        assert "location" not in response.headers
        assert "private" not in response.text
