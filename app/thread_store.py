import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("THREAD_DB_PATH", "thread_store.db")

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
