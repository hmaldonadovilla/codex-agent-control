import copy
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from agent_control.adapters import CodexQueue, DeliveryUnknown
from agent_control.desktop import DesktopBridge
from agent_control.models import Message

from .conftest import THREAD


def test_active_chat_uses_steering_receipt_without_queue_fallback(monkeypatch):
    message_id = str(uuid4())
    bridge = DesktopBridge("unused")
    monkeypatch.setattr(
        bridge,
        "call",
        lambda *a, **k: {
            "status": "steered",
            "thread_id": THREAD,
            "message_id": message_id,
            "turn_id": "active-turn",
        },
    )
    monkeypatch.setattr(CodexQueue, "send", lambda *a: pytest.fail("Steer must not also queue"))
    assert bridge.send(THREAD, "Relevant contract changed")["mode"] == "steer"


@pytest.mark.parametrize("status", ["idle", "unavailable"])
def test_only_known_pre_delivery_rejection_can_fall_back_to_queue(monkeypatch, status):
    bridge = DesktopBridge("unused")
    monkeypatch.setattr(bridge, "call", lambda *a, **k: {"status": status})
    monkeypatch.setattr(
        CodexQueue, "send", lambda *a: {"message_id": str(uuid4()), "thread_id": THREAD}
    )
    assert bridge.send(THREAD, "Follow-up")["mode"] == "queue"


@pytest.mark.parametrize(
    "result",
    [{"status": "unknown"}, {}, {"status": "steered", "thread_id": "wrong", "turn_id": "t"}],
)
def test_ambiguous_or_mismatched_steering_never_falls_back(monkeypatch, result):
    bridge = DesktopBridge("unused")
    monkeypatch.setattr(bridge, "call", lambda *a, **k: result)
    monkeypatch.setattr(
        CodexQueue, "send", lambda *a: pytest.fail("Would duplicate an uncertain send")
    )
    with pytest.raises(DeliveryUnknown):
        bridge.send(THREAD, "Relevant message")


def track_delivery(world, monkeypatch):
    app, _, _, _, codex = world
    removed = []

    def send(self, thread, text):
        self.sent.append((thread, text))
        return {"thread_id": thread, "message_id": str(uuid4()), "mode": "queue"}

    monkeypatch.setattr(codex, "send", send)

    class Native:
        def __init__(self, *args):
            pass

        def remove(self, thread, message):
            removed.append((thread, message))
            return {"status": "removed"}

    app.state.control.desktop_factory = Native
    return app.state.control, app.state.store, removed


def test_completed_report_retires_only_its_native_copy_and_preserves_actual_report(
    world, monkeypatch
):
    control, store, removed = track_delivery(world, monkeypatch)
    job = control.reconcile()["jobs"][0]
    control.dispatch_one()
    receipt = store.deliveries()[0]
    assert control.report(job, "Actual independent review")["saved"]
    assert removed == [(THREAD, receipt["message_id"])]
    assert store.deliveries()[0]["state"] == "retired"
    assert not control.report(job, "Delayed obsolete skip")["saved"]
    assert store.job(job)["detail"] == "Actual independent review"
    assert len(removed) == 1


@pytest.mark.parametrize("change", ["merged", "revision", "draft"])
def test_queued_review_is_retired_and_withdrawn_before_another_wakeup(world, monkeypatch, change):
    control, store, removed = track_delivery(world, monkeypatch)
    ado = world[3]
    job = control.reconcile()["jobs"][0]
    control.dispatch_one()
    if change == "merged":
        ado.pr["status"] = "completed"
    elif change == "draft":
        ado.pr["isDraft"] = True
    else:
        ado.pr["lastMergeSourceCommit"]["commitId"] = "d" * 40
    assert control.retire_reviews()
    control.cleanup_deliveries()
    assert store.job(job)["state"] == "superseded"
    assert not control.review_gate(store.job(job))["proceed"]
    assert len(removed) == 1


