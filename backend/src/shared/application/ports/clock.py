"""Clock port: injectable time source so domain logic is testable."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, always UTC-aware."""
        ...
