import threading

from fastapi.testclient import TestClient

from agent_control.app import create_app

from .conftest import FakeAdo, FakeCodex


def test_worker_waits_for_events_instead_of_scanning_on_startup(tmp_path):
    app = create_app(tmp_path / "state", tmp_path, ado_factory=FakeAdo, codex_factory=FakeCodex)
    settings = app.state.store.settings()
    settings.monitor_enabled = True
    app.state.store.save_settings(settings)
    received = threading.Event()
    values = []

    def reconcile(value=None):
        values.append(value)
        received.set()
        return {"jobs": [], "held": []}

    app.state.control.reconcile = reconcile
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert not received.is_set()
        response = client.post(
            "/webhooks/ado",
            json={"id": "event-wake", "eventType": "build.complete", "resource": {"id": 99}},
            headers={"Authorization": "Bearer " + app.state.vault.get("webhook")},
        )
        assert response.status_code == 202
        assert received.wait(2)
        assert values == [{"build": 99}]


def test_persisted_event_burst_drains_without_another_webhook_or_timer(tmp_path):
    app = create_app(tmp_path / "state", tmp_path, ado_factory=FakeAdo, codex_factory=FakeCodex)
    settings = app.state.store.settings()
    settings.monitor_enabled = True
    app.state.store.save_settings(settings)
    for number in range(110):
        app.state.store.ingest(f"burst-{number}", "build.complete", {"build": number})
    values = []
    drained = threading.Event()
    app.state.control.reconcile = lambda event: values.append(event)

    def dispatch():
        drained.set()
        return False

    app.state.control.dispatch_one = dispatch
    with TestClient(app):
        assert drained.wait(5)
        assert len(values) == 110
        assert app.state.store.pending_events() == []
