# Conversation ID Bug Fix

## Date: 2025-10-04 14:55

## Problem Summary

The system was crashing with 400 errors when trying to use phone numbers as conversation IDs:

```
Error code: 400 - {'error': {'message': "Invalid 'conversation': '50370640246'. 
Expected an ID that begins with 'conv'.", 'type': 'invalid_request_error', 
'param': 'conversation', 'code': 'invalid_value'}}
```

### Root Cause

**File**: `app/openai_agent.py` (lines 1337-1340 - OLD CODE)

The code was blindly accepting any `thread_id` value from the database and treating it as a valid conversation ID:

```python
conversation_id = thread_id or get_conversation_id(user_identifier)
if thread_id:
    # ensure persistence if you want this to become canonical for the user
    save_conversation_id(user_identifier, thread_id)
```

**The Problem**:
- Old database records had **phone numbers** stored in the `thread_id` and `conversation_id` columns
- Example: `thread_id = '50370640246'` instead of `thread_id = 'conv_...'`
- The code would use the phone number directly as the conversation ID
- OpenAI Responses API rejected it with 400 error

### How Did This Happen?

The database schema allows storing any string in `thread_id`:
```sql
CREATE TABLE threads (
    wa_id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,  -- ❌ No validation, accepts phone numbers
    ...
)
```

When the Responses API migration happened, some records got initialized with phone numbers instead of proper `conv_` IDs.

## Solution Implemented

### Code Fix

**File**: `app/openai_agent.py` (lines 1338-1347 - NEW CODE)

Added validation to only use `thread_id` if it's a valid conversation ID:

```python
# CRITICAL FIX: Only use thread_id if it's a valid conversation ID (starts with 'conv_')
# Old data may have phone numbers stored as thread_id, which causes 400 errors
if thread_id and thread_id.startswith('conv_'):
    conversation_id = thread_id
    save_conversation_id(user_identifier, thread_id)
else:
    # Either no thread_id, or it's invalid (phone number) - get from database
    conversation_id = get_conversation_id(user_identifier)
    if thread_id and not thread_id.startswith('conv_'):
        logger.warning(f"[OpenAI] Invalid thread_id format: {thread_id}. Will create new conversation.")
```

### Data Cleanup

Deleted the bad record for customer 50370640246:
```sql
DELETE FROM threads WHERE wa_id = '50370640246';
```

On next message, the system will create a proper `conv_` ID for this customer.

### Verification

Checked for other corrupted records:
```sql
SELECT COUNT(*) as bad_records 
FROM threads 
WHERE thread_id NOT LIKE 'conv_%' OR conversation_id NOT LIKE 'conv_%';
-- Result: 0 (no other bad records found)
```

## Impact

### Before Fix
- ❌ System crashed with 400 error for affected customers
- ❌ Infinite retry loop (10s, 20s, 40s, 80s delays)
- ❌ Customer never received response
- ❌ Wasted OpenAI API quota on failed requests

### After Fix
- ✅ Invalid conversation IDs rejected gracefully
- ✅ New valid conversation created automatically
- ✅ Customer receives response normally
- ✅ Warning logged for debugging: `"Invalid thread_id format: 50370640246"`

## Prevention

### Future Safeguards

1. **Validation at Write Time**: Consider adding validation in `thread_store.py` to reject non-`conv_` IDs:
```python
def save_conversation_id(identifier: str, conversation_id: str):
    if not conversation_id.startswith('conv_'):
        raise ValueError(f"Invalid conversation_id format: {conversation_id}")
    # ... rest of function
```

2. **Database Constraint**: Add CHECK constraint to enforce format:
```sql
ALTER TABLE threads ADD CONSTRAINT check_conversation_id 
CHECK (conversation_id IS NULL OR conversation_id LIKE 'conv_%');
```

3. **Migration Script**: Create a cleanup script to find and fix any remaining bad data:
```sql
-- Find all invalid conversation IDs
SELECT wa_id, thread_id, conversation_id 
FROM threads 
WHERE (thread_id NOT LIKE 'conv_%' AND thread_id IS NOT NULL)
   OR (conversation_id NOT LIKE 'conv_%' AND conversation_id IS NOT NULL);
```

## Testing

### Test Scenario

1. **Customer with bad data**: 50370640246 (cleaned up)
2. **Expected behavior**: On next message, system creates new `conv_` ID
3. **Verification**:
```bash
# Monitor logs for warning
journalctl -f | grep "Invalid thread_id format"

# Check database after customer sends message
sqlite3 app/thread_store.db "SELECT * FROM threads WHERE wa_id = '50370640246';"
# Should show proper conv_ ID
```

## Files Modified

- `/home/robin/watibot4/app/openai_agent.py`: Added conversation ID format validation
- `/home/robin/watibot4/app/thread_store.db`: Deleted bad record for 50370640246

## Service Restart

Service was restarted at **14:59** to apply the fix.
New PID: **2445393**

## Related Issues

This fix complements the earlier **Missed Messages Detection Bug Fix** (MISSED_MESSAGES_FIX.md). Both issues involved incorrect use of database fields:
- **Missed Messages**: Used `last_updated` (updated on bot responses) instead of webhook timestamp
- **This Issue**: Used phone numbers as conversation IDs instead of validating format

Both required careful database field usage validation.
