"""Exercise the real Node framing against a synthetic socket, never a live chat."""

import json
import os
import shutil
import socket
import struct
import subprocess
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from .conftest import THREAD


@pytest.mark.parametrize(
    "scenario", ["active", "idle", "remove", "disconnect", "wrong-owner", "malformed-receipt"]
)
def test_versioned_native_protocol_and_safe_receipts(tmp_path, scenario):
    node = shutil.which("node")
    assert node, "Node.js 22 is required for desktop bridge protocol tests"
    directory = tmp_path / "ipc"
    directory.mkdir(mode=0o700)
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(directory / "ipc.sock"))
    server.listen(1)
    server.settimeout(8)
    methods, errors = [], []
    client_id, owner_id, message_id = str(uuid4()), str(uuid4()), str(uuid4())

    def serve():
        try:
            connection, _ = server.accept()
            with connection:
                connection.settimeout(8)

                def read(n):
                    b = b""
                    while len(b) < n:
                        part = connection.recv(n - len(b))
                        if not part:
                            raise EOFError
                        b += part
                    return b

                while True:
                    try:
                        n = struct.unpack("<I", read(4))[0]
                    except EOFError:
                        return
                    request = json.loads(read(n))
                    method = request["method"]
                    methods.append(method)
                    result = {
                        "type": "response",
                        "requestId": request["requestId"],
                        "resultType": "success",
                        "handledByClientId": owner_id,
                    }
                    if method == "initialize":
                        result["result"] = {"clientId": client_id}
                    elif method == "thread-owner-discovery":
                        assert request["params"]["conversationId"] == THREAD
                        assert request["version"] == 1
                        result["result"] = {"supportsUntrustedAppInput": scenario != "wrong-owner"}
                    else:
                        assert request["targetClientId"] == owner_id
                        assert request["params"]["conversationId"] == THREAD
                        if scenario == "disconnect":
                            return
                        if scenario == "idle":
                            result.update(
                                resultType="error",
                                error=f"Cannot steer conversation {THREAD} because its active turn already ended",
                            )
                        elif scenario == "remove":
                            assert method == "thread-follower-remove-queued-message"
                            assert request["params"]["messageId"] == message_id
                            result["result"] = {
                                "removed": {"message": {"text": "private synthetic content"}}
                            }
                        else:
                            assert method == "thread-follower-steer-turn"
                            assert request["params"]["clientUserMessageId"] == message_id
                            assert request["params"]["input"][0]["text_elements"] == []
                            result["result"] = {
                                "result": {}
                                if scenario == "malformed-receipt"
                                else {"turnId": "active-turn"}
                            }
                    body = json.dumps(result).encode()
                    frame = struct.pack("<I", len(body)) + body
                    connection.sendall(frame[:2])
                    connection.sendall(frame[2:])
        except Exception as error:
            errors.append(error)
        finally:
            server.close()

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    helper = Path(__file__).parents[1] / "src/agent_control/desktop_bridge.cjs"
    result = subprocess.run(
        [node, str(helper)],
        input=json.dumps(
            {
                "action": "remove" if scenario == "remove" else "steer",
                "thread_id": THREAD,
                "message_id": message_id,
                "message": "Relevant synthetic context",
            }
        ),
        text=True,
        capture_output=True,
        timeout=16,
        env={**os.environ, "CODEX_HOME": str(tmp_path)},
    )
    worker.join(2)
    assert not worker.is_alive() and not errors
    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert (
        output["status"]
        == {
            "active": "steered",
            "idle": "idle",
            "remove": "removed",
            "disconnect": "unknown",
            "wrong-owner": "unavailable",
            "malformed-receipt": "unknown",
        }[scenario]
    )
    assert methods[:2] == ["initialize", "thread-owner-discovery"]
    assert not any(x in methods for x in ["thread/start", "thread/resume", "thread/fork"])
    assert "private synthetic content" not in result.stdout
    if scenario == "active":
        assert output["message_id"] == message_id and output["thread_id"] == THREAD
