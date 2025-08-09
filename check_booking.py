import sys
sys.path.append('/home/robin/watibot3/app')
from database_client import get_db_connection

def check_booking_25817():
    conn = get_db_connection()
    if not conn:
        print("Failed to connect to database")
        return
    
    try:
        cursor = conn.cursor()
        query = "SELECT reserva, reserverooms, adultos, ninos, checkin, checkout, nameguest FROM reservas WHERE reserva = 25817"
        cursor.execute(query)
        result = cursor.fetchone()
        
        if result:
            print("Booking 25817 Details:")
            print(f"Reserva: {result[0]}")
            print(f"ReserveRooms: {result[1]}")
            print(f"Adults: {result[2]}")
            print(f"Children: {result[3]}")
            print(f"Check-in: {result[4]}")
            print(f"Check-out: {result[5]}")
            print(f"Guest Name: {result[6]}")
        else:
            print("Booking 25817 not found")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    check_booking_25817()
