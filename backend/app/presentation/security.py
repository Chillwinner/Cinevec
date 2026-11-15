from __future__ import annotations

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.domain.errors import AppError

PUBLIC_EXACT = {
    "/api/health",
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
}


def is_public_path(path: str, method: str) -> bool:
    if method.upper() == "OPTIONS":
        return True
    normalized = path.rstrip("/") or "/"
    if normalized in PUBLIC_EXACT or normalized == "/api/health":
        return True
    return False


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        container = getattr(request.app.state, "container", None)
        if container is None or not hasattr(container, "auth"):
            return await call_next(request)
        auth = container.auth
        policy = container.policy
        request.state.identity = None
        path = request.url.path
        if request.method.upper() == "OPTIONS":
            return await call_next(request)
        if path.startswith("/api/v1"):
            tokens = getattr(container, "api_tokens", None)
            if tokens is None:
                return JSONResponse(
                    status_code=401,
                    content={"error": {"code": "unauthorized", "message": "插件接口需要 Bearer API Token。"}},
                )
            try:
                header = request.headers.get("authorization")
                if not header:
                    token = request.query_params.get("access_token")
                    if token:
                        header = f"Bearer {token}"
                identity = tokens.authenticate(header)
                request.state.identity = identity
            except AppError as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"error": {"code": exc.code, "message": exc.message}},
                )
            return await call_next(request)
        if not auth.is_enabled():
            return await call_next(request)
        if is_public_path(path, request.method):
            return await call_next(request)
        try:
            header = request.headers.get("authorization")
            if not header:
                token = request.query_params.get("access_token")
                if token:
                    header = f"Bearer {token}"
            identity = auth.authenticate_bearer(header)
            request.state.identity = identity
            if not policy.allow(identity, f"{request.method.lower()}:{path}"):
                raise AppError("forbidden", "没有权限执行该操作。", 403)
        except AppError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={"error": {"code": exc.code, "message": exc.message}},
            )
        return await call_next(request)
