import sqlite3
import os

db_path = 'thread_store.db'
wa_id_to_reset = '50376973593'

if not os.path.exists(db_path):
    print(f"Error: Database file not found at {db_path}")
else:
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        print(f"Attempting to reset history_imported flag for wa_id: {wa_id_to_reset}")
        
        cursor.execute("UPDATE threads SET history_imported = 0 WHERE wa_id = ?", (wa_id_to_reset,))
        
        conn.commit()
        
        if cursor.rowcount > 0:
            print(f"Successfully reset history_imported flag for {cursor.rowcount} record(s).")
        else:
            print(f"No record found for wa_id {wa_id_to_reset}. Nothing to update.")
            
    except sqlite3.Error as e:
        print(f"Database error: {e}")
    finally:
        if conn:
            conn.close()
            print("Database connection closed.")
