"""
Flex/Standard Tier Handler

Provides utilities for making API calls with Flex tier first,
falling back to Standard tier on timeout or error.

Error Codes that trigger fallback to Standard:
- asyncio.TimeoutError: Request exceeded FLEX_TIMEOUT_SECONDS (2 min)
- 429 Too Many Requests: Rate limit or quota exceeded
- 500 Internal Server Error: OpenAI server issues
- 502 Bad Gateway: Cloudflare couldn't reach OpenAI
- 503 Service Unavailable: Service overloaded or maintenance
- 504 Gateway Timeout: Request took too long on server side
- "capacity": Flex tier at capacity
- "overloaded": Server overloaded
- "service_unavailable": Service temporarily unavailable
"""
import asyncio
import logging
from typing import Any, Callable, TypeVar

from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception,
    before_sleep_log,
)

logger = logging.getLogger(__name__)

# Retry configuration for conversation_locked errors
CONVERSATION_LOCKED_MAX_RETRIES = 3
CONVERSATION_LOCKED_MIN_WAIT = 2  # seconds
CONVERSATION_LOCKED_MAX_WAIT = 10  # seconds
POST_TIMEOUT_DELAY = 10  # seconds to wait after Flex timeout before trying Standard

T = TypeVar('T')

# ============================================================================
# ERROR CODES AND PATTERNS THAT TRIGGER FALLBACK TO STANDARD
# ============================================================================
# These errors indicate Flex tier issues that Standard might not have

FLEX_FALLBACK_ERROR_CODES = {
    429,  # Too Many Requests - rate limit, Flex tier might be throttled
    500,  # Internal Server Error - server issue, retry on Standard
    502,  # Bad Gateway - Cloudflare couldn't reach OpenAI
    503,  # Service Unavailable - overload or maintenance
    504,  # Gateway Timeout - request took too long
}

FLEX_FALLBACK_ERROR_PATTERNS = [
    # Rate limiting and capacity
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
    "quota exceeded",
    "quota_exceeded",
    
    # Flex-specific capacity issues
    "capacity",
    "flex",
    "service_tier",
    
    # Server availability
    "overloaded",
    "overload",
    "service unavailable",
    "service_unavailable",
    "temporarily unavailable",
    
    # Timeout patterns
    "timeout",
    "timed out",
    "gateway timeout",
    
    # Server errors
    "internal server error",
    "internal_server_error",
    "bad gateway",
    "bad_gateway",
    
    # Connection issues
    "connection reset",
    "connection refused",
    "connection error",
    "network error",
]


def _should_fallback_to_standard(error: Exception) -> bool:
    """
    Determines if an error should trigger fallback from Flex to Standard tier.
    
    Args:
        error: The exception that occurred
    
    Returns:
        True if we should fallback to Standard, False if error is unrecoverable
    """
    error_str = str(error).lower()
    
    # Check for HTTP status code in error
    # OpenAI errors typically include the status code in the message
    for code in FLEX_FALLBACK_ERROR_CODES:
        if str(code) in error_str:
            return True
    
    # Check for known error patterns
    for pattern in FLEX_FALLBACK_ERROR_PATTERNS:
        if pattern in error_str:
            return True
    
    # Check for openai library specific error types
    error_type = type(error).__name__.lower()
    if any(x in error_type for x in ["ratelimit", "timeout", "connection", "api"]):
        return True
    
    # Default: still try Standard for unknown errors (better than failing)
    return True


class FlexTierError(Exception):
    """Raised when Flex tier fails and should fallback to Standard."""
    pass


async def call_with_flex_fallback(
    flex_call: Callable[[], Any],
    standard_call: Callable[[], Any],
    operation_name: str = "API call"
) -> Any:
    """
    Attempts a Flex tier API call first, falls back to Standard on failure.
    
    Fallback triggers:
    - Timeout (>2 minutes)
    - HTTP 429, 500, 502, 503, 504
    - Rate limit / capacity errors
    - Connection errors
    
    Args:
        flex_call: Async callable that makes the Flex tier API call
        standard_call: Async callable that makes the Standard tier API call
        operation_name: Name for logging purposes
    
    Returns:
        The API response from whichever tier succeeds
    
    Raises:
        Exception: If both Flex and Standard calls fail
    """
    from . import config
    
    # Get timeout from config
    timeout_seconds = getattr(config, 'FLEX_TIMEOUT_SECONDS', 120)
    
    # If Flex is disabled globally, go straight to Standard
    if not getattr(config, 'FLEX_ENABLED', True):
        logger.info(f"[TIER] Flex disabled globally, using Standard for {operation_name}")
        return await standard_call()
    
    flex_error = None
    
    # Try Flex first with timeout
    try:
        logger.info(f"[TIER] Attempting Flex tier for {operation_name}")
        result = await asyncio.wait_for(
            flex_call(),
            timeout=timeout_seconds
        )
        logger.info(f"[TIER] Flex tier succeeded for {operation_name}")
        return result
        
    except asyncio.TimeoutError:
        flex_error = "timeout"
        logger.warning(
            f"[TIER] Flex tier TIMEOUT ({timeout_seconds}s) for {operation_name}, "
            f"waiting {POST_TIMEOUT_DELAY}s before Standard fallback (server may still hold conversation lock)"
        )
        # Wait before Standard fallback - the Flex request may still be running
        # on OpenAI's servers, which would cause conversation_locked error
        await asyncio.sleep(POST_TIMEOUT_DELAY)
        
    except Exception as e:
        flex_error = str(e)
        
        if _should_fallback_to_standard(e):
            # Log with error classification
            error_type = type(e).__name__
            logger.warning(
                f"[TIER] Flex tier FAILED for {operation_name} "
                f"[{error_type}]: {e}, falling back to Standard"
            )
        else:
            # Unexpected error - log but still try Standard
            logger.error(
                f"[TIER] Flex tier UNEXPECTED ERROR for {operation_name}: {e}, "
                f"attempting Standard as last resort"
            )
    
    # Fallback to Standard with retry for conversation_locked
    def _is_conversation_locked(exception: Exception) -> bool:
        """Check if error is conversation_locked (retryable)."""
        return "conversation_locked" in str(exception).lower()
    
    @retry(
        stop=stop_after_attempt(CONVERSATION_LOCKED_MAX_RETRIES),
        wait=wait_exponential(multiplier=1, min=CONVERSATION_LOCKED_MIN_WAIT, max=CONVERSATION_LOCKED_MAX_WAIT),
        retry=retry_if_exception(_is_conversation_locked),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True
    )
    async def _standard_with_retry():
        return await standard_call()
    
    try:
        # Truncate error message for logging
        error_preview = flex_error[:50] if flex_error and len(flex_error) > 50 else flex_error
        logger.info(f"[TIER] Using Standard tier for {operation_name} (Flex failed: {error_preview})")
        result = await _standard_with_retry()
        logger.info(f"[TIER] Standard tier succeeded for {operation_name}")
        return result
        
    except Exception as e:
        logger.error(
            f"[TIER] Standard tier ALSO FAILED for {operation_name}: {e} "
            f"(original Flex error: {flex_error})"
        )
        raise
