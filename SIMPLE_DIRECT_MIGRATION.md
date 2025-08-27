# Comprehensive Migration: Assistant API → Responses API

## Overview
Migrate entire project from deprecated Assistant API (shutting down August 26, 2026) to new Responses API with Conversations.

## Additional API Features

### Include Parameters
Optimize response payloads with selective includes:
- `web_search_call.action.sources` - Include web search sources
- `code_interpreter_call.outputs` - Include code execution outputs
- `file_search_call.results` - Include file search results
- `message.input_image.image_url` - Include image URLs
- `message.output_text.logprobs` - Include log probabilities
- `reasoning.encrypted_content` - Include encrypted reasoning tokens

### Metadata Management
- Conversations support 16 key-value metadata pairs
- Keys: max 64 chars, Values: max 512 chars
- Useful for tagging, filtering, and organizing conversations

### Streaming Support
- SSE streaming for real-time response updates
- Stream obfuscation for security (optional)
- Checkpoint support via `starting_after` parameter

## Benefits of Migration
1. **Simplified Architecture:** No polling loops or run status checking
2. **Better Performance:** Synchronous responses, less API calls
3. **Richer Context:** Items support multiple content types
4. **Modern Features:** Background responses, streaming, selective includes
5. **Latest Model:** Now using GPT-5 for all responses

## Migration Timeline
- **Start:** Immediate
- **Assistant API Shutdown:** August 26, 2026
- **Buffer:** 1.5 years to complete migration

## Critical Notes
- Remember timeout bug in current Assistant API implementation (per memory)
- Responses API already partially attempted but failed - ensure proper implementation this time
- Tool identifier injection already working correctly in current code
- **PRESERVE ALL EXISTING LOGIC**: The migration is ONLY changing the API endpoints, not the business logic
- **contextualized_message MUST be preserved exactly as-is (lines 877-943)**
- **NO SAFETY NET**: Complete migration with no fallback - errors will reveal missing pieces

**Key Changes:**
- Assistants → Inline instructions from `app/resources/system_instructions.txt`
- Threads → Conversations (item-based instead of message-only)
- Runs → Responses (synchronous, no polling)
- Model: gpt-5 (now available)

## Migration Scope

### Affected Files
1. **Primary Migration Targets:**
   - `app/openai_agent.py` - Main Assistant API integration (20+ endpoints)
   - `app/image_classifier.py` - Thread message retrieval
   - `app/booking_tool.py` - Conversation context retrieval
   - `app/thread_store.py` - Thread ID storage → Conversation ID storage

2. **Utility/Testing Files:**
   - `agent_context_injector.py` - Message injection
   - `webhook_vs_api_comparator.py` - Message listing
   - `inspect_thread.py` - Thread inspection
   - `app/main.py` - Thread store references

### Environment Variables

**Variables to Remove:**
- `OPENAI_AGENT_ID` - No longer needed
- `OPENAI_PROMPT_ID` - Not used with inline instructions

### Detailed API Endpoint Migration

### Complete Endpoint Mapping

#### Core Assistant → Responses API Migration
| Current Assistant API | New Responses API | Notes |
|----------------------|-------------------|--------|
| `POST /threads` | `POST /v1/conversations` | Creates conversation object |
| `POST /threads/{id}/messages` | `POST /v1/conversations/{id}/items` | Messages become items |
| `POST /threads/{id}/runs` | `POST /v1/responses` | Synchronous by default, no polling |
| `GET /threads/{id}/runs/{id}` | `GET /v1/responses/{response_id}` | For background responses only |
| `POST /threads/{id}/runs/{id}/cancel` | `POST /v1/responses/{response_id}/cancel` | Only for background responses |
| `POST /threads/{id}/runs/{id}/submit_tool_outputs` | Include in next `POST /v1/responses` | Tool outputs as input items |
| `GET /threads/{id}/runs` | ❌ Not needed | No run listing needed |
| `DELETE /threads/{id}` | `DELETE /v1/conversations/{id}` | Delete entire conversation |

