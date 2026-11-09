import logging
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.apps.refunds.router import router as refunds_router
from app.config import settings
from app.platform.auth import DEV_DIRECTORY, Principal, get_principal, issue_token
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


class DevLoginRequest(BaseModel):
    email: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105


@app.post("/api/auth/dev-login", response_model=TokenResponse, tags=["platform"])
def dev_login(body: DevLoginRequest) -> TokenResponse:
    if not settings.dev_login_enabled:
        raise HTTPException(404, "Not found")
    if body.email not in DEV_DIRECTORY:
        raise HTTPException(401, "Unknown user")
    return TokenResponse(access_token=issue_token(body.email))


@app.get("/api/auth/dev-users", tags=["platform"])
def dev_users() -> list[dict[str, object]]:
    if not settings.dev_login_enabled:
        raise HTTPException(404, "Not found")
    return [
        {"email": email, "display_name": name, "roles": sorted(roles)}
        for email, (name, roles) in DEV_DIRECTORY.items()
    ]


class MeResponse(BaseModel):
    subject: str
    display_name: str
    roles: list[str]


@app.get("/api/me", response_model=MeResponse, tags=["platform"])
def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(
        subject=principal.subject,
        display_name=principal.display_name,
        roles=sorted(principal.roles),
    )


app.include_router(refunds_router)
app.include_router(workflows_router)
