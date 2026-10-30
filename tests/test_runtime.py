import pytest

from agent_control.runtime import service_units, unit_quote, validate_remote_tunnel


def test_tunnel_host_refuses_added_dashboard_port():
    value = {"id": "dedicated.euw", "url": "https://example-8769.euw.devtunnels.ms"}
    port = {"portNumber": 8769, "protocol": "http", "portUri": value["url"] + "/"}
    remote = {"tunnel": {"tunnelId": value["id"], "ports": [port]}}
    validate_remote_tunnel(value, remote)
    remote["tunnel"]["ports"].append({"portNumber": 8768})
    with pytest.raises(ValueError, match="only ingress"):
        validate_remote_tunnel(value, remote)


def test_units_keep_configuration_out_of_command_lines(tmp_path):
    units = service_units(tmp_path / "my project", tmp_path / ".local", tmp_path)
    assert set(units) == {
        "agent-desk-control.service",
        "agent-desk-ingress.service",
        "agent-desk-tunnel.service",
        "agent-desk.target",
    }
    assert "UMask=0077" in units["agent-desk-control.service"]
    assert '"agent_control.runtime" "host" "--state"' in units["agent-desk-tunnel.service"]
    assert "--allow-anonymous" not in "".join(units.values())
    assert unit_quote('path%/$name/"x"') == '"path%%/$$name/\\"x\\""'
    with pytest.raises(ValueError):
        unit_quote("path\nExecStart=bad")
