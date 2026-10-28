"""Loopback host and browser-origin protection for the local API."""

import secrets

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class LocalSecurityMiddleware(BaseHTTPMiddleware):
    """Only the exact local origin may read or mutate API state."""

    def __init__(self, app: object, *, port: int, token: str) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self._host = f"127.0.0.1:{port}"
        self._origin = f"http://{self._host}"
        self.token = token

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        host = request.headers.get("host", "")
        origin = request.headers.get("origin")
        if host != self._host or (origin is not None and origin != self._origin):
            return JSONResponse({"detail": "Local origin required"}, status_code=403)
        if request.headers.get("sec-fetch-site") not in (None, "same-origin", "none"):
            return JSONResponse({"detail": "Cross-origin request denied"}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.headers.get("x-tunetrail-token", "")
            if not secrets.compare_digest(token, self.token):
                return JSONResponse({"detail": "Local session token required"}, status_code=403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON required"}, status_code=415)
            length = request.headers.get("content-length")
            if (
                length is None
                or not length.isascii()
                or not length.isdecimal()
                or int(length) > 65536
            ):
                return JSONResponse(
                    {"detail": "Request body too large or unbounded"}, status_code=413
                )
            if len(await request.body()) != int(length):
                return JSONResponse({"detail": "Invalid request body length"}, status_code=400)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response
