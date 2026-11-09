import logging
import uuid

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.apps.refunds.router import router as refunds_router
from app.config import settings
from app.platform.auth import Principal, get_principal
from app.platform.errors import DomainError
from app.platform.request_context import request_id_var
from app.platform.workflows.router import router as workflows_router

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Internal Tools Platform", version="0.1.0")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    request_id = incoming if 0 < len(incoming) <= 64 else str(uuid.uuid4())
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}}
    )


@app.get("/healthz", tags=["platform"])
def healthz() -> dict[str, str]:
    return {"status": "ok"}


class AuthConfig(BaseModel):
    issuer: str
    client_id: str
    audience: str


@app.get("/api/auth/config", response_model=AuthConfig, tags=["platform"])
def auth_config() -> AuthConfig:
    """Public: OIDC settings the SPA needs to start the login redirect (no secrets)."""
    return AuthConfig(
        issuer=settings.oidc_issuer,
        client_id=settings.oidc_web_client_id,
        audience=settings.oidc_audience,
    )


class MeResponse(BaseModel):
    subject: str
    display_name: str
    roles: list[str]
    business_units: list[str]


@app.get("/api/me", response_model=MeResponse, tags=["platform"])
def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(
        subject=principal.subject,
        display_name=principal.display_name,
        roles=sorted(principal.roles),
        business_units=sorted(principal.business_units),
    )


app.include_router(refunds_router)
app.include_router(workflows_router)
