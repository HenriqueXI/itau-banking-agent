"""Chroma container for the vector store integration tests (testcontainers)."""

import time
from collections.abc import Iterator

import httpx
import pytest
from testcontainers.core.container import DockerContainer

# Must match the compose stack's server AND the pinned client (infra/docker-compose.yml):
# a client/server major mismatch fails every call with an opaque HTTP 500.
CHROMA_IMAGE = "chromadb/chroma:1.0.15"


def _wait_for_heartbeat(url: str, timeout: float = 120.0) -> None:
    """Poll the v2 heartbeat — readiness log lines differ across Chroma images."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/api/v2/heartbeat", timeout=5).status_code == 200:
                return
        except httpx.HTTPError as exc:
            last_error = exc
        time.sleep(1)
    raise TimeoutError(f"chroma not ready at {url} after {timeout}s (last error: {last_error})")


@pytest.fixture(scope="session")
def chroma_url() -> Iterator[str]:
    container = DockerContainer(CHROMA_IMAGE).with_exposed_ports(8000)
    with container:
        url = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(8000)}"
        _wait_for_heartbeat(url)
        yield url
