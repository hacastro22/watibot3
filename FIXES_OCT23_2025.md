# Critical Fixes Applied - October 23, 2025

## 1. Fixed ManyChat Context Injection Error (Initial Flow)

**Problem**: System was trying to call WATI API for ManyChat users (Facebook/Instagram), causing errors:
```
[ERROR] Error on page 1: can only concatenate str (not "NoneType") to str
```

**Root Cause**: Initial context injection (line 1679) was calling `get_agent_context_for_system_injection()` for ALL users, including ManyChat users with identifiers like `"instagram:187845315"`.

**Fix Applied** (`app/openai_agent.py` lines 1680-1686):
```python
# Get context based on user type (same logic as thread rotation)
if phone_number and phone_number.isdigit():
    # WATI user: use WATI API to fetch message history
    agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
else:
    # ManyChat user (Facebook/Instagram): use local thread_store to fetch message history
    agent_context_system_msg = get_manychat_context_for_system_injection(user_identifier)
```

**Result**: 
- WATI users: Context fetched from WATI API ✅
- ManyChat users: Context fetched from local thread_store ✅
- Empty message handling: Already handled by existing `if agent_context_system_msg:` check ✅

---

## 2. Fixed ManyChat Context Injection Error (Error Recovery Paths)

**Problem**: When errors occurred and the system created fresh conversations for recovery, it was:
1. Trying to call WATI API for ManyChat users (same issue as #1)
2. Throwing `UnboundLocalError: local variable 'response' referenced before assignment` when agent context was empty

**Root Cause**: Two error recovery code paths (lines 1805-1918 and 2053-2134) had the same WATI-only logic and didn't handle the case when no agent context was available.

**Evidence from logs**:
```
INFO:app.openai_agent:[AGENT_CONTEXT] Injecting agent context for conversation conv_68fa47f6b2708197a8777cffb1cf1c440c6584bec3d21b6a
INFO:httpx:HTTP Request: POST https://api.openai.com/v1/responses "HTTP/1.1 400 Bad Request"
WARNING:app.openai_agent:[OpenAI] Tool call error detected: No tool output found for function call...
INFO:app.openai_agent:[OpenAI] Creating fresh conversation and completely restarting
INFO:app.openai_agent:[OpenAI] Created fresh conversation conv_68fa495069448190b4553499df6287c9...
ERROR:app.openai_agent:[OpenAI] Error: local variable 'response' referenced before assignment
```

**Fixes Applied** (`app/openai_agent.py`):

### A. First Error Recovery Block (lines 1829-1918)
```python
# Added imports
from agent_context_injector import (
    get_agent_context_for_system_injection,
    get_manychat_context_for_system_injection
)

# Channel-aware context injection
if phone_number and phone_number.isdigit():
    agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
else:
    agent_context_system_msg = get_manychat_context_for_system_injection(user_identifier)

if agent_context_system_msg:
    # Inject context and make main call
    ...
else:
    # NEW: Make main call without context (prevents UnboundLocalError)
    response = await openai_client.responses.create(...)
    save_response_id(user_identifier, response.id)
```

### B. Second Error Recovery Block (lines 2074-2134)
- Same channel-aware context injection logic
- This block already made the main API call regardless of context, so no UnboundLocalError

**Result**:
- ManyChat users: No more WATI API errors during recovery ✅
- Empty context: System continues with fresh conversation without crashing ✅
- Both recovery paths: Now channel-aware ✅

---

## 3. Fixed Phone Number Injection for Bookings

**Problem**: The `make_booking` API was receiving incorrect phone values like `"AUTO"`, `"from_wa_id"`, or empty strings instead of actual phone numbers.

**Evidence from logs**:
```
"phone_number": "AUTO"
"phone_number": "from_wa_id"
"phone_number": ""
"wa_id": "AUTO"
```

**Root Cause**: 
- Assistant was sending placeholder values
- Auto-injection used `setdefault()` which didn't override these placeholders
- No differentiation between WATI (can infer phone) and ManyChat (must ask customer)

**Fixes Applied** (`app/openai_agent.py`):

### A. Parameter Injection Logic (lines 1920-1942)
```python
# CRITICAL: Always override wa_id with the actual identifier
if 'wa_id' in sig.parameters:
    if phone_number and phone_number.isdigit():
        fn_args['wa_id'] = phone_number  # WATI user
    elif subscriber_id:
        fn_args['wa_id'] = subscriber_id  # ManyChat user

# CRITICAL: Handle phone_number parameter based on channel
if 'phone_number' in sig.parameters:
    current_phone = fn_args.get('phone_number', '')
    if current_phone in ['', 'AUTO', 'from_wa_id', 'from_waid']:
        if phone_number and phone_number.isdigit():
            # WATI user: keep empty so booking_tool extracts local number from wa_id
            # The _extract_phone_from_wa_id function handles country code removal properly
            fn_args['phone_number'] = ''
        else:
            # ManyChat user: phone must be asked from customer
            fn_args['phone_number'] = ''
```

**Key Design**: For WATI users, we intentionally keep `phone_number` **empty** to trigger the `_extract_phone_from_wa_id()` function in booking_tool.py, which properly:
- Removes country code for El Salvador (503) → local 8-digit number
- Handles international numbers correctly
- Validates phone number format

### B. Tool Description Update (line 614-616)
```python
"phone_number": {
    "type": "string",
    "description": "Customer's phone number. For WhatsApp (WATI) users, use 'AUTO' and it will be extracted from waId. For Facebook/Instagram users, this MUST be explicitly asked from the customer and provided here (cannot be inferred)."
}
```

### C. Debug Logging (lines 1936, 1940)
- Logs when WATI phone is auto-filled
- Warns when ManyChat phone cannot be auto-filled

**Result**:
- **WATI users**: 
  - `wa_id` = actual WhatsApp ID (e.g., "50376304472") ✅
  - `phone_number` = empty → triggers `_extract_phone_from_wa_id()` ✅
  - Booking API receives local number (e.g., "76304472" for El Salvador) ✅
- **ManyChat users**: 
  - `wa_id` = subscriber_id (e.g., "facebook:12345") ✅
  - `phone_number` = empty → validation error forces assistant to ask customer ✅
- **wa_id**: Always contains correct identifier for both platforms ✅

---

## Testing Recommendations

### Test 1: ManyChat Context Injection (Normal Flow)
1. Send message from Facebook/Instagram user
2. Check logs for `[AGENT_CONTEXT]` - should use `get_manychat_context_for_system_injection`
3. Should NOT see WATI API errors

### Test 1b: ManyChat Error Recovery
1. If a Facebook/Instagram conversation hits an error requiring recovery
2. Check logs for `[OpenAI] Creating fresh conversation and completely restarting`
3. Should see channel-aware context injection
4. Should NOT see `UnboundLocalError: local variable 'response' referenced before assignment`
5. Conversation should continue successfully

### Test 2: WATI Booking Phone Number
1. Complete booking flow as WATI user (e.g., wa_id: 50376304472)
2. Assistant uses `"phone_number": "AUTO"`
3. Check logs for `[PHONE_FIX] WATI user - phone_number was 'AUTO', keeping empty so booking_tool extracts from wa_id: 50376304472`
4. Check logs for `[PHONE_EXTRACTION] Processing wa_id: 50376304472`
5. Check logs for `[PHONE_EXTRACTION] Extracted local number for El Salvador: 76304472`
6. Verify booking API payload contains `"phone": "76304472"` (local number, no country code)

### Test 3: ManyChat Booking Phone Number
1. Complete booking flow as Facebook/Instagram user
2. If assistant uses `"phone_number": "AUTO"`, should see warning
3. Booking validation should fail with missing phone number
4. Assistant should ask customer for phone number explicitly

---

## Deployment

**Files Modified**:
- `/home/robin/watibot4/app/openai_agent.py`

**Action Required**:
```bash
sudo systemctl restart watibot4
```

**Monitoring**:
- Watch for `[PHONE_FIX] WATI user - phone_number was 'AUTO', keeping empty...` logs
- Watch for `[PHONE_EXTRACTION] Extracted local number for El Salvador: XXXXXXXX` logs
- Watch for `[AGENT_CONTEXT]` logs to confirm proper context source
- No more WATI API concatenation errors for ManyChat users (in normal flow OR recovery)
- No more `UnboundLocalError: local variable 'response' referenced before assignment` errors
- Watch for `[OpenAI] Successfully started fresh conversation without context` (when context is empty)
- Booking API payloads should show `"phone": "XXXXXXXX"` (8 digits for El Salvador, no country code)
