import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("THREAD_DB_PATH", "app/thread_store.db")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    """Initializes the database and safely migrates the schema."""
    with get_conn() as conn:
        # Create table with the full, ideal schema
        conn.execute("""
        CREATE TABLE IF NOT EXISTS threads (
            wa_id TEXT PRIMARY KEY,
            thread_id TEXT NOT NULL,
            last_updated TIMESTAMP,
            created_at TIMESTAMP,
            history_imported BOOLEAN DEFAULT 0 NOT NULL
        )
        """)
        
        # --- Safe Schema Migration ---
        # The following ALTER TABLE statements will fail silently if the columns
        # already exist, making this function safe to run on existing databases.
        try:
            conn.execute("ALTER TABLE threads ADD COLUMN last_updated TIMESTAMP")
        except sqlite3.OperationalError: pass
        try:
            conn.execute("ALTER TABLE threads ADD COLUMN created_at TIMESTAMP")
        except sqlite3.OperationalError: pass
        try:
            # Default to 0 (False), and ensure it's not NULL
            conn.execute("ALTER TABLE threads ADD COLUMN history_imported BOOLEAN DEFAULT 0 NOT NULL")
        except sqlite3.OperationalError: pass
        try:
            # Add conversation_id column for Responses API migration
            conn.execute("ALTER TABLE threads ADD COLUMN conversation_id TEXT DEFAULT NULL")
        except sqlite3.OperationalError: pass
        try:
            # Add last_response_id column for continuing conversations
            conn.execute("ALTER TABLE threads ADD COLUMN last_response_id TEXT DEFAULT NULL")
        except sqlite3.OperationalError: pass
        try:
            # Add last_webhook_timestamp column to track actual incoming webhook messages
            # This is different from last_updated which gets updated on bot responses too
            conn.execute("ALTER TABLE threads ADD COLUMN last_webhook_timestamp TIMESTAMP DEFAULT NULL")
        except sqlite3.OperationalError: pass
        try:
            # Add message_count to track messages for conditional system_instructions sending
            conn.execute("ALTER TABLE threads ADD COLUMN message_count INTEGER DEFAULT 0")
        except sqlite3.OperationalError: pass
        try:
            # Add loaded_modules to track loaded modules (JSON string)
            conn.execute("ALTER TABLE threads ADD COLUMN loaded_modules TEXT DEFAULT NULL")
        except sqlite3.OperationalError: pass

        # --- Data Backfill for Migrated Rows ---
        # Set default values for rows that existed before the migration.
        conn.execute("UPDATE threads SET last_updated = CURRENT_TIMESTAMP WHERE last_updated IS NULL")
        conn.execute("UPDATE threads SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL")
        conn.commit()

from typing import Optional

def get_thread_id(wa_id: str) -> Optional[dict]:
    """Retrieves the full thread record for a given wa_id."""
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row # Return rows as dict-like objects
        cur = conn.execute("SELECT * FROM threads WHERE wa_id = ?", (wa_id,))
        row = cur.fetchone()
        return dict(row) if row else None

def set_thread_id(wa_id: str, thread_id: str):
    """Inserts or updates a thread_id for a wa_id using an UPSERT.
    
    This preserves the original `created_at` and `history_imported` values on updates.
    """
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO threads (wa_id, thread_id, created_at, last_updated, history_imported)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)
            ON CONFLICT(wa_id) DO UPDATE SET
                thread_id = excluded.thread_id,
                last_updated = excluded.last_updated;
        """, (wa_id, thread_id))
        conn.commit()

def set_history_imported(wa_id: str):
    """Marks a user's history as imported."""
    with get_conn() as conn:
        conn.execute("UPDATE threads SET history_imported = 1 WHERE wa_id = ?", (wa_id,))
        conn.commit()

def delete_old_threads(hours: int = 24):
    with get_conn() as conn:
        conn.execute("DELETE FROM threads WHERE last_updated < datetime('now', ?)", (f'-{hours} hours',))
        conn.commit()

def save_conversation_id(identifier: str, conversation_id: str):
    """
    Saves conversation_id for Responses API migration.
    If conversation_id is None, deletes the record to clear corrupted state.
    """
    with get_conn() as conn:
        if conversation_id is None:
            # Clear corrupted conversation state by deleting the record
            conn.execute("DELETE FROM threads WHERE wa_id = ?", (identifier,))
        else:
            conn.execute("""
                INSERT INTO threads (wa_id, thread_id, conversation_id, created_at, last_updated, history_imported)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)
                ON CONFLICT(wa_id) DO UPDATE SET
                    thread_id = excluded.thread_id,
                    conversation_id = excluded.conversation_id,
                    last_updated = excluded.last_updated;
            """, (identifier, conversation_id, conversation_id))
        conn.commit()

