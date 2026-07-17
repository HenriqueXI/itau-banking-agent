from pathlib import Path

import yaml


def test_mcp_server_has_no_published_host_port_in_default_compose() -> None:
    repository_root = Path(__file__).parents[4]
    compose = yaml.safe_load((repository_root / "infra" / "docker-compose.yml").read_text())

    server = compose["services"]["mcp-server"]

    assert "ports" not in server
    assert server["healthcheck"]["test"]
    assert compose["services"]["backend"]["depends_on"]["mcp-server"] == {
        "condition": "service_healthy"
    }
