# Image Classifier Bug Fix - "No Conversation Context Available"

## Incident Report
**Date**: 2025-10-08 at 12:40:13  
**Customer Affected**: waId 50372927077  
**Error Message Sent**: "Error: No conversation context available"

## Root Cause

When a customer sent an image and **no existing conversation ID** was found in thread_store, the `image_classifier.py` module returned an error message to the customer instead of creating a new conversation.

### Timeline
1. **12:39:32** - Customer sends image
2. **12:40:07** - Message buffer timer expires, starts processing
3. **12:40:08** - Image classifier invoked
4. **12:40:13** - Image classified as "general_inquiry" (90% confidence)
5. **12:40:13** - `handle_general_inquiry_image()` checks for conversation_id
6. **12:40:13** - ❌ **No conversation_id found** → Returns error to customer
7. **12:40:15** - Error message sent via WATI: "Error: No conversation context available"

### The Bug
In `/home/robin/watibot4/app/image_classifier.py` (lines 241-247 - OLD CODE):

```python
if not conversation_id:
    logger.error(f"[IMAGE_CLASSIFIER] No conversation ID found for wa_id: {wa_id}")
    return {
        "success": False,
        "response_text": "Error: No conversation context available",  # ← SENT TO CUSTOMER!
        "error": "No conversation ID found"
    }
```

### Expected Behavior
The `openai_agent.py` module handles this correctly by **creating a new conversation** when one doesn't exist:

```python
if not conversation_id:
    logger.info(f"[OpenAI] Creating new conversation for {user_identifier}")
    # Creates conversation via Conversations API
    # Saves to thread_store
    # Continues processing
```

## The Fix

### Changes Made to `/home/robin/watibot4/app/image_classifier.py`

#### 1. Added httpx Import (Line 15)
```python
import httpx
```

#### 2. Replaced Error Return with Conversation Creation (Lines 242-263)
```python
# Create new conversation if one doesn't exist (same logic as openai_agent.py)
if not conversation_id:
    logger.info(f"[IMAGE_CLASSIFIER] No conversation ID found for wa_id: {wa_id}, creating new conversation")
    from . import config
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(
            "https://api.openai.com/v1/conversations",
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={}
        )
        response.raise_for_status()
        conv_data = response.json()
        conversation_id = conv_data.get("id")
        if not conversation_id:
            raise RuntimeError("Conversations API returned no id")
    
    thread_store.save_conversation_id(wa_id, conversation_id)
    logger.info(f"[IMAGE_CLASSIFIER] Created new conversation {conversation_id} for wa_id: {wa_id}")
    previous_response_id = None  # Fresh conversation has no previous response
```

## Impact

### Before Fix
- ❌ Customers sending images without existing conversation received error messages
- ❌ Poor customer experience
- ❌ Inconsistent behavior between text messages (which create conversations) and images

### After Fix
- ✅ Images automatically create new conversations if needed
- ✅ Consistent behavior with text message handling
- ✅ Better customer experience - no error messages
- ✅ Image processing continues normally

## Testing Recommendations

1. **New Conversation Test**: Send an image from a customer with no existing conversation
   - Expected: Image is processed and receives appropriate AI response
   
2. **Existing Conversation Test**: Send an image from a customer with active conversation
   - Expected: Image continues existing conversation context

3. **Edge Case Test**: Send image after conversation has expired/been deleted
   - Expected: New conversation created automatically

## Deployment

**Status**: Code fixed, ready for deployment  
**Action Required**: Restart watibot4 service

```bash
sudo systemctl restart watibot4
sudo systemctl status watibot4  # Verify running
```

## Log Verification

After restart, look for these log patterns:

### Success Pattern (New Conversation Created)
```
[IMAGE_CLASSIFIER] No conversation ID found for wa_id: XXXXX, creating new conversation
[IMAGE_CLASSIFIER] Created new conversation conv_XXXXX for wa_id: XXXXX
```

### Normal Pattern (Existing Conversation)
```
[IMAGE_CLASSIFIER] Loaded system instructions: XXXXX chars
[IMAGE_CLASSIFIER] Sending general inquiry image to Responses API
```

## Related Files
- `/home/robin/watibot4/app/image_classifier.py` (FIXED)
- `/home/robin/watibot4/app/openai_agent.py` (Reference implementation)
- `/home/robin/watibot4/app/thread_store.py` (Conversation ID storage)

## Prevention
This bug occurred because the image classifier module didn't follow the same conversation initialization pattern as the main agent. 

**Best Practice**: All modules that interact with OpenAI Responses API should:
1. Check for existing conversation_id
2. **Create new conversation if none exists** (don't error)
3. Continue processing normally

---
**Fixed By**: Cascade AI  
**Date**: 2025-10-08  
**Initial Fix**: 12:40 UTC - Added conversation creation logic  
**Scoping Fix**: 15:44 UTC - Removed duplicate `import httpx` on line 364 (UnboundLocalError)  
**Verification Status**: Syntax verified ✅, awaiting restart (requires sudo)

## Issue #2: UnboundLocalError (15:42 UTC)

After deploying the initial fix, a Python scoping error occurred:

```
ERROR:app.image_classifier:[IMAGE_CLASSIFIER] Error handling general inquiry image: 
local variable 'httpx' referenced before assignment
UnboundLocalError: local variable 'httpx' referenced before assignment
```

**Root Cause**: Duplicate `import httpx` on line 364 inside the function caused Python to treat `httpx` as a local variable throughout the entire function scope. When line 246 tried to use `httpx.AsyncClient()`, the variable wasn't assigned yet.

**Fix**: Removed the local `import httpx` on line 364 since httpx is already imported globally at line 15.

**Lesson**: When adding global imports, search for and remove any existing local imports of the same module to avoid scoping conflicts.
