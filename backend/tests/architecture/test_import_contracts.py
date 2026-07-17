"""NFR-8: import-linter contracts are committed AND actually fail on violation.

Runs `lint-imports` as a subprocess — same command CI uses — first on the
clean tree, then with a deliberate framework import injected into a domain
module.
"""

import shutil
import subprocess
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
VIOLATION_FILE = BACKEND_DIR / "src" / "banking" / "domain" / "_deliberate_violation.py"


def _run_lint_imports() -> subprocess.CompletedProcess[str]:
    executable = shutil.which("lint-imports")
    assert executable, "lint-imports not on PATH (run tests via `uv run pytest`)"
    return subprocess.run(
        [executable, "--no-cache"],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_clean_tree_passes_contracts() -> None:
    result = _run_lint_imports()
    assert result.returncode == 0, result.stdout + result.stderr


def test_framework_import_in_domain_fails_contracts() -> None:
    VIOLATION_FILE.write_text("import fastapi  # deliberate violation for NFR-8\n")
    try:
        result = _run_lint_imports()
    finally:
        VIOLATION_FILE.unlink()

    assert result.returncode != 0, "import-linter must fail when domain imports fastapi"
    assert "Domain imports no frameworks" in result.stdout
