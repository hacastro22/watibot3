import mysql.connector
import logging
from . import config

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_db_connection():
    """Establishes a connection to the MySQL database."""
    try:
        conn = mysql.connector.connect(
            host=config.DB_HOST,
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            database=config.DB_NAME
        )
        return conn
    except mysql.connector.Error as err:
        logger.error(f"Error connecting to database: {err}")
        return None


async def check_room_availability(check_in_date: str, check_out_date: str) -> dict:
    """Checks room availability for a given date range."""
    conn = get_db_connection()
    if not conn:
        return {"error": "Database connection failed"}

    try:
        cursor = conn.cursor(dictionary=True)
        
        # Set session variables for check-in and check-out dates
        cursor.execute("SET @check_in = %s;", (check_in_date,))
        cursor.execute("SET @check_out = %s;", (check_out_date,))

        query = """
            WITH
            all_rooms AS (
                SELECT '1'  AS room_number UNION ALL SELECT '2'  UNION ALL SELECT '3'  UNION ALL SELECT '4'  UNION ALL SELECT '5'  UNION ALL
                SELECT '6'  UNION ALL SELECT '7'  UNION ALL SELECT '8'  UNION ALL SELECT '9'  UNION ALL SELECT '10' UNION ALL
                SELECT '11' UNION ALL SELECT '12' UNION ALL SELECT '13' UNION ALL SELECT '14' UNION ALL SELECT '15' UNION ALL
                SELECT '16' UNION ALL SELECT '17' UNION ALL SELECT '18' UNION ALL SELECT '19' UNION ALL SELECT '20' UNION ALL
                SELECT '21' UNION ALL SELECT '22' UNION ALL SELECT '23' UNION ALL SELECT '24' UNION ALL SELECT '25' UNION ALL
                SELECT '26' UNION ALL SELECT '27' UNION ALL SELECT '28' UNION ALL SELECT '29' UNION ALL SELECT '30' UNION ALL
                SELECT '31' UNION ALL SELECT '32' UNION ALL SELECT '33' UNION ALL SELECT '34' UNION ALL SELECT '35' UNION ALL
                SELECT '36' UNION ALL SELECT '37' UNION ALL SELECT '38' UNION ALL SELECT '39' UNION ALL SELECT '40' UNION ALL
                SELECT '41' UNION ALL SELECT '42' UNION ALL SELECT '43' UNION ALL SELECT '44' UNION ALL SELECT '45' UNION ALL
                SELECT '46' UNION ALL SELECT '47' UNION ALL SELECT '48' UNION ALL SELECT '49' UNION ALL SELECT '50' UNION ALL
                SELECT '51' UNION ALL SELECT '52' UNION ALL SELECT '53' UNION ALL SELECT '54' UNION ALL SELECT '55' UNION ALL
                SELECT '56' UNION ALL SELECT '57' UNION ALL SELECT '58' UNION ALL SELECT '59' UNION ALL
                SELECT '1A' UNION ALL SELECT '2A'  UNION ALL SELECT '3A'  UNION ALL SELECT '4A'  UNION ALL SELECT '5A'  UNION ALL
                SELECT '6A' UNION ALL SELECT '7A'  UNION ALL SELECT '8A'  UNION ALL SELECT '9A'  UNION ALL SELECT '10A' UNION ALL
                SELECT '11A' UNION ALL SELECT '12A' UNION ALL SELECT '13A' UNION ALL SELECT '14A' UNION ALL
                SELECT 'Pasadía'
            ),
            -- ------------------------------------------------------------------
            --  All rooms whose bookings overlap ANY day in the requested period
            -- ------------------------------------------------------------------
            booked_rooms_in_range AS (
                /* -------- user_books -------- */
                SELECT DISTINCT
                    TRIM(
                        CASE
                            WHEN reserverooms NOT LIKE '%-%' THEN reserverooms
                            ELSE SUBSTRING_INDEX(
                                     SUBSTRING_INDEX(SUBSTRING(reserverooms, LOCATE('-', reserverooms) + 1), ',', numbers.n),
                                     ',', -1)
                        END
                    ) AS room_number
                FROM user_books
                CROSS JOIN (
                    SELECT 1 n UNION ALL SELECT 2  UNION ALL SELECT 3  UNION ALL SELECT 4  UNION ALL
                    SELECT 5  UNION ALL SELECT 6  UNION ALL SELECT 7  UNION ALL SELECT 8  UNION ALL
                    SELECT 9  UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL
                    SELECT 13 UNION ALL SELECT 14 UNION ALL SELECT 15 UNION ALL SELECT 16 UNION ALL
                    SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19 UNION ALL SELECT 20
                ) numbers
                WHERE cancel_flag != 'yes'
                  AND checkIn  IS NOT NULL AND checkIn  != ''
                  AND checkOut IS NOT NULL AND checkOut != ''
                  AND STR_TO_DATE(checkIn , '%m/%d/%Y') < @check_out
                  AND STR_TO_DATE(checkOut, '%m/%d/%Y') > @check_in
                  AND (
                        (reserverooms NOT LIKE '%-%' AND numbers.n = 1) OR
                        (reserverooms LIKE  '%-%' AND
                         numbers.n <= (LENGTH(SUBSTRING(reserverooms, LOCATE('-', reserverooms) + 1)) -
                                       LENGTH(REPLACE(SUBSTRING(reserverooms, LOCATE('-', reserverooms) + 1), ',', '')) + 1))
                      )

                UNION

                /* -------- member_books -------- */
                SELECT DISTINCT
                    TRIM(
                        CASE
                            WHEN room_number NOT LIKE '%-%' THEN room_number
                            ELSE SUBSTRING_INDEX(
                                     SUBSTRING_INDEX(SUBSTRING(room_number, LOCATE('-', room_number) + 1), ',', numbers.n),
                                     ',', -1)
                        END
                    ) AS room_number
                FROM member_books
                CROSS JOIN (
                    SELECT 1 n UNION ALL SELECT 2  UNION ALL SELECT 3  UNION ALL SELECT 4  UNION ALL
                    SELECT 5  UNION ALL SELECT 6  UNION ALL SELECT 7  UNION ALL SELECT 8  UNION ALL
                    SELECT 9  UNION ALL SELECT 10 UNION ALL SELECT 11 UNION ALL SELECT 12 UNION ALL
                    SELECT 13 UNION ALL SELECT 14 UNION ALL SELECT 15 UNION ALL SELECT 16 UNION ALL
                    SELECT 17 UNION ALL SELECT 18 UNION ALL SELECT 19 UNION ALL SELECT 20
                ) numbers
                WHERE cancel_flag != 'yes'
                  AND checkIn  IS NOT NULL AND checkIn  != ''
                  AND checkOut IS NOT NULL AND checkOut != ''
                  AND STR_TO_DATE(checkIn , '%m/%d/%Y') < @check_out
                  AND STR_TO_DATE(checkOut, '%m/%d/%Y') > @check_in
                  AND (
                        (room_number NOT LIKE '%-%' AND numbers.n = 1) OR
                        (room_number LIKE  '%-%' AND
                         numbers.n <= (LENGTH(SUBSTRING(room_number, LOCATE('-', room_number) + 1)) -
                                       LENGTH(REPLACE(SUBSTRING(room_number, LOCATE('-', room_number) + 1), ',', '')) + 1))
                      )
            ),
            -- ------------------------------------------------------------------
            --  Rooms still free in that period, classified by bungalow type
            -- ------------------------------------------------------------------
            available_rooms_categorized AS (
                SELECT
                    ar.room_number,
                    CASE
                        WHEN ar.room_number REGEXP '^[1-9]$|^1[0-6]$'  THEN 'bungalow_familiar'
                        WHEN ar.room_number REGEXP '^(1[8-9]|[2-5][0-9])$' THEN 'bungalow_junior'
                        WHEN ar.room_number REGEXP '^[1-9]A$|^1[0-4]A$'   THEN 'habitacion'
                        ELSE 'other'
                    END AS bungalow_type
                FROM all_rooms ar
                WHERE ar.room_number NOT IN (SELECT room_number FROM booked_rooms_in_range)
                  AND ar.room_number <> 'Pasadía'
            )
            -- ------------------------------------------------------------------
            --  Final availability flags per bungalow type
            -- ------------------------------------------------------------------
            SELECT
                bt.bungalow_type,
                CASE
                    WHEN COUNT(arc.room_number) > 0 THEN 1
                    ELSE 0
                END AS bungalow_availability
            FROM (
                SELECT 'bungalow_familiar' AS bungalow_type
                UNION ALL
                SELECT 'bungalow_junior'
                UNION ALL
                SELECT 'habitacion'
            ) bt
            LEFT JOIN available_rooms_categorized arc
                   ON bt.bungalow_type = arc.bungalow_type
            GROUP BY bt.bungalow_type
            ORDER BY
                CASE
                    WHEN bt.bungalow_type = 'bungalow_familiar' THEN 1
                    WHEN bt.bungalow_type = 'bungalow_junior'   THEN 2
                    WHEN bt.bungalow_type = 'habitacion'        THEN 3
                END;
        """
        
        # The query is executed in multiple statements, so we use cursor.execute with multi=True
        results = {}
        for result in cursor.execute(query, multi=True):
            if result.with_rows:
                rows = result.fetchall()
                for row in rows:
                    results[row['bungalow_type']] = 'Available' if row['bungalow_availability'] == 1 else 'Not Available'

        logger.info(f"Availability for {check_in_date} to {check_out_date}: {results}")
        return results if results else {"error": "Could not retrieve availability."}

    except mysql.connector.Error as err:
        logger.error(f"Database query failed for availability check: {err}")
        return {"error": "A database error occurred during availability check."}
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()
import logging
from . import config
from datetime import datetime

