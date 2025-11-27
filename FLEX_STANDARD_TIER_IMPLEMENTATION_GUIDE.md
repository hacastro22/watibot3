# Flex/Standard Tier Hybrid Implementation Guide

## Overview

This guide describes implementing a cost-saving hybrid approach where:
- **Flex tier** is used as the first attempt for non-critical chat responses
- **Standard tier** is the fallback when Flex times out (>2 min) or returns errors
- **Standard tier only** is used for critical payment and booking workflows

## Cost Savings Estimate

| Scenario | Monthly Cost |
|----------|-------------|
| **Current (100% Standard)** | ~$900 |
| **Hybrid (estimated 60% Flex success)** | ~$630 |
| **Estimated savings** | ~$270/month (~$3,240/year) |

---

## Tier Assignment Rules

### ✅ Flex First, Standard Fallback (2-min timeout)

| Component | Location | Notes |
|-----------|----------|-------|
| `get_openai_response()` - Initial call | `openai_agent.py` | First API call before tools |
| `load_additional_modules` tool execution | `openai_agent.py` | Just reads local .txt files |
| `classify_image_with_context()` | `image_classifier.py` | Non-payment image classification |

### 🔒 Standard Only (No Flex)

| Component | Location | Reason |
|-----------|----------|--------|
| **All tool calls EXCEPT `load_additional_modules`** | `openai_agent.py` | External systems, critical |
| `analyze_payment_proof()` | `payment_proof_analyzer.py` | Critical payment validation |
| `validate_compraclick_payment()` | `compraclick_tool.py` | Payment confirmation |
| `sync_bank_transfers()` | `bank_transfer_tool.py` | Bank sync - cannot fail |
| `validate_bank_transfer()` | `bank_transfer_tool.py` | Payment validation |
| `make_booking()` | `booking_tool.py` | Reservation creation |
| `transcribe_audio_opus()` | `whisper_client.py` | Whisper API (different endpoint) |

---

## Tool Chain Tier Logic

### The Problem

Tool calls happen in chains. If Flex fails mid-chain, we have orphaned tool calls:

```
Customer: "Quiero reservar para el 15 de diciembre"
    ↓
Flex call #1 → Initial response ✓
    ↓
Flex call #2 → load_additional_modules ✓ (safe - just local files)
    ↓
Standard call #3 → get_price_for_date ✓ (external API)
    ↓
Standard call #4 → check_room_availability ✓ (external API)
    ↓
Standard call #5 → Final response to customer ✓
```

### The Rule

```
┌─────────────────────────────────────────────────────────────────┐
│ TIER SELECTION LOGIC FOR TOOL CHAINS                            │
├─────────────────────────────────────────────────────────────────┤
│ 1. Initial API call: Flex (with 2-min timeout → Standard)       │
│ 2. If tool = "load_additional_modules": Stay on Flex            │
│ 3. If tool = ANYTHING ELSE: Switch to Standard immediately      │
│ 4. Once Standard: Stay on Standard for rest of request          │
└─────────────────────────────────────────────────────────────────┘
```

### Why `load_additional_modules` is Safe for Flex

This tool:
- ✅ Only reads local `.txt` files from `app/resources/`
- ✅ No external API calls
- ✅ No database writes
- ✅ No payment processing
- ✅ If it fails, just retry - no state corruption

### Why Other Tools Need Standard

| Tool | Risk if Flex Fails |
|------|-------------------|
| `get_price_for_date` | Customer gets wrong/no quote |
| `check_room_availability` | Booking flow breaks |
| `make_booking` | Reservation not created |
| `sync_bank_transfers` | Payment not validated |
| `validate_bank_transfer` | Booking not completed |
| `send_compraclick_link` | Customer can't pay |

---

## Error Codes Reference

### HTTP Status Codes That Trigger Fallback to Standard

