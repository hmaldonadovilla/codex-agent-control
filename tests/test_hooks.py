import json

import httpx

from agent_control.adapters import Ado
from agent_control.hooks import register_subscription
from agent_control.store import Secrets, Store


def test_registration_is_scoped_authenticated_and_idempotent(tmp_path):
    store, vault = Store(tmp_path / "state"), Secrets(tmp_path / "state")
    settings = store.settings()
    settings.ado.organization, settings.ado.project = "example", "Project"
    store.save_settings(settings)
    vault.set("ado_pat", "synthetic")
    (store.directory / "tunnel.json").write_text(
        json.dumps(
            {
                "id": "dedicated.euw",
                "url": "https://example-8769.euw.devtunnels.ms",
                "port": 8769,
                "executable": "/bin/true",
            }
        )
    )
    subscriptions, writes = [], []

    def request(req):
        if req.url.path.endswith("projects/Project"):
            return httpx.Response(200, json={"id": "project-id", "name": "Project"})
        if req.url.path.endswith("connectionData"):
            return httpx.Response(200, json={"authenticatedUser": {"id": "own"}})
        if req.method == "GET":
            return httpx.Response(200, json={"value": subscriptions})
        body = json.loads(req.content)
        writes.append(body)
        subscriptions.append(body | {"id": "hook-id", "status": "enabled"})
        return httpx.Response(200, json=subscriptions[-1])

    def factory(settings, pat):
        return Ado(settings, pat, transport=httpx.MockTransport(request))

    first = register_subscription(store, vault, factory)
    second = register_subscription(store, vault, factory)
    assert first == second
    assert len(writes) == 1
    assert writes[0]["publisherInputs"] == {"projectId": "project-id", "buildStatus": "Succeeded"}
    assert writes[0]["consumerInputs"]["basicAuthPassword"] == vault.get("webhook")
    assert vault.get("webhook") not in json.dumps(first)
    assert vault.get("webhook") not in (store.directory / "ado-hook.json").read_text()
