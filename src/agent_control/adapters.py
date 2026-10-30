"""External boundaries: Azure DevOps reads and the installed Codex queue command."""

import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import httpx

from .models import AdoSettings

BUILD_POLICY = "0609b952-1397-4640-95ec-e00a01b2c241"


class ConnectionUnavailable(Exception):
    pass


class DeliveryUnknown(Exception):
    pass


class Ado:
    def __init__(self, settings: AdoSettings, pat: str, transport=None):
        self.settings = settings
        if not settings.organization or not settings.project or not pat:
            raise ConnectionUnavailable("Configure the ADO organization, project and credential")
        self.client = httpx.Client(
            base_url=f"https://dev.azure.com/{settings.organization}/",
            auth=("", pat),
            timeout=15,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self.project = quote(settings.project, safe="")

    def close(self):
        self.client.close()

    def get(self, path: str, params: dict | None = None):
        resource = path.split("_apis/", 1)[-1].split("/", 1)[0]
        operation = {
            "projects": "project lookup",
            "connectionData": "account identity lookup",
            "git": "repository/PR read",
            "build": "build read",
            "policy": "PR policy read",
        }.get(resource, "read")
        try:
            response = self.client.get(path, params={"api-version": "7.1", **(params or {})})
            response.raise_for_status()
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            detail = {
                400: "Request rejected; check the API version and request fields",
                401: "Credential rejected; check token expiry and organization",
                403: "Access denied; check credential scopes and project permissions",
                404: "Resource not found; check the organization, project and repository",
                429: "Request rate limited; retry later",
            }.get(status, "ADO returned an unsuccessful response")
            raise ConnectionUnavailable(f"ADO {operation}: HTTP {status}. {detail}") from None
        except httpx.TimeoutException:
            raise ConnectionUnavailable(
                f"ADO {operation} timed out; check network connectivity"
            ) from None
        except httpx.HTTPError:
            raise ConnectionUnavailable(
                f"ADO {operation} could not connect; check network, proxy and certificate settings"
            ) from None
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
            return value
        except ValueError:
            raise ConnectionUnavailable(
                f"ADO {operation}: HTTP {response.status_code} returned invalid JSON; check authentication or proxy settings"
            ) from None

    def test(self):
        project = self.get(f"_apis/projects/{self.project}")
        identity = self.get(
            "_apis/connectionData", {"connectOptions": "1", "api-version": "7.1-preview.1"}
        )
        own = identity.get("authenticatedUser", {}).get("id")
        if not own:
            raise ConnectionUnavailable("ADO did not return the signed-in identity")
        return {"project_id": project["id"], "project": project["name"], "identity": own}

    def repositories(self):
        return self.get(f"{self.project}/_apis/git/repositories").get("value", [])

    def pull_request(self, repository: str, number: int):
        return self.get(
            f"{self.project}/_apis/git/repositories/{quote(repository, safe='')}/pullRequests/{number}"
        )

    def active_prs(self):
        result = []
        skip = 0
        while True:
            page = self.get(
                f"{self.project}/_apis/git/pullrequests",
                {
                    "searchCriteria.status": "active",
                    "$top": 100,
                    "$skip": skip,
                },
            ).get("value", [])
            result.extend(page)
            if len(page) < 100:
                return result
            skip += len(page)
            if skip > 5000:
                raise ConnectionUnavailable("Too many active PRs; narrow the configured project")

    def candidates(self, event: dict):
        if event.get("repository") and event.get("pr"):
            return [self.pull_request(event["repository"], int(event["pr"]))]
        if event.get("build"):
            build = self.get(f"{self.project}/_apis/build/builds/{int(event['build'])}")
            match = re.fullmatch(r"refs/pull/(\d+)/merge", build.get("sourceBranch", ""))
            # Main/release builds after a merge are never review triggers, even
            # when they retain PR trigger metadata or match an older PR revision.
            if (
                match is None
                or build.get("status") != "completed"
                or build.get("result") != "succeeded"
            ):
                return []
            repository = build.get("repository", {}).get("id")
            if not repository:
                return []
            pr = self.pull_request(repository, int(match.group(1)))
            revisions = {
                pr.get("lastMergeSourceCommit", {}).get("commitId"),
                pr.get("lastMergeCommit", {}).get("commitId"),
            }
            return (
                [pr] if build.get("sourceVersion") and build["sourceVersion"] in revisions else []
            )
        # Only an explicit manual/optional reconciliation may scan active PRs.
        # An incomplete or unrelated event must never fan out to other PRs.
        return [] if event else self.active_prs()

    def ci(self, pr: dict) -> tuple[bool, str]:
        project = pr.get("repository", {}).get("project", {}).get("id")
        if not project:
            return False, "PR project identity missing"
        artifact = f"vstfs:///CodeReview/CodeReviewId/{project}/{pr['pullRequestId']}"
        evaluations = self.get(
            f"{self.project}/_apis/policy/evaluations",
            {"artifactId": artifact, "api-version": "7.1-preview.1"},
        ).get("value", [])
        required = [
            e
            for e in evaluations
            if e.get("configuration", {}).get("type", {}).get("id", "").lower() == BUILD_POLICY
            and e.get("configuration", {}).get("isBlocking", True)
            and e.get("configuration", {}).get("isEnabled", True)
        ]
        if not required:
            return False, "No required build policy found; automatic review is held"
        revisions = {
            pr.get("lastMergeSourceCommit", {}).get("commitId"),
            pr.get("lastMergeCommit", {}).get("commitId"),
        }
        revisions.discard(None)
        if not revisions:
            return False, "Current PR revision missing"
        for evaluation in required:
            if evaluation.get("status", "").lower() != "approved":
                return False, "Required CI is still pending or failed"
            build_id = evaluation.get("context", {}).get("buildId")
            if not build_id:
                return False, "Build policy has no verifiable run"
            build = self.get(f"{self.project}/_apis/build/builds/{int(build_id)}")
            if build.get("status") != "completed" or build.get("result") != "succeeded":
                return False, "Required build has not succeeded"
            if build.get("sourceVersion") not in revisions:
                return False, "Successful CI belongs to an older PR revision"
        return True, "Required CI passed for the current revision"


class CodexQueue:
    """No shell, no private Codex database writes, no new API credentials."""

    def __init__(self, command: str):
        self.command = command

    def executable(self) -> str:
        resolved = shutil.which(self.command)
        if resolved:
            return resolved
        if Path(self.command).is_absolute() and Path(self.command).is_file():
            return self.command
        raise ConnectionUnavailable("Codex executable not found; select the installed CLI")

    def diagnose(self):
        try:
            executable = self.executable()
            version = subprocess.run(
                [executable, "--version"], capture_output=True, text=True, timeout=10
            )
            if version.returncode:
                raise ConnectionUnavailable("Codex CLI did not return a version")
            check = subprocess.run(
                [executable, "queue", "--help"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            capable = check.returncode == 0 and all(
                option in check.stdout for option in ("--thread", "--message")
            )
            return {
                "cli": True,
                "version": version.stdout.strip()[:100],
                "ready": capable,
                "readiness": "queue_capability",
                "detail": "Existing desktop-chat queue command is available. Live receipt and chat execution are tracked separately; daemon status is not a prerequisite for this queue route."
                if capable
                else "CLI installed, but its existing-thread queue command is unavailable. Select a Codex CLI supporting queue --thread and --message.",
            }
        except (OSError, subprocess.TimeoutExpired, ConnectionUnavailable):
            return {
                "cli": False,
                "ready": False,
                "detail": "Codex connection unavailable; verify its configured executable",
            }

    def send(self, thread_id: str, message: str):
        try:
            executable = self.executable()
            result = subprocess.run(
                [executable, "queue", "--thread", thread_id, "--message", message],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            raise DeliveryUnknown("Delivery timed out; check the chat before retrying") from None
        except OSError:
            raise ConnectionUnavailable("Codex could not be started") from None
        if result.returncode:
            # Only known pre-delivery failures are safe to retry; raw output may
            # contain credentials or prompts and never leaves this adapter.
            output = (result.stderr + result.stdout).lower()
            if any(
                s in output
                for s in (
                    "failed to connect",
                    "session not found",
                    "thread not found",
                    "no matching session",
                )
            ):
                raise ConnectionUnavailable(
                    "Codex chat or shared control service unavailable; reconnect and retry"
                )
            raise DeliveryUnknown(
                "Codex did not acknowledge delivery; check the chat before retrying"
            )
        receipt = re.search(
            r"Queued message ([0-9a-fA-F-]{36}) for thread ([0-9a-fA-F-]{36})\.", result.stdout
        )
        if not receipt or receipt.group(2).lower() != thread_id.lower():
            raise DeliveryUnknown(
                "Codex exited without a matching queue receipt; verify delivery before retrying"
            )
        from uuid import UUID

        try:
            message_id = str(UUID(receipt.group(1)))
        except ValueError:
            raise DeliveryUnknown(
                "Codex returned an invalid queue receipt; verify delivery before retrying"
            ) from None
        return {"message_id": message_id, "thread_id": thread_id}
