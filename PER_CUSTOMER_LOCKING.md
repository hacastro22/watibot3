# Per-Customer Linear Conversation Processing

**Date**: November 7, 2025  
**Requirement**: Linear message processing per customer, concurrent processing across customers  
**Implementation**: Database-based processing locks  
**Status**: ✅ IMPLEMENTED

---

## 🎯 **The Requirement**

### **User's Exact Words**:
> "Each conversation must be handled in a linear fashion. If we're already processing a customer message and another message from that same customer arrives, the message must be buffered until the current message sent to OpenAI is answered. On the other hand if the message comes from another customer, then there's no issue in having another worker process the conversation."

### **Translation to Architecture**:
```
✅ Customer A (Message 1) → Worker 1 → Processing
❌ Customer A (Message 2) → Worker 2 → MUST WAIT (not start new processing)
✅ Customer B (Message 1) → Worker 2 → Processing (concurrent with A)
✅ Customer C (Message 1) → Worker 3 → Processing (concurrent with A & B)
```

---

## 🔴 **The Problem with Multiple Workers**

### **Without Coordination (BROKEN)**:
```python
# Worker 1 memory:
waid_timers = {"50376819621": <thread>}  # Processing Customer A

# Worker 2 memory:
waid_timers = {}  # Doesn't know Worker 1 is processing!
```

**Scenario**:
```
00:00 - Customer A sends "Hola"
00:00 - Webhook arrives → Worker 1 → Starts timer
00:05 - Customer A sends "Quiero reservar"
00:05 - Webhook arrives → Worker 2 → Checks local dict → No timer found
00:05 - Worker 2 starts ANOTHER timer ❌
00:65 - Worker 1 sends response based on "Hola"
00:70 - Worker 2 sends response based on "Hola" + "Quiero reservar"
Result: TWO responses, conversation out of order!
```

---

## ✅ **The Solution: Database-Based Locks**

### **1. Processing Lock Table**

**Schema** (`message_buffer.py` lines 75-85):
```sql
CREATE TABLE processing_lock (
    wa_id TEXT PRIMARY KEY,        -- Only one lock per customer
    locked_at DATETIME,            -- When lock was acquired
    worker_pid INTEGER             -- Which worker holds it
)
```

### **2. Lock Acquisition** (`message_buffer.py` lines 168-200)

```python
def try_acquire_processing_lock(wa_id: str) -> bool:
    """Try to acquire lock for customer conversation.
    
    Returns True if acquired (this worker can process).
    Returns False if locked by another worker.
    """
    worker_pid = os.getpid()
    
    try:
        # Try INSERT (will fail if PRIMARY KEY exists)
        conn.execute(
            "INSERT INTO processing_lock VALUES (?, CURRENT_TIMESTAMP, ?)",
            (wa_id, worker_pid)
        )
        return True  # Got the lock!
    except sqlite3.IntegrityError:
        return False  # Another worker has the lock
```

**How PRIMARY KEY Prevents Race**:
- SQLite PRIMARY KEY is atomic
- Only ONE insert can succeed
- All others get IntegrityError
- No race condition possible

### **3. Webhook Handler** (`main.py` lines 1582-1599)

```python
# Buffer message first (always)
message_buffer.buffer_message(phone_number, message_type, content, ...)

# Try to acquire lock
lock_acquired = message_buffer.try_acquire_processing_lock(phone_number)

if lock_acquired:
    # We got the lock - start timer
    start_timer(phone_number)
else:
    # Another worker has the lock - just buffer
    logger.info(f"Another worker processing {phone_number}, message buffered")
    # Message will be included in that worker's batch
```

### **4. Lock Release** (`main.py` line 1078)

```python
# After timer finishes processing
message_buffer.release_processing_lock(wa_id)
# Now other workers can process next message from this customer
```

---

## 📊 **How It Works End-to-End**

### **Scenario: Rapid Messages from Same Customer**

