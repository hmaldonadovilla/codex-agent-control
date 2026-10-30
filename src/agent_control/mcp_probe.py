"""Read-only MCP Apps host-capability probe; never sends a follow-up message."""

import json
import sys

URI = "ui://agent-desk/connection-probe.html"
HTML = """<!doctype html><html><head><meta charset="utf-8"></head>
<body style="font:14px system-ui;padding:20px;color:#26352b;background:#f6f7f1">
<h2>Agent Desk connection probe</h2><p id="status">Checking the host bridge…</p>
<pre id="result" style="white-space:pre-wrap"></pre>
<p>This probe does not send messages or start a review.</p>
<script>
let nextId=1;
const waiting=new Map();
function call(method,params){return new Promise((resolve,reject)=>{
 const id=nextId++;waiting.set(id,{resolve,reject});
 window.parent.postMessage({jsonrpc:'2.0',id,method,params},'*');
 setTimeout(()=>{if(waiting.has(id)){waiting.delete(id);reject(Error('Host did not respond'));}},10000);
});}
window.addEventListener('message',e=>{
 if(e.source!==window.parent||!e.data||e.data.jsonrpc!=='2.0')return;
 const p=waiting.get(e.data.id);if(!p)return;waiting.delete(e.data.id);
 if(e.data.error)p.reject(Error('Host refused initialization'));else p.resolve(e.data.result);
});
(async()=>{try{
 const result=await call('ui/initialize',{appInfo:{name:'Agent Desk connection probe',version:'0.1.0'},
 appCapabilities:{},protocolVersion:'2026-01-26'});
 window.parent.postMessage({jsonrpc:'2.0',method:'ui/notifications/initialized'},'*');
 document.querySelector('#status').textContent='Host initialization received';
 document.querySelector('#result').textContent=JSON.stringify({
 hostInfo:result.hostInfo,hostCapabilities:result.hostCapabilities,
 followUpAlias:typeof window.openai?.sendFollowUpMessage==='function'},null,2);
}catch(e){document.querySelector('#status').textContent=e.message;}})();
</script></body></html>"""


def respond(request):
    method = request.get("method")
    if method == "initialize":
        return {
            "protocolVersion": request.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}, "resources": {}},
            "serverInfo": {"name": "agent-desk-connection-probe", "version": "0.1.0"},
            "instructions": "Read-only capability probe for an event-driven local chat bridge. The probe does not send messages, run reviews or grant approval.",
        }
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": "open_connection_probe",
                    "description": "Open a read-only Agent Desk panel to test whether this host supports MCP Apps messaging. No follow-up is sent.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    },
                    "annotations": {
                        "readOnlyHint": True,
                        "destructiveHint": False,
                        "openWorldHint": False,
                    },
                    "_meta": {"ui": {"resourceUri": URI}, "openai/outputTemplate": URI},
                }
            ]
        }
    if method == "tools/call":
        if request.get("params", {}).get("name") != "open_connection_probe":
            raise ValueError("Unknown tool")
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Agent Desk host-capability probe. Inspect the panel's capability report; no message has been sent.",
                }
            ],
            "_meta": {"ui": {"resourceUri": URI}},
        }
    if method == "resources/list":
        return {
            "resources": [
                {
                    "uri": URI,
                    "name": "Agent Desk connection probe",
                    "mimeType": "text/html;profile=mcp-app",
                }
            ]
        }
    if method == "resources/read":
        if request.get("params", {}).get("uri") != URI:
            raise ValueError("Unknown resource")
        return {
            "contents": [
                {
                    "uri": URI,
                    "mimeType": "text/html;profile=mcp-app",
                    "text": HTML,
                    "_meta": {"ui": {"prefersBorder": True}},
                }
            ]
        }
    if method == "ping":
        return {}
    raise ValueError("Unsupported method")


def main():
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except ValueError:
            continue
        if "id" not in request:
            continue
        try:
            response = {"jsonrpc": "2.0", "id": request["id"], "result": respond(request)}
        except (ValueError, TypeError, KeyError):
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32601, "message": "Unsupported probe request"},
            }
        print(json.dumps(response), flush=True)


if __name__ == "__main__":
    main()
