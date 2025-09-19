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
    """Saves conversation_id for Responses API migration."""
    with get_conn() as conn:
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