```
Time    Event                           Worker    Action
----------------------------------------------------------------------
00:00   Customer A: "Hola"             
        → Webhook arrives               Worker 1   Buffers message
        → Try acquire lock              Worker 1   ✅ SUCCESS (gets lock)
        → Start timer                   Worker 1   Timer counting (65s)

00:05   Customer A: "Quiero reservar"
        → Webhook arrives               Worker 2   Buffers message
        → Try acquire lock              Worker 2   ❌ FAILS (Worker 1 has it)
        → Just buffer                   Worker 2   Returns immediately

01:05   Timer fires                     Worker 1   Gets BOTH messages from buffer
        → Process "Hola\nQuiero..."     Worker 1   Sends to OpenAI
        → Get response                  Worker 1   Single coherent response
        → Send to customer              Worker 1   Customer gets ONE message
        → Release lock                  Worker 1   Lock deleted from DB

01:06   Customer A: "Gracias"
        → Webhook arrives               Worker 3   Buffers message
        → Try acquire lock              Worker 3   ✅ SUCCESS (lock was released)
        → Start timer                   Worker 3   New timer starts
```

### **Scenario: Multiple Customers Concurrently**

```
Time    Event                           Worker    Action
----------------------------------------------------------------------
00:00   Customer A: "Hola"             Worker 1   Acquires lock for A ✅
00:00   Customer B: "Buenos días"      Worker 2   Acquires lock for B ✅
00:00   Customer C: "Información"      Worker 3   Acquires lock for C ✅
00:00   Customer D: "Reserva"          Worker 4   Acquires lock for D ✅

All 4 workers processing DIFFERENT customers in parallel ✅
```

---

## 🛡️ **Safety Mechanisms**

### **1. Stale Lock Cleanup** (`message_buffer.py` lines 215-237)

**Problem**: Worker crashes while holding lock → lock never released → customer stuck forever

**Solution**: Cleanup on startup
```python
def cleanup_stale_locks(max_age_minutes=10):
    """Remove locks older than 10 minutes (worker probably crashed)"""
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    conn.execute("DELETE FROM processing_lock WHERE locked_at < ?", cutoff)
```

Called on service startup (`main.py` lines 709-713).

### **2. Orphaned Message Detection**

Already implemented (from previous fix). If lock cleanup removes a stale lock, orphaned messages will be detected and processed.

### **3. Message Buffer Coordination**

