import sqlite3
import os
import threading
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("THREAD_DB_PATH", "thread_store.db")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

def init_message_buffer_db():
    with get_conn() as conn:
        # Check if the table needs migration
        cursor = conn.execute("PRAGMA table_info(message_buffer)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'message' in columns and 'content' not in columns:
            logger.info("Old 'message_buffer' schema detected. Migrating to new schema.")
            # Rename old table
            conn.execute("ALTER TABLE message_buffer RENAME TO message_buffer_old")
            # Create new table with caption field
            conn.execute("""
            CREATE TABLE message_buffer (
                wa_id TEXT NOT NULL,
                message_type TEXT NOT NULL,
                content TEXT NOT NULL,
                caption TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            # Copy data (assuming old messages are 'text')
            conn.execute("""
            INSERT INTO message_buffer (wa_id, message_type, content, caption, timestamp)
            SELECT wa_id, 'text', message, NULL, timestamp FROM message_buffer_old
            """)
            # Drop old table
            conn.execute("DROP TABLE message_buffer_old")
            logger.info("Schema migration complete.")
        elif 'caption' not in columns:
            # Add caption column to existing table
            logger.info("Adding 'caption' column to message_buffer table")
            conn.execute("ALTER TABLE message_buffer ADD COLUMN caption TEXT")
            logger.info("Caption column added successfully")
        
        # Check if reply_context_id column exists, add if not
        cursor = conn.execute("PRAGMA table_info(message_buffer)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'reply_context_id' not in columns:
            logger.info("Adding 'reply_context_id' column to message_buffer table")
            conn.execute("ALTER TABLE message_buffer ADD COLUMN reply_context_id TEXT")
            logger.info("Reply context column added successfully")
        
        # Create table if it doesn't exist (for fresh setups)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS message_buffer (
            wa_id TEXT NOT NULL,
            message_type TEXT NOT NULL,
            content TEXT NOT NULL,
            caption TEXT,
            reply_context_id TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """)
        conn.commit()
        
        # Create processing_lock table for cross-worker coordination
        # This ensures only ONE worker processes a customer at a time
        conn.execute("""
        CREATE TABLE IF NOT EXISTS processing_lock (
            wa_id TEXT PRIMARY KEY,
            locked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            worker_pid INTEGER
        )
        """)
        conn.commit()
        logger.info("Processing lock table initialized")

def buffer_message(wa_id: str, message_type: str, content: str, caption: str = None, reply_context_id: str = None):
    """Buffer a message with optional caption and reply context.
    
    Args:
        wa_id: WhatsApp/conversation ID
        message_type: Type of message (text, image, audio, etc.)
        content: Primary content (text for text messages, file path/URL for media)
        caption: Optional caption text that accompanies media (images, videos, etc.)
        reply_context_id: Optional ID of message being replied to
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO message_buffer (wa_id, message_type, content, caption, reply_context_id, timestamp) VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (wa_id, message_type, content, caption, reply_context_id)
        )
        conn.commit()

def get_and_clear_buffered_messages(wa_id: str, since_seconds: int = 35):
    """Get all messages for a wa_id from the last `since_seconds`, then delete them."""
    # Calculate cutoff time - look back exactly the specified number of seconds
    cutoff = datetime.utcnow() - timedelta(seconds=since_seconds)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    
    logger.info(f"[BUFFER_DEBUG] Retrieving messages for {wa_id} since {cutoff_str} (looking back {since_seconds}s)")
    
    with get_conn() as conn:
        # First, let's see all messages for this wa_id to debug
        debug_cursor = conn.execute(
            "SELECT message_type, content, caption, timestamp FROM message_buffer WHERE wa_id = ? ORDER BY timestamp DESC LIMIT 10",
            (wa_id,)
        )
        all_messages = debug_cursor.fetchall()
        logger.info(f"[BUFFER_DEBUG] Found {len(all_messages)} total messages for {wa_id}: {[(m[0], m[3]) for m in all_messages]}")
        
        # Now get messages within the cutoff window - include caption and reply_context_id
        cursor = conn.execute(
            "SELECT message_type, content, caption, reply_context_id, timestamp FROM message_buffer WHERE wa_id = ? AND timestamp >= ? ORDER BY timestamp ASC",
            (wa_id, cutoff_str)
        )
        raw_messages = cursor.fetchall()
        messages = [{'type': row[0], 'content': row[1], 'caption': row[2], 'reply_context_id': row[3]} for row in raw_messages]
        
        logger.info(f"[BUFFER_DEBUG] Found {len(messages)} messages within cutoff window for {wa_id}: {[(m[0], m[3]) for m in raw_messages]}")
        
        # CRITICAL FIX: Only delete messages within the time window, not ALL messages for this waId
        # This prevents deletion of messages that arrived after the timer started
        conn.execute("DELETE FROM message_buffer WHERE wa_id = ? AND timestamp >= ?", 
                    (wa_id, cutoff_str))
        conn.commit()
        
        logger.info(f"[BUFFER_DEBUG] Deleted {len(messages)} messages from buffer for {wa_id}")
        
    return messages

def has_buffered_messages(wa_id: str) -> bool:
    """Check if there are any buffered messages for a wa_id.
    
    Used to detect orphaned messages that arrived after a timer retrieved its batch
    but before the timer completed processing.
    """
    with get_conn() as conn:
        cursor = conn.execute(
            "SELECT COUNT(*) FROM message_buffer WHERE wa_id = ?",
            (wa_id,)
        )
        count = cursor.fetchone()[0]
        return count > 0

def get_all_wa_ids_with_buffered_messages() -> list:
    """Get all wa_ids that currently have messages in the buffer.
    
    Used on startup to detect orphaned messages that were never processed
    due to crashes or exceptions.
    """
    with get_conn() as conn:
        cursor = conn.execute(
            "SELECT DISTINCT wa_id FROM message_buffer ORDER BY wa_id"
        )
        wa_ids = [row[0] for row in cursor.fetchall()]
        return wa_ids

def try_acquire_processing_lock(wa_id: str) -> bool:
    """Try to acquire processing lock for a customer conversation.
    
    Returns True if lock was acquired (this worker can process).
    Returns False if another worker already holds the lock.
    
    This ensures linear processing within each customer conversation
    while allowing concurrent processing of different customers.
    """
    import os
    worker_pid = os.getpid()
    
    with get_conn() as conn:
        try:
            # Try to insert lock (will fail if already exists due to PRIMARY KEY)
            conn.execute(
                "INSERT INTO processing_lock (wa_id, locked_at, worker_pid) VALUES (?, CURRENT_TIMESTAMP, ?)",
                (wa_id, worker_pid)
            )
            conn.commit()
            logger.info(f"[LOCK] Acquired processing lock for {wa_id} (worker PID {worker_pid})")
            return True
        except sqlite3.IntegrityError:
            # Lock already exists - another worker is processing this customer
            cursor = conn.execute(
                "SELECT worker_pid, locked_at FROM processing_lock WHERE wa_id = ?",
                (wa_id,)
            )
            row = cursor.fetchone()
            if row:
                existing_pid, locked_at = row
                logger.info(f"[LOCK] Processing lock for {wa_id} already held by worker PID {existing_pid} (locked at {locked_at})")
            return False

def release_processing_lock(wa_id: str):
    """Release processing lock for a customer conversation.
    
    Should be called after timer_callback completes processing.
    """
    import os
    worker_pid = os.getpid()
    
    with get_conn() as conn:
        conn.execute("DELETE FROM processing_lock WHERE wa_id = ?", (wa_id,))
        conn.commit()
        logger.info(f"[LOCK] Released processing lock for {wa_id} (worker PID {worker_pid})")

def cleanup_stale_locks(max_age_minutes: int = 10):
    """Clean up stale locks from crashed/restarted workers.
    
    A lock is considered stale if:
    1. It's older than max_age_minutes, OR
    2. The worker PID no longer exists (service restarted)
    """
    import os
    from datetime import timedelta
    
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    
    with get_conn() as conn:
        # Get ALL locks to check PID existence
        cursor = conn.execute("SELECT wa_id, worker_pid, locked_at FROM processing_lock")
        all_locks = cursor.fetchall()
        
        stale_locks = []
        for wa_id, worker_pid, locked_at in all_locks:
            # Check if lock is old
            is_old = locked_at < cutoff_str
            
            # Check if worker PID still exists
            try:
                os.kill(worker_pid, 0)  # Signal 0 just checks if process exists
                pid_exists = True
            except (OSError, ProcessLookupError):
                pid_exists = False
            
            # Lock is stale if old OR PID doesn't exist
            if is_old or not pid_exists:
                stale_locks.append((wa_id, worker_pid, locked_at, "old" if is_old else "dead_pid"))
        
        if stale_locks:
            logger.warning(f"[LOCK] Found {len(stale_locks)} stale locks: {[(wa, pid, reason) for wa, pid, _, reason in stale_locks]}")
            # Delete all stale locks
            wa_ids_to_delete = [wa_id for wa_id, _, _, _ in stale_locks]
            placeholders = ','.join('?' * len(wa_ids_to_delete))
            conn.execute(f"DELETE FROM processing_lock WHERE wa_id IN ({placeholders})", wa_ids_to_delete)
            conn.commit()
            logger.info(f"[LOCK] Cleaned up {len(stale_locks)} stale locks")
        
        return len(stale_locks)

def cleanup_old_buffered_messages(max_age_minutes: int = 5):
    """Clean up old buffered messages that are no longer relevant.
    
    Messages older than max_age_minutes are removed.
    This prevents accumulation of stale messages from service restarts
    where timers were killed mid-flight.
    
    Default 5 minutes = messages that would have been processed by timer (65s) 
    plus some safety margin for slow processing.
    """
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(minutes=max_age_minutes)
    cutoff_str = cutoff.strftime('%Y-%m-%d %H:%M:%S')
    
    with get_conn() as conn:
        # First check what we're about to delete
        cursor = conn.execute(
            "SELECT wa_id, COUNT(*) as count FROM message_buffer WHERE timestamp < ? GROUP BY wa_id",
            (cutoff_str,)
        )
        old_messages = cursor.fetchall()
        
        if old_messages:
            total = sum(count for _, count in old_messages)
            logger.warning(f"[BUFFER_CLEANUP] Found {total} old messages across {len(old_messages)} wa_ids (older than {max_age_minutes} minutes)")
            logger.info(f"[BUFFER_CLEANUP] Customers: {[(wa_id, count) for wa_id, count in old_messages]}")
            
            # Delete old messages
            conn.execute("DELETE FROM message_buffer WHERE timestamp < ?", (cutoff_str,))
            conn.commit()
            logger.info(f"[BUFFER_CLEANUP] Cleaned up {total} old buffered messages")
            return total
        
        return 0
