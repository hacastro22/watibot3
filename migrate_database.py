#!/usr/bin/env python3
"""
Non-breaking database migration for Vertex AI support
Adds columns to existing tables without affecting current operations
"""

import sqlite3
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def migrate_database():
    """
    Add Vertex AI support columns to existing database
    All operations are non-breaking ADD COLUMN statements
    """
    
    db_path = Path(__file__).parent / "thread_store.db"
    
    if not db_path.exists():
        logger.error(f"Database not found: {db_path}")
        return False
    
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        
        # Check existing schema
        cursor.execute("PRAGMA table_info(threads)")
        existing_columns = [row[1] for row in cursor.fetchall()]
        logger.info(f"Existing columns: {existing_columns}")
        
        migrations = [
            ("session_id", "ALTER TABLE threads ADD COLUMN session_id TEXT"),
            ("vertex_migrated", "ALTER TABLE threads ADD COLUMN vertex_migrated INTEGER DEFAULT 0"),
            ("migration_date", "ALTER TABLE threads ADD COLUMN migration_date TEXT"),
            ("vertex_context_injected", "ALTER TABLE threads ADD COLUMN vertex_context_injected INTEGER DEFAULT 0")
        ]
        
        for column_name, sql in migrations:
            if column_name not in existing_columns:
                logger.info(f"Adding column: {column_name}")
                cursor.execute(sql)
                logger.info(f"✅ Added column: {column_name}")
            else:
                logger.info(f"⏭️  Column already exists: {column_name}")
        
        conn.commit()
        
        # Verify schema after migration
        cursor.execute("PRAGMA table_info(threads)")
        new_columns = [row[1] for row in cursor.fetchall()]
        logger.info(f"Schema after migration: {new_columns}")
        
        conn.close()
        logger.info("🎉 Database migration completed successfully")
        return True
        
    except Exception as e:
        logger.exception(f"❌ Database migration failed: {e}")
        return False

if __name__ == "__main__":
    success = migrate_database()
    exit(0 if success else 1)
