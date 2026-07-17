"""PasswordHasher port — argon2 adapter in production, trivial fake in tests."""

from typing import Protocol


class PasswordHasher(Protocol):
    def hash(self, plain: str) -> str: ...

    def verify(self, plain: str, hashed: str) -> bool:
        """True iff `plain` matches. Must never raise on malformed hashes."""
        ...

    def dummy_hash(self) -> str:
        """A valid hash of a random secret — verified against when the user
        does not exist, so unknown-user and wrong-password take similar time."""
        ...
