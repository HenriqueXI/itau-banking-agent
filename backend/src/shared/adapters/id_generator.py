"""System implementation of the IdGenerator port."""

import uuid


class UuidIdGenerator:
    def new_id(self) -> uuid.UUID:
        return uuid.uuid4()
