"""uvicorn entrypoint: `uvicorn api.main:app`, or `python -m api.main` to run on a host.

The module runner exists because psycopg's async mode cannot run on Windows'
default ProactorEventLoop, and uvicorn >= 0.36 hardcodes that loop in its
factory. `python -m api.main` picks a SelectorEventLoop on Windows and behaves
exactly like plain uvicorn everywhere else.
"""

import asyncio
import os
import sys

import uvicorn

from api.app import create_app

app = create_app()


def run() -> None:
    host = os.environ.get("API_HOST", "127.0.0.1")
    port = int(os.environ.get("API_PORT", "8000"))
    if sys.platform == "win32":
        config = uvicorn.Config("api.main:app", host=host, port=port)
        server = uvicorn.Server(config)
        asyncio.run(server.serve(), loop_factory=asyncio.SelectorEventLoop)
    else:
        uvicorn.run("api.main:app", host=host, port=port)


if __name__ == "__main__":
    run()
