"""IdGenerator port: injectable id source so entities/events are testable."""

import uuid
from typing import Protocol


class IdGenerator(Protocol):
    def new_id(self) -> uuid.UUID: ...