#### Message/Item Operations
| Current Assistant API | New Responses API | Notes |
|----------------------|-------------------|--------|
| `beta.threads.messages.list()` | `GET /v1/conversations/{id}/items` | Returns all item types, not just messages |
| `beta.threads.messages.create()` | `POST /v1/conversations/{id}/items` | Creates items (messages, tool calls, etc.) |
| `beta.threads.messages.retrieve()` | `GET /v1/conversations/{id}/items/{item_id}` | Gets specific item |
| `beta.threads.messages.delete()` | `DELETE /v1/conversations/{id}/items/{item_id}` | Deletes specific item |
| `GET /threads/{id}/messages` | `GET /v1/responses/{response_id}/input_items` | Get input items for a response |

#### New Responses API Capabilities
| New Endpoint | Purpose | Use Case |
|--------------|---------|----------|
| `DELETE /v1/responses/{response_id}` | Delete a response | Clean up failed/test responses |
| `POST /v1/conversations/{id}` | Update conversation metadata | Update conversation tags/metadata |
| `GET /v1/conversations/{id}` | Retrieve conversation details | Get conversation metadata |

## Implementation Details

### 1. Pre-Migration: Prepare System Instructions

**File:** `app/resources/system_instructions.txt`
**Action:** Verify file exists and contains complete instructions
```bash
# Check file exists and is not empty
test -s app/resources/system_instructions.txt && echo "File OK" || echo "File missing/empty"
```

**File:** `app/openai_agent.py`
**Location:** Add after imports (around line 15)
**Add:**
```python
# Add system instruction loading function
def load_system_instructions():
    """Load system instructions from file."""
    with open('app/resources/system_instructions.txt', 'r', encoding='utf-8') as f:
        return f.read()
```

### 2. Replace Thread/Run Logic in `openai_agent.py`

**File:** `app/openai_agent.py`

#### 2.1 Remove Assistant API Variables
**Location:** Lines ~30-35
**Remove:**
```python
OPENAI_AGENT_ID = config.OPENAI_AGENT_ID
OPENAI_PROMPT_ID = config.OPENAI_PROMPT_ID  # If exists
```

#### 2.2 Update OpenAI Client Initialization
**Location:** Lines ~45-50 (where AsyncOpenAI is initialized)
**Replace:**
```python
# OLD:
from openai import AsyncOpenAI
openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)

# NEW:
from openai import AsyncOpenAI
openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
# No change needed - same client for Responses API
```

#### 2.3 Replace get_openai_response Function
**Location:** Lines 875-1250
**PRESERVE:** Lines 877-943 (contextualized_message construction)

**Remove (lines ~966-1010):**
```python
# DELETE ALL OF THIS:
headers = {
    "Authorization": f"Bearer {OPENAI_API_KEY}",
    "Content-Type": "application/json",
    "OpenAI-Beta": "assistants=v2"
}

if not thread_id:
    logger.info(f"[OpenAI] Creating new thread for {wa_id or subscriber_id}")
    thread_resp = await client.post(
        f"{OPENAI_API_BASE}/threads",
        headers=headers
    )
    thread_data = thread_resp.json()
    thread_id = thread_data["id"]
    await thread_store.save_thread_mapping(
        wa_id=wa_id,
        subscriber_id=subscriber_id,
        thread_id=thread_id
    )

msg_payload = {
    "role": "user",
    "content": contextualized_message
}
msg_resp = await client.post(
    f"{OPENAI_API_BASE}/threads/{thread_id}/messages",
    headers=headers,
    json=msg_payload
)
```

**Replace with (insert at line ~966):**
```python
# Load system instructions
system_instructions = load_system_instructions()

# Get or create conversation
conversation_id = await thread_store.get_conversation_id(wa_id or subscriber_id)
if not conversation_id:
    logger.info(f"[OpenAI] Creating new conversation for {wa_id or subscriber_id}")
    conversation = await openai_client.conversations.create(
        metadata={"user_id": wa_id or subscriber_id}
    )
    conversation_id = conversation.id
    await thread_store.save_conversation_id(
        wa_id or subscriber_id,
        conversation_id
    )
```

