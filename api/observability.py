import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware
from shared.telemetry import trace_context, new_trace
from .metrics import record_api_request

logger = logging.getLogger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        trace = new_trace(request.headers.get('traceparent'))
        token = trace_context.set(trace)
        began, status = time.perf_counter(), 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers['traceparent'] = trace
            response.headers['X-Request-ID'] = trace.split('-')[1]
            return response
        finally:
            route = request.scope.get('route')
            endpoint = getattr(route, 'path', 'unmatched')
            if endpoint not in ('/healthz', '/readyz', '/metrics'):
                elapsed = time.perf_counter() - began
                method = request.method if request.method in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS') else 'OTHER'
                record_api_request(method, endpoint, status, elapsed)
                logger.info('http method=%s route=%s status=%s duration_seconds=%.6f', method, endpoint, status, elapsed)
            trace_context.reset(token)