| Code | Name | Description | Action |
|------|------|-------------|--------|
| **429** | Too Many Requests | Rate limit or quota exceeded on Flex tier | Immediate fallback to Standard |
| **500** | Internal Server Error | OpenAI server crash or bug | Fallback + retry on Standard |
| **502** | Bad Gateway | Cloudflare couldn't reach OpenAI servers | Fallback to Standard |
| **503** | Service Unavailable | Flex tier overloaded or maintenance | Fallback to Standard |
| **504** | Gateway Timeout | Request took too long on server side | Fallback to Standard |

### Error Message Patterns That Trigger Fallback

| Pattern | Meaning | Example Error Message |
|---------|---------|----------------------|
| `rate limit` | Exceeded requests/tokens per minute | "Rate limit reached for gpt-5.1" |
| `rate_limit` | Same as above (snake_case variant) | "rate_limit_exceeded" |
| `too many requests` | Same as 429 | "Too many requests, please slow down" |
| `quota exceeded` | Monthly/account quota exhausted | "You exceeded your current quota" |
| `capacity` | Flex tier at capacity | "Flex tier capacity exceeded" |
| `overloaded` | Server under heavy load | "The server is currently overloaded" |
| `service unavailable` | Temporary outage | "Service temporarily unavailable" |
| `timeout` | Request didn't complete in time | "Request timed out" |
| `gateway timeout` | 504 equivalent message | "Gateway timeout while waiting" |
| `bad gateway` | 502 equivalent message | "Bad gateway error" |
| `connection reset` | Network connection dropped | "Connection reset by peer" |
| `connection refused` | Server not accepting connections | "Connection refused" |
| `connection error` | Generic connection failure | "Failed to connect to API" |

### Errors That Should NOT Fallback (Unrecoverable)

| Code | Name | Reason |
|------|------|--------|
| **400** | Bad Request | Invalid request - same request will fail on Standard too |
| **401** | Unauthorized | Invalid API key - won't work on any tier |
| **403** | Forbidden | Access denied - permission issue |
| **404** | Not Found | Model/endpoint doesn't exist |

### Timeout Configuration

| Setting | Value | Notes |
|---------|-------|-------|
| `FLEX_TIMEOUT_SECONDS` | 120 (2 min) | Max wait before fallback |
| OpenAI Gateway Timeout | ~100 seconds | Cloudflare's internal limit |
| Recommended client timeout | 130 seconds | Slightly above our 2-min limit |

---

## Implementation Details

### 1. Add Service Tier Configuration

**File: `app/config.py`**

```python
# Service tier configuration
FLEX_TIMEOUT_SECONDS = 120  # 2 minutes
FLEX_ENABLED = os.getenv("FLEX_ENABLED", "true").lower() == "true"
```

**File: `.env`**

```bash
# Flex tier toggle (set to "false" to disable Flex entirely)
FLEX_ENABLED=true
```

---

### 2. Create Flex Wrapper Utility

**File: `app/flex_tier_handler.py`** (NEW FILE)

```python
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
from typing import Any, Callable, TypeVar, Optional
from functools import wraps

logger = logging.getLogger(__name__)

# Configuration
FLEX_TIMEOUT_SECONDS = 120  # 2 minutes

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
            timeout=FLEX_TIMEOUT_SECONDS
        )
        logger.info(f"[TIER] Flex tier succeeded for {operation_name}")
        return result
        
    except asyncio.TimeoutError:
        flex_error = "timeout"
        logger.warning(
            f"[TIER] Flex tier TIMEOUT ({FLEX_TIMEOUT_SECONDS}s) for {operation_name}, "
            f"falling back to Standard"
        )
        
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
    
    # Fallback to Standard
    try:
        logger.info(f"[TIER] Using Standard tier for {operation_name} (Flex failed: {flex_error[:50]}...)")
        result = await standard_call()
        logger.info(f"[TIER] Standard tier succeeded for {operation_name}")
        return result
        
    except Exception as e:
        logger.error(
            f"[TIER] Standard tier ALSO FAILED for {operation_name}: {e} "
            f"(original Flex error: {flex_error})"
        )
        raise


def responses_api_service_tier(tier: str = "auto") -> dict:
    """
    Returns the service_tier parameter for Responses API calls.
    
    Args:
        tier: "flex" or "auto" (Standard)
    
    Returns:
        dict with service_tier key for API call kwargs
    """
    if tier == "flex":
        return {"service_tier": "flex"}
    return {}  # Default is "auto" (Standard) - no need to specify


def chat_completions_service_tier(tier: str = "auto") -> dict:
    """
    Returns the service_tier parameter for Chat Completions API calls.
    
    Args:
        tier: "flex" or "auto" (Standard)
    
    Returns:
        dict with service_tier key for API call kwargs
    """
    if tier == "flex":
        return {"service_tier": "flex"}
    return {}  # Default is "auto" (Standard) - no need to specify
```