**Remove (lines ~1011-1035 - Run creation):**
```python
# DELETE ALL OF THIS:
run_payload = {
    "assistant_id": OPENAI_AGENT_ID,
    "model": "gpt-4-turbo-preview",
    "tools": tools_list,
    "temperature": 0.7
}
run_resp = await client.post(
    f"{OPENAI_API_BASE}/threads/{thread_id}/runs",
    headers=headers,
    json=run_payload
)
```

**Replace with (insert at line ~1011):**
```python
# Create response with contextualized message (NO POLLING!)
response = await openai_client.responses.create(
    model="gpt-5",
    conversation=conversation_id,
    input=[
        {"type": "message", "role": "system", "content": system_instructions},
        {"type": "message", "role": "user", "content": contextualized_message}
    ],
    tools=tools_list,
    tool_choice="auto"
)
```

**Remove (lines ~1036-1180 - Entire polling loop):**
```python
# DELETE ENTIRE POLLING LOOP:
while run.status in ("queued", "in_progress", "requires_action"):
    await asyncio.sleep(1)
    # ... all status checking code ...
```

### 3. Handle Tool Calls (Replace Polling)

**File:** `app/openai_agent.py`
**Location:** Replace lines ~1036-1180 (old polling loop)
**Add:**
```python
# Check if response requires tool execution
if response.choices[0].message.tool_calls:
    tool_outputs = []
    
    for tool_call in response.choices[0].message.tool_calls:
        function_name = tool_call.function.name
        function_args = json.loads(tool_call.function.arguments)
        
        logger.info(f"[Tool] Executing {function_name}")
        
        # Keep ALL existing tool execution logic from lines ~1100-1150
        # Just change how we get tool_call info
        
        # Execute tool (preserve existing logic)
        if function_name == "check_room_availability":
            # ... existing implementation ...
        elif function_name == "create_booking":
            # ... existing implementation ...
        # ... all 22 tools ...
        
        tool_outputs.append({
            "tool_call_id": tool_call.id,
            "output": json.dumps(result)
        })
    
    # Submit tool outputs (new API call instead of /submit_tool_outputs)
    response = await openai_client.responses.create(
        model="gpt-5",
        conversation=conversation_id,
        input=[
            {"type": "tool_outputs", "tool_outputs": tool_outputs}
        ]
    )
```

### 4. Extract Final Response

**File:** `app/openai_agent.py`
**Location:** Replace lines ~1181-1200 (response extraction)

**Remove:**
```python
# DELETE OLD EXTRACTION:
final_response = ""
if run.status == "completed":
    messages_resp = await client.get(
        f"{OPENAI_API_BASE}/threads/{thread_id}/messages",
        headers=headers,
        params={"limit": 1, "order": "desc"}
    )
    messages_data = messages_resp.json()
    if messages_data.get("data"):
        final_response = messages_data["data"][0].get("content", [{}])[0].get("text", {}).get("value", "")
```

**Replace with:**
```python
# Extract response from Responses API
final_response = ""
if response.choices and response.choices[0].message:
    final_response = response.choices[0].message.content or ""
    
    # Apply JSON guard (preserve from memory)
    if final_response and len(final_response) < 200:
        try:
            parsed = json.loads(final_response)
            if any(k in str(parsed).lower() for k in ['function', 'arguments', 'name']):
                logger.info("[JSON_GUARD] Detected tool-like JSON, repairing")
                repair_response = await openai_client.responses.create(
                    model="gpt-5",
                    conversation=conversation_id,
                    input=[
                        {"type": "message", "role": "system", "content": "Generate a user-friendly Spanish message."},
                        {"type": "message", "role": "user", "content": f"Convert to natural Spanish: {final_response}"}
                    ]
                )
                final_response = repair_response.choices[0].message.content
        except:
            pass  # Not JSON, use as-is
```