def test_review_backlog_is_coalesced_but_peer_messages_can_steer(world, monkeypatch):
    app, _, _, ado, codex = world
    control, store = app.state.control, app.state.store
    first = control.reconcile()["jobs"][0]
    prs = {42: copy.deepcopy(ado.pr)}
    ado.pr["pullRequestId"] = 43
    prs[43] = copy.deepcopy(ado.pr)
    monkeypatch.setattr(ado, "pull_request", lambda self, repo, number: copy.deepcopy(prs[number]))
    second = control.reconcile()["jobs"][0]
    assert control.dispatch_one()
    assert store.job(first)["state"] == "queued_in_codex"
    assert not control.dispatch_one()
    assert store.review_delay(0) is None  # no millisecond busy loop for a held inbox
    peer = control.message(Message(target_agent_id="review", body="Relevant API contract update"))
    assert control.dispatch_one()
    assert store.job(peer)["state"] == "queued_in_codex"
    control.report(first, "First completed")
    next_job = control.next_run("review")
    assert next_job["id"] == peer  # delivered but not yet claimed
    assert control.next_run("review")["id"] == second
    assert control.next_run("review") is None
    assert len(codex.sent) == 2  # second review claimed in-chat without a new prompt


def test_report_does_not_require_native_cleanup_to_succeed(world, monkeypatch):
    control, store, _ = track_delivery(world, monkeypatch)
    monkeypatch.setattr(control.desktop_factory, "remove", lambda *a: {"status": "unknown"})
    job = control.reconcile()["jobs"][0]
    control.dispatch_one()
    assert control.report(job, "Completed")["saved"]
    assert store.job(job)["state"] == "reviewed"
    assert store.deliveries()[0]["state"] == "cleanup_pending"
    assert control.dispatch_one() is False


def test_worker_and_active_chat_cannot_claim_same_pending_job(world):
    app, *_ = world
    store = app.state.store
    app.state.control.reconcile()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(store.claim, 0)
        b = pool.submit(store.claim_inbox, "review", 0)
        assert sum(r is not None for r in [a.result(), b.result()]) == 1


def test_inbox_endpoint_is_profile_scoped_and_claim_does_not_dispatch(world):
    app, http, headers, _, codex = world
    own = app.state.control.reconcile()["jobs"][0]
    other = app.state.control.message(Message(target_agent_id="development", body="Other agent"))
    assert http.post("/agent/inbox/next", json={}).status_code == 401
    key = http.post("/api/agents/review/report-key", json={}, headers=headers).json()["token"]
    auth = {"Authorization": "Bearer " + key}
    result = http.post("/agent/inbox/next", json={}, headers=auth).json()["run"]
    assert result["id"] == own and result["review_gate"]["proceed"]
    assert http.post("/agent/inbox/next", json={}, headers=auth).json()["run"] is None
    assert app.state.store.job(other)["state"] == "pending"
    assert codex.sent == []


def test_new_revision_retires_pending_old_revision_before_it_can_queue(world):
    app, _, _, ado, codex = world
    control, store = app.state.control, app.state.store
    old = control.reconcile()["jobs"][0]
    ado.pr["lastMergeSourceCommit"]["commitId"] = "d" * 40
    new = control.reconcile()["jobs"][0]
    assert store.job(old)["state"] == "superseded"
    assert control.dispatch_one()
    assert store.job(new)["state"] == "queued_in_codex"
    assert len(codex.sent) == 1


def test_stale_retirement_cannot_overwrite_a_concurrent_completed_assessment(world, monkeypatch):
    app, _, _, ado, _ = world
    control, store = app.state.control, app.state.store
    job = control.reconcile()["jobs"][0]
    control.dispatch_one()

    def read(self, repo, number):
        control.report(job, "Finished while live state was being fetched")
        return copy.deepcopy(ado.pr) | {"status": "completed"}

    monkeypatch.setattr(ado, "pull_request", read)
    assert control.retire_reviews() is False
    assert store.job(job)["state"] == "reviewed"
    assert store.job(job)["detail"] == "Finished while live state was being fetched"