---

### 3. Modify `openai_agent.py` - Main Chat Responses

**Changes to `get_openai_response()` function:**

The Responses API call needs to be wrapped with the Flex fallback logic. Here's how to modify the key sections:

#### 3.1 Add imports at the top

```python
from .flex_tier_handler import call_with_flex_fallback, responses_api_service_tier
```

#### 3.2 Wrap the main API calls

Find the `responses.create()` calls (around lines 1780-1813) and wrap them:

**BEFORE:**
```python
response = await openai_client.responses.create(
    model="gpt-5.1",
    previous_response_id=previous_response_id,
    input=input_messages,
    tools=tools,
    max_output_tokens=4000
)
```

**AFTER:**
```python
async def _flex_call():
    return await openai_client.responses.create(
        model="gpt-5.1",
        previous_response_id=previous_response_id,
        input=input_messages,
        tools=tools,
        max_output_tokens=4000,
        service_tier="flex"
    )

async def _standard_call():
    return await openai_client.responses.create(
        model="gpt-5.1",
        previous_response_id=previous_response_id,
        input=input_messages,
        tools=tools,
        max_output_tokens=4000
        # service_tier defaults to "auto" (Standard)
    )

response = await call_with_flex_fallback(
    flex_call=_flex_call,
    standard_call=_standard_call,
    operation_name=f"chat_response:{user_identifier}"
)
```

#### 3.3 Apply to all Responses API call sites

There are multiple `responses.create()` calls in `openai_agent.py`:
1. **Line ~1780-1786**: Main response with `previous_response_id`
2. **Line ~1791-1813**: New conversation start
3. **Line ~1865-1874**: Agent context injection (keep Standard - small call)
4. **Line ~1901-1906**: Fresh conversation recovery
5. **Line ~1918-1940**: Fresh conversation without context
6. **Tool call loop**: Multiple calls during tool execution

**IMPORTANT**: For simplicity, create a helper function:

```python
async def _make_responses_call(
    use_flex: bool,
    user_identifier: str,
    **kwargs
) -> Any:
    """
    Make a Responses API call with optional Flex tier.
    
    Args:
        use_flex: Whether to try Flex first with fallback
        user_identifier: For logging
        **kwargs: Arguments for responses.create()
    
    Returns:
        API response
    """
    if not use_flex:
        # Standard only - no Flex attempt
        return await openai_client.responses.create(**kwargs)
    
    # Flex with fallback
    async def _flex():
        return await openai_client.responses.create(
            **kwargs,
            service_tier="flex"
        )
    
    async def _standard():
        return await openai_client.responses.create(**kwargs)
    
    return await call_with_flex_fallback(
        flex_call=_flex,
        standard_call=_standard,
        operation_name=f"responses:{user_identifier}"
    )
```

Then replace all main chat `responses.create()` calls with:

```python
response = await _make_responses_call(
    use_flex=True,  # Enable Flex for chat
    user_identifier=user_identifier,
    model="gpt-5.1",
    previous_response_id=previous_response_id,
    input=input_messages,
    tools=tools,
    max_output_tokens=4000
)
```