### 5. Update Error Handling

**File:** `app/openai_agent.py`
**Location:** Lines ~1201-1250 (error handling section)

**Remove:**
```python
# DELETE OLD ERROR HANDLING:
except Exception as e:
    if "rate_limit" in str(e).lower():
        # ... rate limit handling ...
    elif "timeout" in str(e).lower():
        # ... timeout recovery with new run creation ...
```

**Replace with:**
```python
try:
    # ... main response logic ...
except Exception as e:
    logger.error(f"[OpenAI] Error: {str(e)}")
    
    # Simpler error handling - no polling means no timeout recovery needed
    if "rate_limit" in str(e).lower():
        await asyncio.sleep(5)
        # Retry once
        response = await openai_client.responses.create(
            model="gpt-5",
            conversation=conversation_id,
            input=[
                {"type": "message", "role": "system", "content": system_instructions},
                {"type": "message", "role": "user", "content": contextualized_message}
            ],
            tools=tools_list
        )
    else:
        # Return error message
        return "Lo siento, ocurrió un error. Por favor intenta de nuevo."
```

### 6. Remove Unused Imports and Constants

**File:** `app/openai_agent.py`
**Location:** Lines 1-50 (top of file)

**Remove:**
```python
# DELETE THESE IMPORTS/CONSTANTS:
import httpx  # If only used for Assistant API
OPENAI_API_BASE = "https://api.openai.com/v1"  # No longer needed
OPENAI_AGENT_ID = config.OPENAI_AGENT_ID  # Delete
OPENAI_PROMPT_ID = config.OPENAI_PROMPT_ID  # Delete if exists
```

**Keep:**
```python
# KEEP THESE:
from openai import AsyncOpenAI
import json
import asyncio
from datetime import datetime
from pytz import timezone
# ... all other necessary imports for tools ...
    order="desc",  # or "asc"
    include=["message.output_text.logprobs"]  # Optional includes
)

# Add multiple items at once (up to 20)
await openai.conversations.items.create(
    conversation_id=conversation_id,
    items=[
        {"type": "message", "role": "user", "content": "Hello"},
        {"type": "message", "role": "assistant", "content": "Hi!"}
    ]
)

# Delete specific item
await openai.conversations.items.delete(
    conversation_id=conversation_id,
    item_id=item_id
)
```

### 7. Tool Migration Details

**Current Tools (22 total):**
- `analyze_payment_proof` - Image/PDF analysis
- `create_compraclick_link` - Payment link generation  
- `check_office_status` - Office hours validation
- `get_price_for_date` - Price lookup
- `send_location_pin` - Location sharing
- `send_menu_pdf` - PDF file sending
- `check_room_availability` - Availability checking
- `check_smart_availability` - Smart availability with alternatives
- `send_bungalow_pictures` - Image sending
- `send_public_areas_pictures` - Image sending
- `sync_compraclick_payments` - Payment sync
- `validate_compraclick_payment` - Payment validation
- `validate_compraclick_payment_fallback` - Fallback validation
- `sync_bank_transfers` - Bank transfer sync
- `validate_bank_transfer` - Bank transfer validation
- `make_booking` - Reservation creation
- `send_email` - Internal email notifications
- `start_bank_transfer_retry_process` - Retry automation
- `mark_customer_frustrated` - Escalation trigger
- `trigger_compraclick_retry_for_missing_payment` - Retry automation

**What Stays the Same:**
- ✅ `tools` array definitions (lines 28-550) - Standard JSON schema format
- ✅ `available_functions` mapping (lines 841-862) - Function implementations
- ✅ Tool execution logic with identifier injection (lines 1127-1171)
- ✅ Async/sync function handling
- ✅ Error handling and logging

**What Changes:**
- **Tool call detection:**
  - **Assistant API:** `run_data.get('required_action', {}).get('submit_tool_outputs', {}).get('tool_calls', [])`
  - **Chat Completions API:** `response.choices[0].message.tool_calls or []`

