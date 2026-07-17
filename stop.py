#!/usr/bin/env python3
"""Stop the Itaú Banking Agent local Docker Compose stack.

Run ``python stop.py`` from the repository root. By default, containers and
networks are removed while persisted data (PostgreSQL, Chroma, and Ollama
models) is kept. Use ``--volumes`` only when a complete local reset is wanted.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
COMPOSE = ("docker", "compose", "--env-file", ".env", "-f", "infra/docker-compose.yml")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Finaliza os serviços locais do Itaú Banking Agent."
    )
    parser.add_argument(
        "--volumes",
        action="store_true",
        help="remove também os volumes e todos os dados locais persistidos",
    )
    args = parser.parse_args()

    if not shutil.which("docker"):
        print("Docker Desktop não foi encontrado no PATH.", file=sys.stderr)
        return 1

    command = [*COMPOSE, "down"]
    if args.volumes:
        command.append("--volumes")

    try:
        subprocess.run(command, cwd=ROOT, check=True)
    except subprocess.CalledProcessError as error:
        print(f"Falha ao finalizar os serviços: {error}", file=sys.stderr)
        return 1

    if args.volumes:
        print("Serviços finalizados e volumes locais removidos.")
    else:
        print("Serviços finalizados. Os dados locais foram preservados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
