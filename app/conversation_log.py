"""
Conversation logging for ManyChat users (Facebook & Instagram)
Stores message history locally for context injection during thread rotation
"""
import sqlite3
import os
from datetime import datetime
from typing import Optional, List, Dict
from contextlib import contextmanager

DB_PATH = os.environ.get("CONVERSATION_LOG_DB_PATH", "app/conversation_log.db")

@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()

def init_conversation_log_db():
    """Initialize the conversation log database"""
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_identifier TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            channel TEXT
        )
        """)
        
        # Create index for faster lookups
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_user_timestamp ON conversation_log(user_identifier, timestamp DESC)")
        except sqlite3.OperationalError:
            pass
        
        conn.commit()

def log_message(user_identifier: str, role: str, content: str, channel: str = None):
    """
    Log a conversation message for ManyChat users
    
    Args:
        user_identifier: Subscriber ID (e.g., "2555928227777606")
        role: Either "user" (customer) or "assistant" (bot)
        content: Message text content
        channel: "facebook" or "instagram"
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversation_log (user_identifier, role, content, channel, timestamp) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)",
            (user_identifier, role, content, channel)
        )
        conn.commit()

def get_recent_messages(user_identifier: str, limit: int = 100) -> List[Dict]:
    """
    Retrieve recent conversation messages for a user
    
    Args:
        user_identifier: Subscriber ID
        limit: Maximum number of messages to retrieve
        
    Returns:
        List of message dictionaries with keys: role, content, timestamp, channel
    """
    with get_conn() as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute(
            """SELECT role, content, timestamp, channel 
               FROM conversation_log 
               WHERE user_identifier = ? 
               ORDER BY timestamp DESC 
               LIMIT ?""",
            (user_identifier, limit)
        )
        messages = [dict(row) for row in cursor.fetchall()]
        # Reverse to get chronological order (oldest first)
        return list(reversed(messages))

def cleanup_old_messages(days: int = 30):
    """Delete messages older than specified days to keep database manageable"""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM conversation_log WHERE timestamp < datetime('now', ?)",
            (f'-{days} days',)
        )
        conn.commit()

# Initialize database on module import
init_conversation_log_db()
