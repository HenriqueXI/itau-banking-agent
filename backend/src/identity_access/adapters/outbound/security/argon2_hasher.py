"""Argon2 password hashing (database.md §1: demo hashes are argon2)."""

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError


class Argon2PasswordHasher:
    def __init__(self) -> None:
        self._hasher = PasswordHasher()
        # Hash of a random throwaway secret, verified against for unknown
        # users so login timing doesn't reveal account existence.
        self._dummy = self._hasher.hash(secrets.token_hex(16))

    def hash(self, plain: str) -> str:
        return self._hasher.hash(plain)

    def verify(self, plain: str, hashed: str) -> bool:
        try:
            return self._hasher.verify(hashed, plain)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    def dummy_hash(self) -> str:
        return self._dummy
