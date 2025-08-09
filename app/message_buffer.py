import sqlite3
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta

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
            # Create new table
            conn.execute("""
            CREATE TABLE message_buffer (
                wa_id TEXT NOT NULL,
                message_type TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            # Copy data (assuming old messages are 'text')
            conn.execute("""
            INSERT INTO message_buffer (wa_id, message_type, content, timestamp)
            SELECT wa_id, 'text', message, timestamp FROM message_buffer_old
            """)
            # Drop old table
            conn.execute("DROP TABLE message_buffer_old")
            logger.info("Schema migration complete.")
        else:
            # Create table if it doesn't exist (for fresh setups)
            conn.execute("""
            CREATE TABLE IF NOT EXISTS message_buffer (
                wa_id TEXT NOT NULL,
                message_type TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
        conn.commit()

def buffer_message(wa_id: str, message_type: str, content: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO message_buffer (wa_id, message_type, content, timestamp) VALUES (?, ?, ?, CURRENT_TIMESTAMP)",
            (wa_id, message_type, content)
        )
        conn.commit()

def get_and_clear_buffered_messages(wa_id: str, since_seconds: int = 65):
    """Get all messages for a wa_id from the last `since_seconds`, then delete them."""
    # CRITICAL FIX: Add extra buffer to account for timing discrepancies and processing delays
    # The timer waits 60 seconds but we need to catch messages from the very start of that period
    cutoff = datetime.utcnow() - timedelta(seconds=since_seconds + 10)  # Extra 10 second safety buffer
    with get_conn() as conn:
        cursor = conn.execute(
            "SELECT message_type, content FROM message_buffer WHERE wa_id = ? AND timestamp >= ? ORDER BY timestamp ASC",
            (wa_id, cutoff.strftime('%Y-%m-%d %H:%M:%S'))
        )
        messages = [{'type': row[0], 'content': row[1]} for row in cursor.fetchall()]
        
        # CRITICAL FIX: Only delete messages within the time window, not ALL messages for this waId
        # This prevents deletion of messages that arrived after the timer started
        conn.execute("DELETE FROM message_buffer WHERE wa_id = ? AND timestamp >= ?", 
                    (wa_id, cutoff.strftime('%Y-%m-%d %H:%M:%S')))
        conn.commit()
        
    return messages
