import io
import json

import pytest

from agent_control.adapters import DeliveryUnknown
from agent_control.models import Agent, Message, Proposal, ReviewOutput, Workspace
from agent_control.owned import OwnedCodex
from agent_control.store import Store

THREAD = "22222222-2222-4222-8222-222222222222"


def test_repeated_jobs_resume_one_persistent_conversation(tmp_path, monkeypatch):
    store = Store(tmp_path / "state")
    agent = Agent(id="review", name="Reviewer", role="reviewer", delivery="owned")
    calls = []

    class Process:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.stdin = io.StringIO()
            output = {
                "outcome": "completed",
                "assessment": "Reviewed",
                "proposals": [],
                "messages": [],
            }
            self.stdout = io.StringIO(
                "\n".join(
                    json.dumps(e)
                    for e in [
                        {"type": "thread.started", "thread_id": THREAD},
                        {
                            "type": "item.completed",
                            "item": {"type": "agent_message", "text": json.dumps(output)},
                        },
                        {"type": "turn.completed"},
                    ]
                )
            )

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr("agent_control.owned.subprocess.Popen", Process)
    runner = OwnedCodex(store, "/bin/true")
    for number in range(2):
        assert (
            runner.run(
                agent, {"id": str(number), "value": {}}, "Review this job", tmp_path
            ).assessment
            == "Reviewed"
        )
    assert "resume" not in calls[0]
    assert calls[1][-3:] == ["resume", THREAD, "-"]
    assert all(
        "--ignore-user-config" in c and "read-only" in c and "--ephemeral" not in c for c in calls
    )
    assert store.session(agent.id) == THREAD
    assert len(store.conversation(agent.id)) == 4
    with pytest.raises(ValueError, match="replace"):
        store.save_session(agent.id, "33333333-3333-4333-8333-333333333333")


def test_resume_failure_never_starts_a_replacement_conversation(tmp_path, monkeypatch):
    store = Store(tmp_path / "state")
    store.save_session("review", THREAD)
    calls = []

    class Failed:
        def __init__(self, args, **kwargs):
            calls.append(args)
            self.stdin = io.StringIO()
            self.stdout = io.StringIO(json.dumps({"type": "turn.failed"}) + "\n")

        def wait(self, timeout):
            return 1

        def poll(self):
            return 1

    monkeypatch.setattr("agent_control.owned.subprocess.Popen", Failed)
    runner = OwnedCodex(store, "/bin/true")
    with pytest.raises(DeliveryUnknown):
        runner.run(
            Agent(id="review", name="Reviewer", role="reviewer"),
            {"id": "job", "value": {}},
            "Review",
            tmp_path,
        )
    assert len(calls) == 1
    assert store.session("review") == THREAD


def test_owned_report_and_peer_note_are_saved_without_external_actions(world):
    app, _, _, *_ = world
    control, store = app.state.control, app.state.store
    agent = store.agent("review")
    agent.delivery = "owned"
    agent.workspaces = [Workspace(repository="example", path=str(control.workspace_root))]
    control.save_agent(agent)

    class Runner:
        def __init__(self, store, command):
            pass

        def run(self, agent, job, prompt, cwd):
            return ReviewOutput(
                outcome="completed",
                assessment="Contract change affects E3",
                proposals=[],
                messages=[
                    {
                        "target_agent_id": "development",
                        "body": "Please assess the interface change; this is information only.",
                    }
                ],
            )

    control.owned_factory = Runner
    job = control.message(Message(target_agent_id="review", body="Review the contract"))
    assert control.dispatch_one()
    assert store.job(job)["state"] == "reviewed"
    peer = next(j for j in store.jobs() if j["agent_id"] == "development")
    assert peer["state"] == "pending"
    assert peer["value"]["parent_job_id"] == job
    assert peer["value"]["hops"] == 1


def test_owned_approval_creates_a_handoff_without_running_or_claiming_execution(world):
    app, _, _, *_ = world
    control, store = app.state.control, app.state.store
    agent = store.agent("review")
    agent.delivery = "owned"
    control.save_agent(agent)
    proposal = control.propose(
        Proposal(
            agent_id="review",
            target_agent_id="review",
            kind="post_comments",
            title="Post exact reviewed comment",
            body="Exact comment for the human",
        )
    )
    job_id = control.approve(proposal)
    assert store.proposal(proposal)["state"] == "approved_for_handoff"
    assert store.job(job_id)["state"] == "awaiting_handoff"
    assert not control.dispatch_one()


def test_paused_desktop_agent_can_receive_manual_handoff_without_being_enabled(world):
    app, _, _, *_ = world
    control, store = app.state.control, app.state.store
    agent = store.agent("development")
    agent.enabled = False
    control.save_agent(agent)
    proposal = control.propose(
        Proposal(
            agent_id="review",
            target_agent_id="development",
            kind="post_comments",
            title="Post reviewed comment",
            body="Exact human-reviewed text",
        )
    )
    job_id = control.approve(proposal)
    assert store.job(job_id)["state"] == "awaiting_handoff"
    assert store.agent("development").enabled is False
    assert not control.dispatch_one()


def test_peer_derived_proposal_keeps_original_pr_revision_check(world):
    app, _, _, ado, _ = world
    control, store = app.state.control, app.state.store
    parent = control.reconcile()["jobs"][0]
    store.job_state(parent, "queued_in_codex", "synthetic delivered review")
    child = control.message(
        Message(
            source_agent_id="review",
            target_agent_id="development",
            parent_job_id=parent,
            body="Relevant PR context",
        )
    )
    store.job_state(child, "queued_in_codex", "synthetic delivered peer note")
    proposal = control.propose(
        Proposal(
            agent_id="development",
            target_agent_id="development",
            job_id=child,
            kind="adjust_code",
            title="Apply the reviewed fix",
            body="Exact fix scope",
        )
    )
    ado.pr["lastMergeSourceCommit"]["commitId"] = "d" * 40
    with pytest.raises(ValueError, match="changed"):
        control.approve(proposal)
    assert store.proposal(proposal)["state"] == "pending"
