from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
import time
from collections import defaultdict
from typing import Dict, List
from api.config import settings


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Rate limiting middleware using sliding window algorithm."""
    
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests: Dict[str, List[float]] = defaultdict(list)
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks and metrics
        if request.url.path in ["/healthz", "/readyz", "/metrics"]:
            return await call_next(request)
        
        # Get client identifier (API key or IP)
        api_key = request.headers.get("X-API-Key")
        client_id = api_key or request.client.host if request.client else "unknown"
        
        now = time.time()
        minute_ago = now - 60
        
        # Clean old requests (sliding window)
        self.requests[client_id] = [t for t in self.requests[client_id] if t > minute_ago]
        
        # Check limit
        if len(self.requests[client_id]) >= self.requests_per_minute:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Try again later.",
                    "retry_after": 60
                },
                headers={"Retry-After": "60"}
            )
        
        # Record request
        self.requests[client_id].append(now)
        
        response = await call_next(request)
        
        # Add rate limit headers
        remaining = max(0, self.requests_per_minute - len(self.requests[client_id]))
        response.headers["X-RateLimit-Limit"] = str(self.requests_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(now + 60))
        
        return response


class TokenBucketRateLimiter:
    """Alternative: Token bucket algorithm for more sophisticated rate limiting."""
    
    def __init__(self, capacity: int, refill_rate: float):
        """
        Args:
            capacity: Maximum tokens in bucket (burst allowance)
            refill_rate: Tokens added per second
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.buckets: Dict[str, Dict] = {}
    
    def _get_bucket(self, client_id: str) -> Dict:
        now = time.time()
        if client_id not in self.buckets:
            self.buckets[client_id] = {
                "tokens": self.capacity,
                "last_refill": now
            }
        
        bucket = self.buckets[client_id]
        # Refill tokens based on time passed
        elapsed = now - bucket["last_refill"]
        bucket["tokens"] = min(self.capacity, bucket["tokens"] + elapsed * self.refill_rate)
        bucket["last_refill"] = now
        return bucket
    
    def consume(self, client_id: str, tokens: int = 1) -> bool:
        """Try to consume tokens. Returns True if allowed, False if rate limited."""
        bucket = self._get_bucket(client_id)
        if bucket["tokens"] >= tokens:
            bucket["tokens"] -= tokens
            return True
        return False
    
    def get_remaining(self, client_id: str) -> int:
        bucket = self._get_bucket(client_id)
        return int(bucket["tokens"])


# Global token bucket rate limiter (optional, more sophisticated)
token_bucket_limiter = TokenBucketRateLimiter(
    capacity=settings.RATE_LIMIT_PER_MINUTE,
    refill_rate=settings.RATE_LIMIT_PER_MINUTE / 60.0  # tokens per second
)


class TokenBucketMiddleware(BaseHTTPMiddleware):
    """Token bucket rate limiting middleware."""
    
    def __init__(self, app, limiter: TokenBucketRateLimiter = None):
        super().__init__(app)
        self.limiter = limiter or token_bucket_limiter
    
    async def dispatch(self, request: Request, call_next):
        # Skip rate limiting for health checks and metrics
        if request.url.path in ["/healthz", "/readyz", "/metrics"]:
            return await call_next(request)
        
        # Get client identifier
        api_key = request.headers.get("X-API-Key")
        client_id = api_key or request.client.host if request.client else "unknown"
        
        # Try to consume token
        if not self.limiter.consume(client_id):
            remaining = self.limiter.get_remaining(client_id)
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Rate limit exceeded. Try again later.",
                    "retry_after": 60
                },
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": str(settings.RATE_LIMIT_PER_MINUTE),
                    "X-RateLimit-Remaining": str(remaining),
                    "X-RateLimit-Reset": str(int(time.time() + 60))
                }
            )
        
        response = await call_next(request)
        
        # Add rate limit headers
        remaining = self.limiter.get_remaining(client_id)
        response.headers["X-RateLimit-Limit"] = str(settings.RATE_LIMIT_PER_MINUTE)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time() + 60))
        
        return response