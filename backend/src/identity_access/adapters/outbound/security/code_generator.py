"""CSPRNG-backed 6-digit step-up codes."""

import secrets


class SecretsCodeGenerator:
    def generate(self) -> str:
        return f"{secrets.randbelow(1_000_000):06d}"
