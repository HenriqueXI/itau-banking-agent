"""RFC 9457 problem+json helpers (api.md conventions).

Bodies carry {type, title, status, detail, correlation_id} — never stack
traces or internal identifiers.
"""

import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

PROBLEM_MEDIA_TYPE = "application/problem+json"


class ProblemError(Exception):
    """Raised by routes/dependencies; rendered by the handler below."""

    def __init__(self, *, status: int, title: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.title = title
        self.detail = detail


def problem_response(*, status: int, title: str, detail: str) -> JSONResponse:
    body: dict[str, Any] = {
        "type": "about:blank",
        "title": title,
        "status": status,
        "detail": detail,
        "correlation_id": str(uuid.uuid4()),
    }
    return JSONResponse(status_code=status, content=body, media_type=PROBLEM_MEDIA_TYPE)


def register_problem_handler(app: FastAPI) -> None:
    @app.exception_handler(ProblemError)
    async def _handle(request: Request, exc: ProblemError) -> JSONResponse:
        return problem_response(status=exc.status, title=exc.title, detail=exc.detail)
