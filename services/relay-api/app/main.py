from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse
from pydantic.json_schema import models_json_schema
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from .auth import CurrentUser, require_current_user
from .contracts import (
    ActionDispatchRecord,
    ActionRecord,
    ActionStatusResponse,
    Approval,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    CallContract,
    Commitment,
    DispatchClaim,
    Disruption,
    Edge,
    GmailEvidenceRef,
    HandoffResponse,
    OutcomeValidation,
    Problem,
    Provenance,
    ProviderEvent,
    RecordCallOutcomeInput,
    SourceEventEnvelope,
)
from .domain.impact import PlanningOptions
from .domain.product import (
    ActionAuditView,
    ActionOutcomeView,
    ApprovalActionSummary,
    ApprovalBatchView,
    AuditEventView,
    DashboardView,
    PickupContactCommand,
    PickupContactResponse,
    PickerContactView,
    PickerPhoneView,
    PickerSessionView,
    PlanTimelineItem,
    RegisterDeviceRequest,
)
from .routes.actions import get_action_repository, router as actions_router
from .routes.google import pickup_router, router as google_router
from .routes.pubsub import router as pubsub_router
from .routes.product import router as product_router
from .routes.repair_plans import CreateRepairPlanRequest, CreateRepairPlanResponse, router as repair_plans_router
from .routes.webhooks import router as webhooks_router
from .observability import RelayLogEvent, classify_exception, log_event
from .services.retention import RedactingLogFilter
from .workers.action_dispatch import router as action_dispatch_router
from .workers.reconcile_actions import router as reconcile_actions_router


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        request.state.correlation_id = correlation_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception as error:
            log_event(
                RelayLogEvent(
                    event="request_completed",
                    severity="ERROR",
                    correlation_id=correlation_id,
                    user_id_hash=None,
                    action_id=None,
                    approval_id=None,
                    provider="none",
                    outcome=classify_exception(error),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                ),
                method=request.method,
            )
            raise
        response.headers["X-Correlation-ID"] = correlation_id
        log_event(
            RelayLogEvent(
                event="request_completed",
                severity="INFO" if response.status_code < 500 else "ERROR",
                correlation_id=correlation_id,
                user_id_hash=None,
                action_id=None,
                approval_id=None,
                provider="none",
                outcome=str(response.status_code),
                latency_ms=int((time.perf_counter() - started) * 1000),
            ),
            method=request.method,
        )
        return response


# basicConfig() is a safety net for the case where nothing else configures a
# handler at all (e.g. running outside uvicorn/pytest). It is a no-op if a
# handler already exists on root, which is the common case here.
logging.basicConfig(level=logging.INFO)

# The real fix: without an explicit level, "relay.*" loggers inherit root's
# default WARNING, so every INFO-level operational log (extraction outcomes,
# ingestion summaries, daily maintenance counts) is silently dropped even
# though a handler exists. Setting the level here fixes it regardless of
# whether uvicorn or pytest already attached a handler to root first.
logging.getLogger("relay").setLevel(logging.INFO)

# Every Relay logger is filtered, so a sensitive value cannot reach a log sink
# even if a future call site passes one by mistake.
logging.getLogger("relay").addFilter(RedactingLogFilter())

app = FastAPI(title="Relay API", version="0.1.0")
app.add_middleware(CorrelationIdMiddleware)
app.include_router(actions_router)
app.include_router(google_router)
app.include_router(pickup_router)
app.include_router(pubsub_router)
app.include_router(product_router)
app.include_router(repair_plans_router)
app.include_router(action_dispatch_router)
app.include_router(webhooks_router)
app.include_router(reconcile_actions_router)


def create_app() -> FastAPI:
    return app


def _correlation_id(request: Request) -> str:
    return getattr(request.state, "correlation_id", str(uuid4()))


def _problem_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    problem = Problem(code=code, message=message, correlation_id=_correlation_id(request))
    return JSONResponse(status_code=status_code, content=problem.model_dump(), headers={"X-Correlation-ID": problem.correlation_id})


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exception: StarletteHTTPException) -> JSONResponse:
    safe_errors = {
        401: ("unauthorized", "Authentication is required to access this resource."),
        403: ("forbidden", "You are not allowed to access this resource."),
        404: ("not_found", "The requested resource was not found."),
        409: ("approval_version_conflict", "The approval was already decided. Refresh and review the current plan."),
    }
    if exception.status_code == 409 and exception.detail == "CONTACTS_PERMISSION_REQUIRED":
        return _problem_response(
            request,
            409,
            "CONTACTS_PERMISSION_REQUIRED",
            "Contacts permission is required to search your Google Contacts.",
        )
    code, message = safe_errors.get(exception.status_code, ("request_failed", "The request could not be completed."))
    return _problem_response(request, exception.status_code, code, message)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exception: RequestValidationError) -> JSONResponse:
    return _problem_response(request, 422, "invalid_request", "The request does not match the API contract.")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exception: Exception) -> JSONResponse:
    return _problem_response(request, 500, "internal_error", "The service could not complete this request.")


# Google Front End intercepts /healthz on Cloud Run and answers it itself, so
# the request never reaches the container. /health is the reachable path; both
# are served so local tooling and probes keep working.
@app.get("/health")
@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/me", response_model=CurrentUser)
async def get_current_user(current_user: CurrentUser = Depends(require_current_user)) -> CurrentUser:
    return current_user


@app.get("/v1/actions/{action_id}", response_model=ActionStatusResponse, responses={401: {"model": Problem}, 404: {"model": Problem}})
async def get_action(
    action_id: str,
    current_user: CurrentUser = Depends(require_current_user),
    repository=Depends(get_action_repository),
) -> ActionStatusResponse:
    action = await repository.get(current_user.uid, action_id)
    if action is None:
        raise HTTPException(status_code=404)
    return ActionStatusResponse(
        action_id=action.id,
        state=action.state,
        retry_count=action.retry_count,
        verification_evidence=action.verification_evidence,
        correlation_id=action.correlation_id,
    )


def _openapi_models() -> list[type]:
    return [
        ActionRecord,
        ActionDispatchRecord,
        ActionStatusResponse,
        ActionAuditView,
        ActionOutcomeView,
        ApprovalActionSummary,
        ApprovalBatchView,
        Approval,
        ApprovalDecisionRequest,
        ApprovalDecisionResponse,
        CallContract,
        Commitment,
        CreateRepairPlanRequest,
        CreateRepairPlanResponse,
        DispatchClaim,
        Disruption,
        Edge,
        GmailEvidenceRef,
        HandoffResponse,
        AuditEventView,
        DashboardView,
        PickupContactCommand,
        PickupContactResponse,
        PickerContactView,
        PickerPhoneView,
        PickerSessionView,
        PlanTimelineItem,
        RegisterDeviceRequest,
        PlanningOptions,
        OutcomeValidation,
        Problem,
        Provenance,
        ProviderEvent,
        RecordCallOutcomeInput,
        SourceEventEnvelope,
    ]


def custom_openapi() -> dict:
    if app.openapi_schema:
        return app.openapi_schema

    document = get_openapi(title=app.title, version=app.version, routes=app.routes)
    schemas, _ = models_json_schema(
        [(model, "validation") for model in _openapi_models()],
        ref_template="#/components/schemas/{model}",
    )
    document.setdefault("components", {}).setdefault("schemas", {}).update(
        {model.__name__: schema for (model, _), schema in schemas.items()}
    )
    app.openapi_schema = document
    return document


app.openapi = custom_openapi
