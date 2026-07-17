"""POST /api/auth/login and /api/auth/step-up/request (api.md surface)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from api.dependencies import CurrentUser
from api.problem import ProblemError
from banking.adapters.outbound.postgres.pending_operation_repository import (
    PostgresPendingOperationRepository,
)
from banking.domain.pending_operation import OperationStatus
from identity_access.adapters.outbound.postgres.step_up_repository import (
    PostgresStepUpChallengeRepository,
)
from identity_access.adapters.outbound.postgres.user_repository import PostgresUserRepository
from identity_access.application.dto import LoginCommand, RequestStepUpCommand
from identity_access.application.use_cases.login import Login
from identity_access.application.use_cases.request_step_up import RequestStepUp
from shared.adapters.event_publisher import event_transaction
from shared.domain.result import Err

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str
    expires_in: int


class StepUpRequestBody(BaseModel):
    operation_hash: str = Field(min_length=1)


class StepUpRequestResponse(BaseModel):
    challenge_id: uuid.UUID
    expires_at: datetime
    delivery: str
    dev_code: str | None
    """Demo-only simulated delivery; null outside dev-mode environments."""


@router.post("/login", response_model=LoginResponse)
async def login(request: Request, body: LoginRequest) -> LoginResponse:
    container = request.app.state.container
    identity = request.app.state.identity
    async with container.session_factory() as session:
        use_case = Login(
            users=PostgresUserRepository(session),
            password_hasher=identity.password_hasher,
            token_codec=identity.token_codec,
            clock=container.clock,
            id_generator=container.id_generator,
            jwt_ttl_minutes=container.settings.jwt_ttl_minutes,
        )
        result = await use_case.execute(LoginCommand(email=body.email, password=body.password))

    if isinstance(result, Err):
        # Identical response for unknown user and wrong password (PRD004-FR-2).
        raise ProblemError(status=401, title="Unauthorized", detail="Invalid credentials")
    return LoginResponse(
        access_token=result.value.access_token,
        token_type=result.value.token_type,
        expires_in=result.value.expires_in_seconds,
    )


@router.post("/step-up/request", response_model=StepUpRequestResponse)
async def request_step_up(
    request: Request, body: StepUpRequestBody, user: CurrentUser
) -> StepUpRequestResponse:
    container = request.app.state.container
    identity = request.app.state.identity
    settings = container.settings
    async with container.session_factory() as session, session.begin(), event_transaction(session):
        operation = await PostgresPendingOperationRepository(session).get_for_user(
            body.operation_hash, user.id, lock=True
        )
        if (
            operation is None
            or operation.tool != "fazer_pix"
            or operation.status is not OperationStatus.PENDING_STEP_UP
            or container.clock.now() >= operation.expires_at
        ):
            raise ProblemError(
                status=422,
                title="Unprocessable",
                detail="No active PIX step-up operation for this request",
            )
        use_case = RequestStepUp(
            challenges=PostgresStepUpChallengeRepository(session),
            code_generator=identity.code_generator,
            clock=container.clock,
            id_generator=container.id_generator,
            event_publisher=identity.event_publisher,
            ttl_minutes=settings.step_up_ttl_minutes,
            reveal_code=settings.env != "prod",
        )
        result = await use_case.execute(
            RequestStepUpCommand(user=user, operation_hash=body.operation_hash)
        )

    if isinstance(result, Err):
        raise ProblemError(status=422, title="Unprocessable", detail=result.error.message)
    issued = result.value
    return StepUpRequestResponse(
        challenge_id=issued.challenge_id,
        expires_at=issued.expires_at,
        delivery="simulated",
        dev_code=issued.dev_code,
    )