- **Tool response handling:**
  - **Assistant API:** Submit via `POST /submit_tool_outputs` endpoint
  - **Chat Completions API:** Add tool responses as messages, make new API call

**Tool Call Flow Changes:**
```python
# Current Assistant API flow (remove this)
if status == "requires_action":
    tool_calls = run_data.get('required_action', {}).get('submit_tool_outputs', {}).get('tool_calls', [])
    # Execute tools...
    # Submit via /submit_tool_outputs endpoint

# New Chat Completions API flow  
if response.choices[0].message.tool_calls:
    tool_calls = response.choices[0].message.tool_calls
    # Execute tools (same logic)...
    # Add tool responses to messages array and make new API call
```

### 8. Update Database Schema

**thread_store.py changes:**
```sql
ALTER TABLE conversations ADD COLUMN conversation_id TEXT;
-- Migrate existing thread_ids to conversation_ids
UPDATE conversations SET conversation_id = thread_id WHERE conversation_id IS NULL;
```

### 9. File-by-File Migration Guide

#### `app/openai_agent.py` Changes:
- Remove all httpx direct API calls
- Replace with OpenAI SDK calls
- Remove polling loops
- Remove run status checking
- Remove run cancellation logic
- Update `get_thread_messages()` to use conversations API
- Update `add_message_to_thread()` if keeping manual message addition

#### `app/thread_store.py` Changes:
```python
# Old: Store thread_id
def create_thread(wa_id, thread_id):
    # ...

# New: Store conversation_id  
def create_conversation(wa_id, conversation_id):
    # ...
```

#### `app/image_classifier.py` Changes:
```python
# Old:
messages = await openai_client.beta.threads.messages.list(
    thread_id=thread_id, limit=max_messages, order="desc"
)

# New:
items = await openai_client.conversations.messages.list(
    conversation_id=conversation_id, limit=max_messages, order="desc"
)
```

#### `app/booking_tool.py` Changes:
- Update `_get_full_conversation_context()` to use conversations API
- Replace thread references with conversation_id
- Note: Already using local thread_store instead of thread_id (per memory)

#### `inspect_thread.py` Changes:
- Update to use `GET /v1/conversations/{id}/items`
- Replace thread_id parameter with conversation_id

#### `webhook_vs_api_comparator.py` Changes:
- Update message retrieval to use conversations API
- Handle new item structure instead of just messages

### 10. Testing Strategy

1. **Unit Tests:**
   - Test conversation creation
   - Test message addition
   - Test tool execution
   - Test error handling

2. **Integration Tests:**
   - End-to-end conversation flow
   - Tool calling sequences
   - Payment workflow
   - Booking workflow

3. **Direct Migration (preserving ALL logic):**
   ```python
   # Build contextualized_message EXACTLY as before
   el_salvador_tz = timezone("America/El_Salvador")
   now_in_sv = datetime.now(el_salvador_tz)
   datetime_str = now_in_sv.strftime("%A, %Y-%m-%d, %H:%M")
   contextualized_message = (
       f"The current date, day, and time in El Salvador (GMT-6) is {datetime_str}. "
       # ... ALL existing booking workflow logic (lines 882-942) ...
       f"Do not answer from memory. User query: {message}"
   )
   
   # New Responses API - complete replacement
   with open('app/resources/system_instructions.txt', 'r') as f:
       system_instructions = f.read()
       
   response = await openai.responses.create(
       model="gpt-5",
       conversation=conversation_id,
       input=[
           {"type": "message", "role": "system", "content": system_instructions},
           {"type": "message", "role": "user", "content": contextualized_message}  # SAME contextualized_message!
       ],
       tools=tools
   )
   # No fallback - Assistant API code completely removed
   ```

### 11. Error Detection & Monitoring

1. Monitor error logs during migration
2. Track response times and success rates
3. Errors will immediately reveal any missing functionality
4. No fallback - fix forward approach only

