from __future__ import annotations

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
    ActionRecord,
    Approval,
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    Commitment,
    Disruption,
    Edge,
    Problem,
    ProviderEvent,
    SourceEventEnvelope,
)
from .routes.actions import router as actions_router


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        correlation_id = request.headers.get("X-Correlation-ID") or str(uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Correlation-ID"] = correlation_id
        return response


app = FastAPI(title="Relay API", version="0.1.0")
app.add_middleware(CorrelationIdMiddleware)
app.include_router(actions_router)


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
    code, message = safe_errors.get(exception.status_code, ("request_failed", "The request could not be completed."))
    return _problem_response(request, exception.status_code, code, message)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exception: RequestValidationError) -> JSONResponse:
    return _problem_response(request, 422, "invalid_request", "The request does not match the API contract.")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exception: Exception) -> JSONResponse:
    return _problem_response(request, 500, "internal_error", "The service could not complete this request.")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/me", response_model=CurrentUser)
async def get_current_user(current_user: CurrentUser = Depends(require_current_user)) -> CurrentUser:
    return current_user


@app.get("/v1/actions/{action_id}", response_model=ActionRecord, responses={401: {"model": Problem}, 404: {"model": Problem}})
async def get_action(action_id: str, current_user: CurrentUser = Depends(require_current_user)) -> ActionRecord:
    del action_id, current_user
    raise HTTPException(status_code=404)


def _openapi_models() -> list[type]:
    return [
        ActionRecord,
        Approval,
        ApprovalDecisionRequest,
        ApprovalDecisionResponse,
        Commitment,
        Disruption,
        Edge,
        Problem,
        ProviderEvent,
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