def get_conversation_id(identifier: str) -> Optional[str]:
    """Retrieves conversation_id for Responses API migration."""
    with get_conn() as conn:
        cur = conn.execute("SELECT conversation_id FROM threads WHERE wa_id = ?", (identifier,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

def save_response_id(identifier: str, response_id: str):
    """Saves the last response_id for continuing conversations."""
    with get_conn() as conn:
        conn.execute("""UPDATE threads SET last_response_id = ?, last_updated = CURRENT_TIMESTAMP 
                        WHERE wa_id = ?""", (response_id, identifier))
        conn.commit()

def get_last_response_id(identifier: str) -> Optional[str]:
    """Retrieves the last response_id for continuing conversations."""
    with get_conn() as conn:
        cur = conn.execute("SELECT last_response_id FROM threads WHERE wa_id = ?", (identifier,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

def update_last_webhook_timestamp(wa_id: str):
    """Updates the last_webhook_timestamp when a webhook message arrives.
    
    This is ONLY called when an incoming webhook message is received,
    NOT when the bot sends responses. This allows accurate tracking of 
    when customers actually sent messages for missed message detection.
    """
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO threads (wa_id, thread_id, last_webhook_timestamp, created_at, last_updated, history_imported)
            VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 0)
            ON CONFLICT(wa_id) DO UPDATE SET
                last_webhook_timestamp = CURRENT_TIMESTAMP,
                last_updated = CURRENT_TIMESTAMP;
        """, (wa_id, wa_id))
        conn.commit()

def get_last_webhook_timestamp(wa_id: str) -> Optional[str]:
    """Retrieves the last webhook message timestamp for missed message detection."""
    with get_conn() as conn:
        cur = conn.execute("SELECT last_webhook_timestamp FROM threads WHERE wa_id = ?", (wa_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

def get_last_updated_timestamp(wa_id: str) -> Optional[str]:
    """Retrieves the last_updated timestamp (when assistant last responded).
    
    This is used as the cutoff for missed message detection - we want messages
    that arrived AFTER the assistant's last response.
    """
    with get_conn() as conn:
        cur = conn.execute("SELECT last_updated FROM threads WHERE wa_id = ?", (wa_id,))
        row = cur.fetchone()
        return row[0] if row and row[0] else None

def increment_message_count(identifier: str) -> int:
    """Increments and returns the message count for a conversation.
    
    Used to track when to send system_instructions (every 2 messages).
    """
    with get_conn() as conn:
        # Get current count
        cur = conn.execute("SELECT message_count FROM threads WHERE wa_id = ?", (identifier,))
        row = cur.fetchone()
        current_count = row[0] if row and row[0] is not None else 0
        
        # Increment
        new_count = current_count + 1
        conn.execute("""UPDATE threads SET message_count = ? WHERE wa_id = ?""", (new_count, identifier))
        conn.commit()
        return new_count

def get_message_count(identifier: str) -> int:
    """Gets the current message count for a conversation."""
    with get_conn() as conn:
        cur = conn.execute("SELECT message_count FROM threads WHERE wa_id = ?", (identifier,))
        row = cur.fetchone()
        return row[0] if row and row[0] is not None else 0

def reset_message_count(identifier: str):
    """Resets message count to 0 (used after thread rotation)."""
    with get_conn() as conn:
        conn.execute("""UPDATE threads SET message_count = 0 WHERE wa_id = ?""", (identifier,))
        conn.commit()

def save_loaded_modules(identifier: str, modules: list, message_num: int):
    """Saves loaded modules with their message number for tracking.
    
    Stored as JSON: {"modules": ["MODULE_X"], "message_num": 5}
    """
    import json
    with get_conn() as conn:
        data = json.dumps({"modules": modules, "message_num": message_num})
        conn.execute("""UPDATE threads SET loaded_modules = ? WHERE wa_id = ?""", (data, identifier))
        conn.commit()

def get_loaded_modules(identifier: str) -> Optional[dict]:
    """Retrieves loaded modules info.
    
    Returns: {"modules": ["MODULE_X"], "message_num": 5} or None
    """
    import json
    with get_conn() as conn:
        cur = conn.execute("SELECT loaded_modules FROM threads WHERE wa_id = ?", (identifier,))
        row = cur.fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except:
                return None
        return None

def clear_loaded_modules(identifier: str):
    """Clears loaded modules (used after thread rotation)."""
    with get_conn() as conn:
        conn.execute("""UPDATE threads SET loaded_modules = NULL WHERE wa_id = ?""", (identifier,))
        conn.commit()