### 12. What Stays the Same
- ✅ Tool definitions (JSON schemas)
- ✅ Tool execution functions
- ✅ Identifier injection logic
- ✅ Error handling patterns
- ✅ **ALL business logic including:**
  - contextualized_message construction (lines 877-943)
  - Booking workflow rules
  - Payment validation logic
  - Office status checks
  - Date/time context injection
  - User query augmentation
- ✅ Database structure (with minor column addition)

## Phased Migration Steps

### Phase 1: Preparation

#### Step 1.1: Database Schema Update
```sql
-- Execute in MySQL/MariaDB
ALTER TABLE thread_store ADD COLUMN conversation_id VARCHAR(255) DEFAULT NULL;
ALTER TABLE thread_store ADD INDEX idx_conversation_id (conversation_id);
```

#### Step 1.2: Verify System Instructions
```bash
# Check that system instructions file exists and is valid JSON
cat app/resources/system_instructions.txt | python -m json.tool
```

### Phase 2: Core Migration

#### Step 2.1: Update `app/openai_agent.py`

**CRITICAL**: Preserve lines 877-943 (contextualized_message construction) exactly as-is.

**Changes to make:**

1. **Remove imports and variables (lines ~1-50):**
   ```python
   # REMOVE:
   OPENAI_AGENT_ID = config.OPENAI_AGENT_ID
   
   # ADD:
   from openai import AsyncOpenAI
   openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
   ```

2. **Replace get_openai_response function (lines ~875-1250):**
   - Keep lines 877-943 (contextualized_message) unchanged
   - Replace thread creation with conversation creation
   - Replace message addition + run creation with single responses.create call
   - Remove all polling logic (lines ~1036-1180)
   - Replace tool submission with new response call

3. **New response creation pattern:**
   ```python
   # Load instructions
   with open('app/resources/system_instructions.txt', 'r') as f:
       system_instructions = f.read()
   
   # Create or get conversation
   if not conversation_id:
       conversation = await openai_client.conversations.create(
           metadata={"user_id": wa_id or subscriber_id}
       )
       conversation_id = conversation.id
       # Save to thread_store
       await thread_store.save_conversation_id(wa_id or subscriber_id, conversation_id)
   
   # Single API call (no polling!)
   response = await openai_client.responses.create(
       model="gpt-5",
       conversation=conversation_id,
       input=[
           {"type": "message", "role": "system", "content": system_instructions},
           {"type": "message", "role": "user", "content": contextualized_message}
       ],
       tools=tools
   )
   ```

4. **Tool execution changes:**
   ```python
   # Check for tool calls
   if response.choices[0].message.tool_calls:
       tool_outputs = []
       for tool_call in response.choices[0].message.tool_calls:
           # Execute tool (keep existing logic)
           result = await execute_tool(tool_call.function.name, tool_call.function.arguments)
           tool_outputs.append({
               "tool_call_id": tool_call.id,
               "output": result
           })
       
       # Submit tool outputs with new response call
       response = await openai_client.responses.create(
           model="gpt-5",
           conversation=conversation_id,
           input=[
               {"type": "tool_outputs", "tool_outputs": tool_outputs}
           ]
       )
   ```

#### Step 2.2: Update `app/thread_store.py`

**Changes:**
1. Replace all `thread_id` with `conversation_id` in function signatures
2. Update SQL queries:
   ```python
   # OLD:
   "SELECT thread_id FROM thread_store WHERE wa_id = ?"
   
   # NEW:
   "SELECT conversation_id FROM thread_store WHERE wa_id = ?"
   ```
3. Add new functions:
   ```python
   async def save_conversation_id(identifier, conversation_id):
       # Save conversation_id to database
   
   async def get_conversation_id(identifier):
       # Retrieve conversation_id from database
   ```

### Phase 3: Supporting Files

