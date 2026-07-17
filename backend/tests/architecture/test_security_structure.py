"""PRD-015 structural proofs for banking write paths and authorization metadata."""

import ast
from pathlib import Path

import pytest

from conversation.domain.tools import REGISTRY
from identity_access.domain.authorization import MATRIX, Action

BACKEND = Path(__file__).resolve().parents[2]
WRITE_METHODS = {"update_card_limit", "execute_pix"}
ALLOWED_WRITE_CALLERS = {
    "src/banking/application/use_cases/execute_card_limit_change.py",
    "src/banking/application/use_cases/execute_pix_transfer.py",
    # The simulated MCP server is the external contract implementation, not an
    # application call path.  It must expose the write methods for contract tests.
    "src/mcp_server/main.py",
}


@pytest.mark.adversarial
def test_banking_writes_exist_only_in_pending_operation_executors() -> None:
    """No graph or adapter entry point may call an MCP write directly."""
    offenders: list[str] = []
    for path in (BACKEND / "src").rglob("*.py"):
        relative = path.relative_to(BACKEND).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in WRITE_METHODS
                and relative not in ALLOWED_WRITE_CALLERS
            ):
                offenders.append(f"{relative}:{node.lineno}:{node.func.attr}")
    assert not offenders, f"Banking write outside pending-operation executor: {offenders}"


@pytest.mark.adversarial
def test_registered_tool_actions_are_authorization_matrix_actions() -> None:
    actions = {spec.action for spec in REGISTRY.values()}
    assert actions <= {action.value for action in Action}
    assert set(MATRIX) == set(Action)
