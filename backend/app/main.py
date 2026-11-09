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
from app.platform.security import (
    BodySizeLimitMiddleware,
    SecurityHeadersMiddleware,
    UnhandledErrorMiddleware,
    configure_cors,
    cors_origins_from_env,
    fastapi_settings,
    install_log_redaction,
    internal_error_response,
    max_body_bytes,
    security_headers,
)
from app.platform.workflows.router import router as workflows_router

logging.basicConfig(level=logging.INFO)
install_log_redaction()
logger = logging.getLogger("app")

app = FastAPI(
    title="Internal Tools Platform",
    version="0.1.0",
    **fastapi_settings(production=settings.is_production),
)

# Middleware order (outermost first): security headers -> CORS (off unless configured) ->
# generic 500 -> request id -> body size limit -> routes.
app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_body_bytes())

_REQUEST_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:")


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming = request.headers.get("X-Request-ID", "")
    valid = 0 < len(incoming) <= 64 and set(incoming) <= _REQUEST_ID_CHARS
    request_id = incoming if valid else str(uuid.uuid4())
    request.state.request_id = request_id
    token = request_id_var.set(request_id)
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("unhandled error request_id=%s path=%s", request_id, request.url.path)
        raise  # -> UnhandledErrorMiddleware (generic 500 inside CORS), then re-raised to the server
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = request_id
    return response


app.add_middleware(UnhandledErrorMiddleware)
configure_cors(app, cors_origins_from_env())
app.add_middleware(SecurityHeadersMiddleware, production=settings.is_production)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}}
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request | None, exc: Exception) -> JSONResponse:
    """Generic 500 for anything unhandled. Runs in Starlette's outermost ServerErrorMiddleware,
    outside the header middleware, so it sets the security headers and request id itself."""
    response = internal_error_response()
    path = request.url.path if request is not None else ""
    for name, value in security_headers(path, production=settings.is_production):
        response.headers[name.decode()] = value.decode()
    request_id = getattr(request.state, "request_id", None) if request is not None else None
    if request_id:
        response.headers["X-Request-ID"] = request_id
    else:
        logger.error("unhandled error outside request scope: %s", type(exc).__name__)
    return response


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