#### Step 3.1: Update `app/image_classifier.py`
```python
# Find and replace:
# OLD: await openai_client.beta.threads.messages.list(thread_id=thread_id)
# NEW: await openai_client.conversations.items.list(conversation_id=conversation_id)
```

#### Step 3.2: Update `app/booking_tool.py`
```python
# Already updated per memory - uses thread_store.get_recent_messages()
# Just verify it works with conversation_id instead of thread_id
```

#### Step 3.3: Update utility scripts

**`agent_context_injector.py`:**
```python
# OLD: beta.threads.messages.create/retrieve
# NEW: conversations.items.create
```

**`webhook_vs_api_comparator.py`:**
```python
# OLD: beta.threads.messages.list
# NEW: conversations.items.list
```

**`inspect_thread.py`:**
```python
# OLD: beta.threads.messages.list(thread_id)
# NEW: conversations.items.list(conversation_id)
```

### Phase 4: Testing & Deploy

#### Step 4.1: Test Each Component
```bash
# Test conversation creation
python -c "import app.openai_agent; ..."

# Test each of the 22 tools
python test_complete_flow.py

# Test payment workflows
python test_payment_workflow_fix.py
```

#### Step 4.2: Deploy
1. **Remove environment variables:**
   ```bash
   # From .env file, remove:
   OPENAI_AGENT_ID=...
   OPENAI_PROMPT_ID=...
   ```

2. **Deploy the updated code**
   - No gradual rollout - complete replacement
   - Monitor logs for errors

#### Step 4.3: Monitor
```bash
# Watch for errors
tail -f logs/app.log | grep ERROR

# Monitor response times
# Check that responses < 3 seconds (vs old 5-30 seconds)
```

### Phase 5: Thread History Migration (Post-Migration)

#### Step 5.1: Add Migration Tracking
```sql
-- Add column to track migration status
ALTER TABLE thread_store ADD COLUMN thread_migration TINYINT DEFAULT 0;

-- Create table for thread history
CREATE TABLE thread_history (
    id INT AUTO_INCREMENT PRIMARY KEY,
    wa_id VARCHAR(255),
    subscriber_id VARCHAR(255),
    thread_id VARCHAR(255),
    messages JSON,
    exported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_wa_id (wa_id),
    INDEX idx_subscriber_id (subscriber_id)
);
```

#### Step 5.2: Create `export_threads.py`
```python
#!/usr/bin/env python3
import asyncio
import json
from openai import AsyncOpenAI
from app import thread_store

async def export_all_threads():
    client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
    
    # Get all identifiers with threads
    identifiers = await thread_store.get_all_identifiers_with_threads()
    
    for identifier, thread_id in identifiers:
        # Fetch messages from Assistant API
        messages = await client.beta.threads.messages.list(
            thread_id=thread_id,
            limit=100  # Get last 100 messages
        )
        
        # Store in thread_history table
        await store_thread_history(identifier, thread_id, messages)
    
    print(f"Exported {len(identifiers)} threads")

asyncio.run(export_all_threads())
```

#### Step 5.3: Update `app/openai_agent.py` for seeding
```python
# Add to get_openai_response() after conversation creation:
if conversation_id:
    # Check if needs migration
    migration_status = await thread_store.get_migration_status(wa_id or subscriber_id)
    
    if migration_status == 0:
        # Get thread history
        history = await get_thread_history_from_db(wa_id or subscriber_id)
        
        if history:
            # Seed conversation with last 20 messages
            for msg in history[-20:]:
                await openai_client.conversations.items.create(
                    conversation_id=conversation_id,
                    type="message",
                    role=msg['role'],
                    content=[{"type": "text", "text": msg['content']}]
                )
        
        # Mark as migrated
        await thread_store.set_migration_status(wa_id or subscriber_id, 1)
```

## Success Metrics
- ✅ All 22 tools working correctly
- ✅ Response time < 3 seconds (vs current 5-30 seconds)
- ✅ No polling timeouts
- ✅ Error rate < 1%
- ✅ Successful handling of concurrent conversations