logger = logging.getLogger(__name__)

def get_price_for_date(date_str: str) -> dict:
    """Fetches the price for different packages from the database for a given date."""
    cnx = None
    cursor = None
    try:
        # Validate input format from the assistant
        datetime.strptime(date_str, '%Y-%m-%d')

        cnx = mysql.connector.connect(
            user=config.DB_USER,
            password=config.DB_PASSWORD,
            host=config.DB_HOST,
            database=config.DB_NAME
        )
        cursor = cnx.cursor(dictionary=True)

        # Per the user's explicit and correct instruction, we must use STR_TO_DATE
        # with the '%m/%d/%Y' format on the database column and compare it to the
        # assistant's 'YYYY-MM-DD' date string.
        query = """
            SELECT lh_adulto, lh_nino, pa_adulto, pa_nino, es_adulto, es_nino
            FROM tarifarios
            WHERE STR_TO_DATE(date, '%m/%d/%Y') = %s
        """
        params = (date_str,)
        logger.info(f"Executing query: {query.strip()} with params: {params}")

        cursor.execute(query, params)
        result = cursor.fetchone()
        logger.info(f"Raw query result for {date_str}: {result}")

        if result:
            logger.info(f"Prices for {date_str}: {result}")
            return result
        else:
            logger.warning(f"No prices found for date: {date_str}")
            return {"error": f"No prices found for {date_str}."}

    except ValueError:
        logger.error(f"Invalid date format provided: {date_str}. Expected YYYY-MM-DD.")
        return {"error": "Invalid date format. Please use YYYY-MM-DD."}
    except mysql.connector.Error as err:
        logger.exception(f"Database error while fetching price for date {date_str}")
        return {"error": f"Database error: {err}"}
    except Exception as e:
        logger.exception(f"An unexpected error occurred while fetching price for date {date_str}")
        return {"error": f"An unexpected error occurred: {e}"}
    finally:
        if cursor:
            cursor.close()
        if cnx and cnx.is_connected():
            cnx.close()