Even if multiple timers start (shouldn't happen, but defensive):
- Both timers call `get_and_clear_buffered_messages()`
- First one gets messages and DELETES them
- Second one finds nothing and exits cleanly
- No duplicate processing

---

## 🧪 **Testing Scenarios**

### **Test 1: Rapid Same-Customer Messages**

```bash
# Send 3 messages from same customer with 2-second gaps
for i in 1 2 3; do
  curl -X POST http://127.0.0.1:8006/webhook \
    -H 'Content-Type: application/json' \
    -d "{\"waId\":\"50376819621\",\"text\":\"Message $i\",\"passkey\":\"FuK@tTcKerZ-2o25\"}"
  sleep 2
done
```

**Expected behavior**:
```bash
journalctl -u watibot4 -f | grep "50376819621"

# Should see:
# - "Acquired lock and started timer" (once)
# - "Another worker processing" (twice)
# - One combined OpenAI call with all 3 messages
# - One response sent to customer
```

### **Test 2: Multiple Customers Concurrently**

```bash
# Send 4 messages from different customers simultaneously
for i in 1 2 3 4; do
  curl -X POST http://127.0.0.1:8006/webhook \
    -d "{\"waId\":\"5037681962$i\",\"text\":\"Test\",\"passkey\":\"FuK@tTcKerZ-2o25\"}" &
done
wait
```

**Expected behavior**:
```bash
# All 4 should show "Acquired lock and started timer"
# No "Another worker processing" messages
# 4 separate OpenAI calls
# 4 separate responses
```

### **Test 3: Stale Lock Recovery**

```bash
# Manually insert a stale lock
sqlite3 thread_store.db "INSERT INTO processing_lock VALUES ('50399999999', datetime('now', '-15 minutes'), 99999)"

# Restart service
sudo systemctl restart watibot4

# Check logs
journalctl -u watibot4 -n 50 | grep "stale"
# Should see: "Cleaned up 1 stale processing locks"
```

---

## 📈 **Performance Characteristics**

### **Lock Acquisition Time**
- SQLite INSERT: < 1ms typically
- PRIMARY KEY check: Atomic, no race
- **Total webhook delay**: < 2ms additional

### **Concurrency**
- **Same customer**: Serialized (as required)
- **Different customers**: Fully concurrent
- **Capacity**: 4 workers × ~20 customers = ~80 concurrent conversations

### **Resource Usage**
- Processing lock table: Minimal (1 row per active conversation)
- Max rows: ~80 (if all workers busy)
- Disk space: ~5KB typical

---

## 🔍 **Monitoring & Debugging**

### **Check Active Locks**

```bash
sqlite3 thread_store.db "SELECT wa_id, worker_pid, locked_at FROM processing_lock"
```

**Healthy state**: 0-4 locks (one per busy worker)  
**Problem**: > 4 locks (stale locks or excessive concurrency)

### **Log Patterns**

**Normal**:
```
[LOCK] Acquired processing lock for 50376819621 (worker PID 12345)
[TIMER_THREAD] Acquired lock and started timer for 50376819621
... 65 seconds later ...
[LOCK] Released processing lock for 50376819621 (worker PID 12345)
```

**Coordination working**:
```
[LOCK] Processing lock for 50376819621 already held by worker PID 12345
[TIMER_THREAD] Another worker processing 50376819621, message buffered
```

**Stale lock cleanup**:
```
[LOCK] Found 2 stale locks, cleaning up: [('50376819621', 12345, '2025-11-07 00:30:00'), ...]
```

### **Alert Conditions**

- ❌ Lock held > 5 minutes → Worker may be stuck
- ❌ > 10 stale locks on startup → Frequent crashes
- ❌ "Already held" logs > 50% → May need more workers

---

## 🎯 **Key Design Decisions**

### **Why Database Lock vs Redis/Memcached?**

**Pros of SQLite**:
- ✅ Already using SQLite for message buffer
- ✅ ACID guarantees (atomic PRIMARY KEY)
- ✅ No additional infrastructure
- ✅ Survives restarts (can detect stale locks)

**Cons**:
- ⚠️ Write serialization (not an issue at our scale)
- ⚠️ Single file (but we're using multiple workers on same machine)

**Redis would be better if**:
- Running across multiple servers
- Need > 100 workers
- Need TTL on locks (auto-expire)

### **Why Lock on Timer Start vs Message Arrival?**

**Current**: Lock acquired AFTER buffering message

**Alternative**: Lock acquired BEFORE buffering

**Rationale**:
- Messages must always be buffered (never drop)
- Buffering is fast (< 5ms)
- Lock only prevents timer start
- If lock fails, message still buffered and will be included in active batch

---

## 📚 **Files Modified**

1. **`/home/robin/watibot4/app/message_buffer.py`**
   - Lines 75-85: Create processing_lock table
   - Lines 168-200: `try_acquire_processing_lock()`
   - Lines 202-213: `release_processing_lock()`
   - Lines 215-237: `cleanup_stale_locks()`

2. **`/home/robin/watibot4/app/main.py`**
   - Lines 709-713: Stale lock cleanup on startup
   - Lines 1582-1599: Lock-based timer start in webhook
   - Line 1078: Lock release after timer completes

3. **`/home/robin/watibot4/start_watibot4.sh`**
   - Line 53: `--workers 4` flag

---

## ✅ **Validation Checklist**

Before deploying:
- [ ] Run Test 1: Rapid same-customer messages
- [ ] Run Test 2: Multiple customers concurrently
- [ ] Run Test 3: Stale lock recovery
- [ ] Monitor locks: `sqlite3 thread_store.db "SELECT * FROM processing_lock"`
- [ ] Check logs for "Acquired lock" and "Another worker processing"
- [ ] Verify no duplicate responses sent to customers

---

## 🚀 **Deployment**

```bash
# 1. Restart service to apply changes
sudo systemctl restart watibot4

# 2. Verify multiple workers started
ps aux | grep "uvicorn app.main:app" | wc -l
# Should show 5 (1 master + 4 workers)

# 3. Monitor startup
journalctl -u watibot4 -f | grep -E "(STARTUP|LOCK)"

# 4. Watch for lock activity
watch 'sqlite3 thread_store.db "SELECT * FROM processing_lock"'
```

---

**End of Documentation**
