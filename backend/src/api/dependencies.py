"""Request-scoped dependencies: authenticated user (PRD004-FR-3)."""

from typing import Annotated

from fastapi import Depends, Request

from api.problem import ProblemError
from identity_access.application.use_cases.authenticate_token import AuthenticateToken
from identity_access.domain.values import AuthenticatedUser
from shared.domain.result import Err


def get_current_user(request: Request) -> AuthenticatedUser:
    """Extract and verify the Bearer token; 401 problem+json otherwise.

    Detail strings never distinguish unknown users or reveal token internals.
    """
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ProblemError(
            status=401, title="Unauthorized", detail="Missing or malformed credentials"
        )

    use_case = AuthenticateToken(token_codec=request.app.state.identity.token_codec)
    result = use_case.execute(token)
    if isinstance(result, Err):
        raise ProblemError(
            status=401, title="Unauthorized", detail="Invalid or expired credentials"
        )
    return result.value


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]
