"""Register one scoped ADO build webhook; never modify unrelated subscriptions."""

import json

import httpx

from .adapters import Ado, ConnectionUnavailable
from .runtime import load_tunnel


def subscription_status(directory):
    path = directory / "ado-hook.json"
    return json.loads(path.read_text()) if path.exists() else {"registered": False}


def register_subscription(store, vault, ado_factory=Ado):
    settings = store.settings()
    tunnel = load_tunnel(store.directory)
    if not tunnel:
        raise ValueError("Configure and verify the dedicated Azure Dev Tunnel first")
    url = tunnel["url"].rstrip("/") + "/webhooks/ado"
    adapter = ado_factory(settings.ado, vault.get("ado_pat"))
    try:
        project = adapter.test()
        existing = adapter.get("_apis/hooks/subscriptions").get("value", [])
        matches = [s for s in existing if s.get("consumerInputs", {}).get("url") == url]
        if matches:
            if len(matches) != 1:
                raise ValueError(
                    "Several subscriptions use this receiver; inspect ADO before changing it"
                )
            result = matches[0]
            if not (
                result.get("publisherId") == "tfs"
                and result.get("eventType") == "build.complete"
                and result.get("consumerId") == "webHooks"
                and result.get("publisherInputs", {}).get("projectId") == project["project_id"]
                and result.get("publisherInputs", {}).get("buildStatus") == "Succeeded"
                and result.get("status") == "enabled"
            ):
                raise ValueError("The existing receiver subscription differs; it was not modified")
        else:
            body = {
                "publisherId": "tfs",
                "eventType": "build.complete",
                "resourceVersion": "2.0",
                "consumerId": "webHooks",
                "consumerActionId": "httpRequest",
                "publisherInputs": {"projectId": project["project_id"], "buildStatus": "Succeeded"},
                "consumerInputs": {
                    "url": url,
                    "basicAuthUsername": "ado",
                    "basicAuthPassword": vault.get("webhook"),
                    "resourceDetailsToSend": "minimal",
                    "messagesToSend": "none",
                    "detailedMessagesToSend": "none",
                },
            }
            try:
                response = adapter.client.post(
                    "_apis/hooks/subscriptions", params={"api-version": "7.1"}, json=body
                )
                response.raise_for_status()
                result = response.json()
            except httpx.HTTPStatusError as error:
                raise ConnectionUnavailable(
                    f"ADO webhook registration: HTTP {error.response.status_code}. Check service-hook creation permission for this project"
                ) from None
            except (httpx.HTTPError, ValueError):
                raise ConnectionUnavailable(
                    "ADO webhook registration was not confirmed. Inspect the project's service hooks before retrying; no automatic retry was made"
                ) from None
        public = {
            "registered": True,
            "id": result["id"],
            "event": result["eventType"],
            "status": result["status"],
            "url": url,
            "project": settings.ado.project,
            "organization": settings.ado.organization,
        }
        path = store.directory / "ado-hook.json"
        path.write_text(json.dumps(public, indent=2) + "\n")
        path.chmod(0o600)
        store.audit(
            "ado.subscription",
            "Successful build events registered for the dedicated authenticated receiver",
        )
        return public
    finally:
        adapter.close()
