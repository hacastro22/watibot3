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
        
        # --- Vertex AI Migration Schema ---
        try:
            conn.execute("ALTER TABLE threads ADD COLUMN session_id TEXT")
        except sqlite3.OperationalError: pass
        try:
            conn.execute("ALTER TABLE threads ADD COLUMN vertex_migrated BOOLEAN DEFAULT 0")
        except sqlite3.OperationalError: pass
        try:
            conn.execute("ALTER TABLE threads ADD COLUMN migration_date TIMESTAMP")
        except sqlite3.OperationalError: pass
        try:
            conn.execute("ALTER TABLE threads ADD COLUMN vertex_context_injected BOOLEAN DEFAULT 0")
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

# Vertex AI Migration Helper Functions
def get_session_id(wa_id: str) -> Optional[str]:
    """Get Vertex session_id for a wa_id, fallback to thread_id if not migrated"""
    thread_info = get_thread_id(wa_id)
    if thread_info:
        return thread_info.get('session_id') or thread_info.get('thread_id')
    return None

def set_session_id(wa_id: str, session_id: str, migrated: bool = False):
    """Set Vertex session_id and optionally mark as migrated"""
    with get_conn() as conn:
        if migrated:
            conn.execute("""
                UPDATE threads 
                SET session_id = ?, vertex_migrated = 1, migration_date = CURRENT_TIMESTAMP 
                WHERE wa_id = ?
            """, (session_id, wa_id))
        else:
            conn.execute("UPDATE threads SET session_id = ? WHERE wa_id = ?", (session_id, wa_id))
        conn.commit()

def mark_vertex_migrated(wa_id: str, session_id: str):
    """Mark a conversation as successfully migrated to Vertex"""
    with get_conn() as conn:
        conn.execute("""
            UPDATE threads 
            SET session_id = ?, vertex_migrated = 1, migration_date = CURRENT_TIMESTAMP 
            WHERE wa_id = ?
        """, (session_id, wa_id))
        conn.commit()

def get_conversations_to_migrate(limit: int = 100) -> list:
    """Get conversations that need migration to Vertex"""
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("""
            SELECT wa_id, thread_id, created_at, history_imported 
            FROM threads 
            WHERE vertex_migrated = 0 OR vertex_migrated IS NULL
            ORDER BY last_updated DESC
            LIMIT ?
        """, (limit,))
        return [dict(row) for row in cursor.fetchall()]

def get_migration_stats():
    """Get migration statistics"""
    with get_conn() as conn:
        cursor = conn.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN vertex_migrated = 1 THEN 1 ELSE 0 END) as migrated,
                SUM(CASE WHEN vertex_migrated = 0 OR vertex_migrated IS NULL THEN 1 ELSE 0 END) as pending
            FROM threads
        """)
        result = cursor.fetchone()
        return {
            'total': result[0],
            'migrated': result[1], 
            'pending': result[2]
        }
