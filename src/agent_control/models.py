"""Validated configuration. Agent roles are data, not hardcoded execution branches."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Workspace(Value):
    repository: str = Field(min_length=1, max_length=120)
    path: str = Field(min_length=1, max_length=1000)


class Agent(Value):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=80)
    thread_id: str = ""
    delivery: Literal["desktop", "owned"] = "desktop"
    model: str = Field(default="", max_length=120)
    instructions: str = Field(default="", max_length=12000)
    enabled: bool = False
    review_on_ci: bool = False
    repositories: list[str] = Field(default_factory=list, max_length=100)
    peers: list[str] = Field(default_factory=list, max_length=50)
    workspaces: list[Workspace] = Field(default_factory=list, max_length=30)

    @field_validator("thread_id")
    @classmethod
    def valid_thread(cls, value: str) -> str:
        return str(UUID(value)) if value else ""


class AdoSettings(Value):
    organization: str = Field(default="", pattern=r"^[A-Za-z0-9-]*$")
    project: str = Field(default="", max_length=160)
    repositories: list[str] = Field(default_factory=list, max_length=100)
    excluded_authors: list[str] = Field(default_factory=list, max_length=100)
    own_identity: str = ""
    exclude_own: bool = True
    enabled: bool = False


class Settings(Value):
    ado: AdoSettings = Field(default_factory=AdoSettings)
    codex_command: str = "codex"
    desktop_node_command: str = ""
    monitor_enabled: bool = False
    reconcile_periodically: bool = False
    poll_seconds: Annotated[int, Field(ge=60, le=86400)] = 300
    review_grace_seconds: Annotated[int, Field(ge=0, le=300)] = 30


class ConnectionInput(Value):
    settings: AdoSettings
    pat: str | None = Field(default=None, repr=False, max_length=1000)


class Proposal(Value):
    agent_id: str
    kind: Literal["post_comments", "update_backlog", "adjust_code", "coordinate"]
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=20000)
    target_agent_id: str
    job_id: str | None = None


class Message(Value):
    target_agent_id: str
    body: str = Field(min_length=1, max_length=16000)
    source_agent_id: str | None = None
    parent_job_id: str | None = None


class AgentReport(Value):
    job_id: str
    summary: str = Field(min_length=1, max_length=24000)


class SuggestedAction(Value):
    kind: Literal["post_comments", "update_backlog", "adjust_code", "coordinate"]
    title: str = Field(min_length=1, max_length=160)
    body: str = Field(min_length=1, max_length=20000)
    target_agent_id: str = Field(min_length=1, max_length=64)


class PeerNote(Value):
    target_agent_id: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=16000)


class ReviewOutput(Value):
    outcome: Literal["completed", "blocked"]
    assessment: str = Field(min_length=1, max_length=24000)
    proposals: list[SuggestedAction] = Field(max_length=20)
    messages: list[PeerNote] = Field(max_length=10)


class WorktreeInput(Value):
    agent_id: str
    repository_path: str
    ref: str = Field(min_length=1, max_length=200)
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
