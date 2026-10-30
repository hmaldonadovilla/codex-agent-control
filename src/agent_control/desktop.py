"""Optional versioned IPC access to the owner of an existing desktop chat.

Queue CLI remains the portable fallback. Never start another app-server to obtain
steering: it would not be the running desktop owner. Ambiguous sends never fall back.
"""

import json
import shutil
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

from .adapters import CodexQueue, DeliveryUnknown


class DesktopBridge:
    def __init__(self, command: str, node_command: str = ""):
        self.command, self.node_command = command, node_command

    def runtime(self):
        command = self.node_command
        if not command and self.command.lower().endswith(".exe"):
            powershell = shutil.which("powershell.exe")
            if powershell:
                result = subprocess.run(
                    [
                        powershell,
                        "-NoProfile",
                        "-NonInteractive",
                        "-Command",
                        "(Get-Command node.exe -ErrorAction SilentlyContinue).Source",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=8,
                )
                command = result.stdout.strip()
                if command:
                    command = subprocess.run(
                        ["wslpath", "-u", command],
                        capture_output=True,
                        text=True,
                        check=True,
                        timeout=5,
                    ).stdout.strip()
        command = command or shutil.which("node")
        if not command:
            return None
        helper = str(Path(__file__).with_name("desktop_bridge.cjs").resolve())
        if command.lower().endswith(".exe"):
            helper = subprocess.run(
                ["wslpath", "-w", helper], capture_output=True, text=True, check=True, timeout=5
            ).stdout.strip()
        return [command, helper]

    def call(self, action: str, thread_id: str, **kwargs) -> dict:
        thread_id = str(UUID(thread_id))
        started = False
        try:
            runtime = self.runtime()
            if runtime is None:
                return {"status": "unavailable"}
            started = True
            result = subprocess.run(
                runtime,
                input=json.dumps({"action": action, "thread_id": thread_id, **kwargs}),
                capture_output=True,
                text=True,
                timeout=18,
            )
            if result.returncode:
                return {"status": "unknown" if action != "probe" else "unavailable"}
            value = json.loads(result.stdout)
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"status": "unknown" if started and action != "probe" else "unavailable"}

    def diagnose(self, thread_id):
        return self.call("probe", thread_id)

    def send(self, thread_id, message):
        result = self.call("steer", thread_id, message=message, message_id=str(uuid4()))
        if result.get("status") == "steered":
            if result.get("thread_id") != thread_id or not result.get("turn_id"):
                raise DeliveryUnknown("Desktop steering receipt did not match the linked chat")
            UUID(result["message_id"])
            return result | {"mode": "steer"}
        if result.get("status") not in {"idle", "unavailable"}:
            raise DeliveryUnknown(
                "Desktop steering outcome is uncertain; no queue fallback attempted"
            )
        return CodexQueue(self.command).send(thread_id, message) | {"mode": "queue"}

    def remove(self, thread_id, message_id):
        return self.call("remove", thread_id, message_id=str(UUID(message_id)))
