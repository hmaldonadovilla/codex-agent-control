from agent_control.mcp_probe import HTML, URI, respond


def test_probe_is_read_only_and_does_not_send_a_followup():
    tool = respond({"method": "tools/list"})["tools"][0]
    assert tool["annotations"]["readOnlyHint"] is True
    assert tool["_meta"]["ui"]["resourceUri"] == URI
    resource = respond({"method": "resources/read", "params": {"uri": URI}})
    assert resource["contents"][0]["mimeType"] == "text/html;profile=mcp-app"
    assert "ui/initialize" in HTML
    assert "call('ui/message'" not in HTML
    assert "sendFollowUpMessage(" not in HTML