#### 3.4 Tool Chain Tier Switching Logic

The key implementation is in the **tool execution loop**. We track the current tier and switch to Standard when non-module tools are detected.

**Add this logic to the tool execution loop in `get_openai_response()`:**

```python
# Track tier state for this request
current_tier_is_flex = True  # Start with Flex

# Tool execution loop
while tool_calls:
    for tool_call in tool_calls:
        tool_name = getattr(tool_call, "name", None)
        
        # ================================================================
        # TIER SWITCHING LOGIC
        # ================================================================
        if tool_name != "load_additional_modules":
            # Any tool OTHER than load_additional_modules → switch to Standard
            if current_tier_is_flex:
                logger.info(f"[TIER] Tool '{tool_name}' detected, switching to Standard tier for rest of chain")
                current_tier_is_flex = False
        else:
            logger.info(f"[TIER] Tool '{tool_name}' is safe for Flex tier")
        
        # Execute the tool (existing logic)
        tool_result = await execute_tool(tool_name, tool_args)
        tool_outputs.append({"call_id": tool_call.id, "output": tool_result})
    
    # Submit tool outputs and get next response
    # USE THE TRACKED TIER STATE
    response = await _make_responses_call(
        use_flex=current_tier_is_flex,  # Dynamic based on tools seen
        user_identifier=user_identifier,
        model="gpt-5.1",
        previous_response_id=response.id,
        input=[{
            "type": "tool_result",
            "call_id": call_id,
            "output": output
        } for call_id, output in tool_outputs],
        tools=tools,
        max_output_tokens=4000
    )
    
    # Check for more tool calls
    tool_calls = _iter_tool_calls(response)
    tool_outputs = []
```

**Visual Flow:**

```
┌─────────────────────────────────────────────────────────────────┐
│ Request: "Cuánto cuesta para 2 adultos el 15 de diciembre?"    │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  [1] Initial call                    → Flex (with fallback)     │
│       ↓                                                          │
│  [2] Tool: load_additional_modules   → Flex ✓ (safe)            │
│       ↓                                                          │
│  [3] Tool: get_price_for_date        → Standard 🔒 (switch!)    │
│       ↓                                                          │
│  [4] Tool: check_room_availability   → Standard 🔒 (locked)     │
│       ↓                                                          │
│  [5] Final response                  → Standard 🔒 (locked)     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

### 4. Modify `image_classifier.py` - Flex with Fallback

**Current state**: Uses `gpt-4o-mini` which does NOT support Flex tier.

**Solution**: Upgrade to `gpt-5-mini` which supports Flex:

| Model | Standard Input | Flex Input | Standard Output | Flex Output |
|-------|---------------|------------|-----------------|-------------|
| gpt-4o-mini | $0.15 | ❌ N/A | $0.60 | ❌ N/A |
| gpt-5-mini | $0.25 | $0.125 | $2.00 | $1.00 |

**gpt-5-mini supports Flex and is more capable for image classification.**

#### 4.1 Add imports

```python
from .flex_tier_handler import call_with_flex_fallback, chat_completions_service_tier
```

#### 4.2 Upgrade model AND wrap the API call (around line 130)

**BEFORE:**
```python
response = await client.chat.completions.create(
    model="gpt-4o-mini",
    messages=messages,
    response_format={"type": "json_object"},
    max_tokens=1024
)
```

**AFTER:**
```python
async def _flex_call():
    return await client.chat.completions.create(
        model="gpt-5-mini",  # Upgraded from gpt-4o-mini - supports Flex!
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=1024,
        service_tier="flex"
    )

async def _standard_call():
    return await client.chat.completions.create(
        model="gpt-5-mini",  # Upgraded from gpt-4o-mini
        messages=messages,
        response_format={"type": "json_object"},
        max_tokens=1024
    )

