import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from crm_api.logging_config import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"

_logger = logging.getLogger("crm_api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, records latency, and emits one access log per request."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = request_id
            _logger.info(
                "request",
                extra={
                    "context": {
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "latency_ms": round((time.perf_counter() - start) * 1000, 2),
                    }
                },
            )
            return response
        finally:
            request_id_var.reset(token)
