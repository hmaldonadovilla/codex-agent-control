"use strict";
// Versioned desktop IPC adapter. No chats, servers or sessions are created here.
// Only these three operations are exposed; never accept arbitrary RPC from HTTP.
const net = require("node:net");
const crypto = require("node:crypto");
const os = require("node:os");
const path = require("node:path");
const fs = require("node:fs");
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

async function main(request) {
  if (!["probe", "steer", "remove"].includes(request.action) || !uuid.test(request.thread_id) ||
      (request.action === "remove" && !uuid.test(request.message_id)) ||
      (request.action === "steer" && (!uuid.test(request.message_id) || typeof request.message !== "string" || !request.message.trim()))) {
    return {status: "unavailable"};
  }
  const endpoint = process.platform === "win32" ? "\\\\.\\pipe\\codex-ipc" :
    path.join(process.env.CODEX_HOME || path.join(os.homedir(), ".codex"), "ipc", "ipc.sock");
  if (process.platform !== "win32") {
    const stat = fs.lstatSync(endpoint), parent = fs.lstatSync(path.dirname(endpoint));
    if (!stat.isSocket() || stat.uid !== process.getuid() || parent.uid !== process.getuid() || parent.mode & 0o022) return {status: "unavailable"};
  }
  let client = "initializing-client", buffer = Buffer.alloc(0), mutating = false;
  const pending = new Map(), socket = net.connect(endpoint);
  const fail = () => { for (const p of pending.values()) p.reject(Error("connection-lost")); pending.clear(); };
  socket.on("error", fail); socket.on("close", fail);
  socket.on("data", data => {
    buffer = Buffer.concat([buffer, data]);
    while (buffer.length >= 4) {
      const n = buffer.readUInt32LE();
      if (!n || n > 16 * 1024 * 1024) { socket.destroy(); return; }
      if (buffer.length < n + 4) return;
      let response;
      try { response = JSON.parse(buffer.subarray(4, n + 4).toString("utf8")); }
      catch { socket.destroy(); return; }
      buffer = buffer.subarray(n + 4);
      // Discard unsolicited broadcasts: neither log nor retain chat contents.
      if (response.type !== "response") continue;
      const waiter = pending.get(response.requestId);
      if (waiter) { pending.delete(response.requestId); waiter.resolve(response); }
    }
  });
  function call(method, params, version, target, mutation = false) {
    return new Promise((resolve, reject) => {
      const requestId = crypto.randomUUID();
      pending.set(requestId, {resolve, reject});
      const body = Buffer.from(JSON.stringify({type: "request", requestId, sourceClientId: client,
        method, params, version, targetClientId: target, timeoutMs: 8000}));
      const header = Buffer.alloc(4); header.writeUInt32LE(body.length);
      if (mutation) mutating = true;
      socket.write(Buffer.concat([header, body]));
    });
  }
  const timer = setTimeout(() => socket.destroy(), 12000);
  try {
    await new Promise((resolve, reject) => { socket.once("connect", resolve); socket.once("error", reject); });
    const init = await call("initialize", {clientType: "agent-desk"}, 0);
    if (init.resultType !== "success" || !uuid.test(init.result?.clientId)) return {status: "unavailable"};
    client = init.result.clientId;
    const owner = await call("thread-owner-discovery", {hostId: "local", conversationId: request.thread_id}, 1);
    if (owner.resultType !== "success" || !uuid.test(owner.handledByClientId) || owner.result?.supportsUntrustedAppInput !== true) return {status: "unavailable"};
    if (request.action === "probe") return {status: "ready", protocol: "desktop-ipc-v1"};
    const method = request.action === "steer" ? "thread-follower-steer-turn" : "thread-follower-remove-queued-message";
    const params = request.action === "steer" ? {
      conversationId: request.thread_id,
      input: [{type: "text", text: request.message, text_elements: []}],
      clientUserMessageId: request.message_id,
      restoreMessage: {id: request.message_id, text: request.message, context: {}},
    } : {conversationId: request.thread_id, messageId: request.message_id};
    const response = await call(method, params, 1, owner.handledByClientId, true);
    if (response.resultType !== "success") {
      const error = typeof response.error === "string" ? response.error : "";
      // Only an explicit pre-admission idle response allows native queue fallback.
      if (request.action === "steer" && (error === "no active turn to steer" ||
          error === `Conversation ${request.thread_id} is not being streamed.` ||
          error === `Cannot steer conversation ${request.thread_id} because its active turn already ended`)) return {status: "idle"};
      // Router discovery rejection guarantees that the method was never forwarded.
      if (error === "no-client-found") return {status: "unavailable"};
      return {status: "unknown"};
    }
    const result = response.result;
    if (request.action === "steer") {
      if (!result?.result || typeof result.result.turnId !== "string") return {status: "unknown"};
      return {status: "steered", thread_id: request.thread_id, message_id: request.message_id, turn_id: result.result.turnId};
    }
    if (!result || !("removed" in result)) return {status: "unknown"};
    return {status: result.removed == null ? "absent" : "removed"};
  } catch { return {status: mutating ? "unknown" : "unavailable"}; }
  finally { clearTimeout(timer); socket.destroy(); }
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", chunk => { input += chunk; if (input.length > 128000) process.exit(1); });
process.stdin.on("end", async () => {
  try { console.log(JSON.stringify(await main(JSON.parse(input)))); }
  catch { console.log(JSON.stringify({status: "unavailable"})); }
});
