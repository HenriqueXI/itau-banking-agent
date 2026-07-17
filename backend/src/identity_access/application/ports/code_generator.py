"""StepUpCodeGenerator port — `secrets`-backed adapter, fixed code in tests."""

from typing import Protocol


class StepUpCodeGenerator(Protocol):
    def generate(self) -> str:
        """A 6-digit numeric code (BR-5.1)."""
        ...
