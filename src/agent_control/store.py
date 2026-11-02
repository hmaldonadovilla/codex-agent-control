"""Small durable queue. Each mutation has an audit entry; secrets use a separate file."""

import contextlib
import json
import os
import secrets
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from .models import Agent, Settings


def now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
        self.path = directory / "state.sqlite3"
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS agents (id TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY, type TEXT NOT NULL, value TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, dedupe TEXT NOT NULL UNIQUE, agent_id TEXT NOT NULL,
                    kind TEXT NOT NULL, state TEXT NOT NULL, value TEXT NOT NULL,
                    prompt TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries (
                    job_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL,
                    message_id TEXT NOT NULL, mode TEXT NOT NULL, state TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS proposals (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL, value TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
                    action TEXT NOT NULL, detail TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS owned_sessions (
                    agent_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT NOT NULL,
                    job_id TEXT NOT NULL, role TEXT NOT NULL, body TEXT NOT NULL,
                    created_at TEXT NOT NULL);
            """)
            columns = {r[1] for r in db.execute("PRAGMA table_info(jobs)")}
            if "claimed_at" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN claimed_at TEXT")
        self.path.chmod(0o600)
        # A process dying after send may already have queued its message. Reconcile manually.
        with self.db() as db:
            db.execute(
                "UPDATE jobs SET state='unknown', detail='Previous dispatch interrupted; confirm in Codex before retrying' WHERE state='dispatching'"
            )

    @contextlib.contextmanager
    def db(self):
        with self.lock:
            connection = sqlite3.connect(self.path, timeout=15)
            connection.row_factory = sqlite3.Row
            try:
                yield connection
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def settings(self) -> Settings:
        with self.db() as db:
            row = db.execute("SELECT value FROM config WHERE key='settings'").fetchone()
        return Settings.model_validate_json(row[0]) if row else Settings()

    def save_settings(self, value: Settings):
        with self.db() as db:
            db.execute(
                "INSERT INTO config VALUES ('settings', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (value.model_dump_json(),),
            )
        self.audit("settings.updated", "Local controls updated")

    def agents(self) -> list[Agent]:
        with self.db() as db:
            rows = db.execute("SELECT value FROM agents ORDER BY id").fetchall()
        return [Agent.model_validate_json(row[0]) for row in rows]

    def agent(self, agent_id: str) -> Agent:
        agent = next((a for a in self.agents() if a.id == agent_id), None)
        if agent is None:
            raise ValueError("Agent not found")
        return agent

    def save_agent(self, agent: Agent):
        with self.db() as db:
            db.execute(
                "INSERT INTO agents VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET value=excluded.value",
                (agent.id, agent.model_dump_json()),
            )
        self.audit("agent.saved", agent.name)

    def ingest(self, event_id: str, kind: str, value: dict) -> bool:
        with self.db() as db:
            added = db.execute(
                "INSERT OR IGNORE INTO events (id,type,value,created_at) VALUES (?,?,?,?)",
                (event_id, kind, json.dumps(value), now()),
            ).rowcount
        if added:
            self.audit("ado.event", kind)
        return bool(added)

    def event_state(self, event_id: str, state: str):
        with self.db() as db:
            db.execute("UPDATE events SET state=? WHERE id=?", (state, event_id))

    def pending_events(self) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM events WHERE state='pending' ORDER BY created_at LIMIT 50"
            ).fetchall()
        return [dict(row) | {"value": json.loads(row["value"])} for row in rows]

    def event_counts(self) -> dict:
        with self.db() as db:
            rows = db.execute(
                "SELECT state, count(*) AS total FROM events GROUP BY state"
            ).fetchall()
        return {row["state"]: row["total"] for row in rows}

    def session(self, agent_id: str) -> str | None:
        with self.db() as db:
            row = db.execute(
                "SELECT thread_id FROM owned_sessions WHERE agent_id=?", (agent_id,)
            ).fetchone()
        return row[0] if row else None

    def save_session(self, agent_id: str, thread_id: str):
        from uuid import UUID

        thread_id = str(UUID(thread_id))
        with self.db() as db:
            row = db.execute(
                "SELECT thread_id FROM owned_sessions WHERE agent_id=?", (agent_id,)
            ).fetchone()
            if row and row[0] != thread_id:
                raise ValueError("Refusing to replace this agent's persistent conversation")
            db.execute(
                "INSERT OR IGNORE INTO owned_sessions VALUES (?,?,?)", (agent_id, thread_id, now())
            )

    def conversation(self, agent_id: str) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM (SELECT * FROM conversation_messages WHERE agent_id=? ORDER BY id DESC LIMIT 100) ORDER BY id",
                (agent_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def append_conversation(self, agent_id: str, job_id: str, role: str, body: str):
        if role not in {"user", "assistant"}:
            raise ValueError("Invalid conversation role")
        with self.db() as db:
            db.execute(
                "INSERT INTO conversation_messages(agent_id,job_id,role,body,created_at) VALUES(?,?,?,?,?)",
                (agent_id, job_id, role, body, now()),
            )

    def add_job(
        self, agent_id: str, kind: str, value: dict, prompt: str, dedupe: str | None = None
    ) -> str:
        job_id, timestamp = str(uuid4()), now()
        dedupe = dedupe or job_id
        with self.db() as db:
            db.execute(
                "INSERT OR IGNORE INTO jobs (id,dedupe,agent_id,kind,state,value,prompt,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    job_id,
                    dedupe,
                    agent_id,
                    kind,
                    "pending",
                    json.dumps(value),
                    prompt,
                    timestamp,
                    timestamp,
                ),
            )
            job_id = db.execute("SELECT id FROM jobs WHERE dedupe=?", (dedupe,)).fetchone()[0]
        return job_id

    def jobs(self, limit: int = 100) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) | {"value": json.loads(row["value"])} for row in rows]

    def job(self, job_id: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Job not found")
        return dict(row) | {"value": json.loads(row["value"])}

    # One outstanding review wake-up per agent. Other PRs remain in the durable
    # inbox; peer messages can still steer the active chat immediately.
    REVIEW_SLOT = """NOT EXISTS (SELECT 1 FROM jobs active WHERE active.agent_id=jobs.agent_id
        AND active.id!=jobs.id AND active.kind='review'
        AND active.state IN ('dispatching','queued_in_codex','unknown'))"""

    def claim(self, review_grace_seconds: int = 0) -> dict | None:
        cutoff = (datetime.now(UTC) - timedelta(seconds=review_grace_seconds)).isoformat()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                f"SELECT * FROM jobs WHERE state='pending' AND (kind != 'review' OR (created_at <= ? AND {self.REVIEW_SLOT})) ORDER BY CASE WHEN kind='message' THEN 0 ELSE 1 END, created_at LIMIT 1",
                (cutoff,),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET state='dispatching',updated_at=? WHERE id=? AND state='pending'",
                (now(), row["id"]),
            )
        return self.job(row["id"])

    def review_delay(self, grace_seconds: int) -> float | None:
        with self.db() as db:
            row = db.execute(
                f"SELECT MIN(created_at) FROM jobs WHERE state='pending' AND kind='review' AND {self.REVIEW_SLOT}"
            ).fetchone()
        if row[0] is None:
            return None
        due = datetime.fromisoformat(row[0]) + timedelta(seconds=grace_seconds)
        return max(0.001, (due - datetime.now(UTC)).total_seconds())

    def job_state(self, job_id: str, state: str, detail: str = ""):
        with self.db() as db:
            db.execute(
                "UPDATE jobs SET state=?,detail=?,updated_at=? WHERE id=?",
                (state, detail[:24000], now(), job_id),
            )
        self.audit("job." + state, job_id)

    def outstanding_reviews(self):
        with self.db() as db:
            rows = db.execute(
                "SELECT id FROM jobs WHERE kind='review' AND state IN ('pending','queued_in_codex')"
            ).fetchall()
        return [self.job(row[0]) for row in rows]

    def transition(self, job_id, expected, state, detail):
        with self.db() as db:
            changed = db.execute(
                "UPDATE jobs SET state=?,detail=?,updated_at=? WHERE id=? AND state=?",
                (state, detail, now(), job_id, expected),
            ).rowcount
        if changed:
            self.audit("job." + state, job_id)
        return bool(changed)

    def record_delivery(self, job_id, receipt):
        with self.db() as db:
            db.execute(
                "INSERT INTO deliveries VALUES (?,?,?,?,?,?,?)",
                (
                    job_id,
                    receipt["thread_id"],
                    receipt["message_id"],
                    receipt.get("mode", "queue"),
                    "accepted",
                    "",
                    now(),
                ),
            )

    def deliveries(self):
        with self.db() as db:
            return [
                dict(r) for r in db.execute("SELECT * FROM deliveries ORDER BY updated_at DESC")
            ]

    def delivery_state(self, job_id, state, detail=""):
        with self.db() as db:
            db.execute(
                "UPDATE deliveries SET state=?,detail=?,updated_at=? WHERE job_id=?",
                (state, detail, now(), job_id),
            )

    def finish_report(self, job_id, summary):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state,detail FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row["state"] == "reviewed":
                return False  # a delayed duplicate must never replace the actual report
            if row["state"] not in {"queued_in_codex", "awaiting_handoff"}:
                raise ValueError("Only a delivered run can receive a completion report")
            db.execute(
                "UPDATE jobs SET state='reviewed',detail=?,updated_at=? WHERE id=?",
                (summary, now(), job_id),
            )
        self.audit("job.reviewed", job_id)
        return True

    def claim_inbox(self, agent_id, grace_seconds):
        cutoff = (datetime.now(UTC) - timedelta(seconds=grace_seconds)).isoformat()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """SELECT id FROM jobs WHERE agent_id=? AND claimed_at IS NULL
                AND (state='queued_in_codex' OR (state='pending' AND
                    (kind!='review' OR created_at<=?)))
                ORDER BY CASE WHEN state='queued_in_codex' THEN 0 ELSE 1 END,created_at LIMIT 1""",
                (agent_id, cutoff),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE jobs SET state='dispatching',claimed_at=?,updated_at=? WHERE id=?",
                (now(), now(), row[0]),
            )
        return self.job(row[0])

    def proposals(self) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                "SELECT * FROM proposals ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [dict(row) | {"value": json.loads(row["value"])} for row in rows]

    def proposal(self, proposal_id: str) -> dict:
        with self.db() as db:
            row = db.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise ValueError("Proposal not found")
        return dict(row) | {"value": json.loads(row["value"])}

    def add_proposal(self, value: dict) -> str:
        proposal_id, timestamp = str(uuid4()), now()
        with self.db() as db:
            db.execute(
                "INSERT INTO proposals VALUES (?,?,?,?,?)",
                (proposal_id, "pending", json.dumps(value), timestamp, timestamp),
            )
        self.audit("proposal.created", value["title"])
        return proposal_id

    def decide(self, proposal_id: str, state: str, work=None):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None or row[0] != "pending":
                raise ValueError("Proposal has already been decided")
            if work:
                work(db)
            db.execute(
                "UPDATE proposals SET state=?,updated_at=? WHERE id=?", (state, now(), proposal_id)
            )
        self.audit("proposal." + state, proposal_id)

    def audit(self, action: str, detail: str):
        with self.db() as db:
            db.execute(
                "INSERT INTO audit (at,action,detail) VALUES (?,?,?)", (now(), action, detail[:300])
            )

    def activity(self):
        with self.db() as db:
            return [
                dict(row) for row in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")
            ]


class Secrets:
    def __init__(self, directory: Path):
        self.path = directory / "secrets.json"
        self.lock = threading.Lock()
        if not self.path.exists():
            self.write({"ui": secrets.token_urlsafe(32), "webhook": secrets.token_urlsafe(32)})

    def read(self) -> dict:
        return json.loads(self.path.read_text())

    def write(self, value: dict):
        temporary = self.path.with_suffix(".staged")
        descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w") as target:
            target.write(json.dumps(value))
        temporary.replace(self.path)
        self.path.chmod(0o600)

    def set(self, name: str, value: str):
        with self.lock:
            data = self.read()
            data[name] = value
            self.write(data)

    def get(self, name: str) -> str:
        return self.read().get(name, "")