response = await call_with_flex_fallback(
    flex_call=_flex_call,
    standard_call=_standard_call,
    operation_name=f"image_classifier:{wa_id}"
)
```

---

### 5. Files That Stay Standard Only (NO CHANGES NEEDED)

These files should NOT be modified - they will continue using Standard tier:

| File | Reason |
|------|--------|
| `payment_proof_analyzer.py` | Critical payment validation |
| `compraclick_tool.py` | Payment processing |
| `bank_transfer_tool.py` | Bank sync operations |
| `booking_tool.py` | Reservation creation |
| `whisper_client.py` | Uses Whisper API (no tier support) |

---

## Testing Plan

### Phase 1: Local Testing

1. **Add logging**: Ensure all tier decisions are logged
2. **Test Flex success path**: Normal chat responses (e.g., "Hola")
3. **Test timeout fallback**: Simulate slow responses (mock 3-min delay)
4. **Test error fallback**: Simulate 429 errors
5. **Test tool chain switching**:
   - Send "Cuánto cuesta para 2 adultos el 15 de dic?" 
   - Verify: Initial call = Flex, `load_additional_modules` = Flex, `get_price_for_date` = Standard
6. **Verify Standard-only paths**: Payment and booking workflows
7. **Test payment flows stay Standard**: Send payment proof, verify NO Flex used

### Phase 2: Gradual Rollout

1. **Week 1**: Enable for 10% of traffic (add user-based toggle)
2. **Week 2**: If stable, increase to 25%
3. **Week 3**: If stable, increase to 50%
4. **Week 4**: Full rollout

### Phase 3: Monitoring

Track these metrics:
- Flex success rate (target: >80%)
- Flex → Standard fallback rate
- Average response latency (Flex vs Standard)
- Error rates by tier
- Cost savings (compare to baseline)

---

## Rollback Plan

If issues arise, set in `.env`:

```bash
FLEX_ENABLED=false
```

This immediately disables all Flex tier calls without code deployment.

---

## Environment Variable Summary

```bash
# .env additions
FLEX_ENABLED=true          # Master toggle for Flex tier
FLEX_TIMEOUT_SECONDS=120   # Timeout before fallback (optional, defaults to 120)
```

---

## Implementation Checklist

- [ ] Create `app/flex_tier_handler.py`
- [ ] Add `FLEX_ENABLED` to `app/config.py`
- [ ] Add `FLEX_ENABLED=true` to `.env`
- [ ] Modify `app/openai_agent.py`:
  - [ ] Add imports
  - [ ] Create `_make_responses_call()` helper
  - [ ] Add `current_tier_is_flex` state variable
  - [ ] Wrap initial API calls with Flex fallback
  - [ ] Add tier switching logic in tool execution loop
  - [ ] Keep `load_additional_modules` on Flex
  - [ ] Switch to Standard for all other tools
- [ ] Modify `app/image_classifier.py`:
  - [ ] Add imports
  - [ ] Wrap classification API call with Flex fallback
- [ ] Test locally (all 7 test cases)
- [ ] Deploy to staging
- [ ] Gradual production rollout
- [ ] Monitor and adjust

---

## Expected Results

| Metric | Before | After (Expected) |
|--------|--------|------------------|
| **Monthly cost** | ~$900 | ~$630 |
| **Savings** | - | ~$270/month |
| **Flex success rate** | N/A | ~70-80% |
| **Average latency** | ~2-4s | ~3-5s (Flex), ~2-4s (Standard) |
| **Payment reliability** | 100% | 100% (unchanged) |

---

## Notes

1. **Flex tier availability**: Flex is available for gpt-5.1, gpt-5, gpt-5-mini, gpt-5-nano, o3, o4-mini
2. **Cached input discount**: Flex cached input is $0.0625/1M vs Standard $0.125/1M (50% cheaper)
3. **Output discount**: Flex output is $5.00/1M vs Standard $10.00/1M (50% cheaper)
4. **Tool calling**: Works with Flex, but may have higher latency
5. **SLA**: Flex has no SLA - Standard fallback protects against this
