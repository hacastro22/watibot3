"""
Bank Transfer Synchronization and Validation Tool

This module provides functions to automate bank transfer tasks, meticulously ported
from a working Node.js/Puppeteer script to ensure reliability.
1. Synchronizing transaction statements from BAC with human-like interaction.
2. Validating bank transfer payments from the database.
"""
import os
import csv
import json
import logging
import asyncio
import random
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from playwright.async_api import async_playwright, Page
import mysql.connector
from mysql.connector import Error as MySQLError
from .database_client import get_db_connection
from .wati_client import update_chat_status, send_wati_message
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Constants ---
BAC_URL = 'https://www1.sucursalelectronica.com/redir/showLogin.go'
DOWNLOAD_PATH = '/tmp/bank_transfers'

# Ensure download directory exists
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

# --- Helper Functions for Human-like Interaction ---

async def human_delay(page: Page, min_delay=1000, max_delay=2000):
    """Waits for a random duration to mimic human behavior."""
    delay = random.uniform(min_delay, max_delay)
    await page.wait_for_timeout(delay)

async def human_click(page: Page, selector: str, options: Dict = None):
    """Waits for a selector, moves the mouse realistically, and clicks."""
    if options is None:
        options = {}
    timeout = options.get('timeout', 30000)
    
    logger.info(f"Waiting for selector: {selector}")
    # Use .first() to handle strict mode violation when multiple elements match
    element = page.locator(selector).first
    await element.wait_for(state='visible', timeout=timeout)
    
    box = await element.bounding_box()
    if box:
        await page.mouse.move(
            box['x'] + box['width'] / 2 + random.uniform(-5, 5),
            box['y'] + box['height'] / 2 + random.uniform(-5, 5),
            steps=10
        )
        await human_delay(page, 200, 500)
        await element.click(delay=random.uniform(50, 150))
        logger.info(f"Clicked on selector: {selector}")
    else:
        raise Exception(f"Could not get bounding box for selector: {selector}")
    return element

# --- Main Automation Logic ---

async def login_and_download_csv(page: Page, username: str, password: str) -> str:
    """Logs into BAC, navigates to statements, and downloads the CSV report.
    INFINITE RETRY: Never give up on individual steps to avoid leaving customers hanging.
    """
    logger.info(f"Navigating to {BAC_URL}")
    
    # INFINITE RETRY for page navigation
    nav_retry_count = 0
    while True:
        nav_retry_count += 1
        try:
            logger.info(f"Navigation attempt #{nav_retry_count}")
            await page.goto(BAC_URL, wait_until='networkidle', timeout=60000)
            logger.info("Navigation successful")
            break
        except Exception as e:
            logger.error(f"Navigation attempt #{nav_retry_count} failed: {e}")
            logger.info(f"Waiting 5 seconds before navigation retry #{nav_retry_count + 1}...")
            await asyncio.sleep(5)
    
    await human_delay(page, 2000, 3000)

    # --- Login Process ---
    logger.info('Starting login process...')
    await page.wait_for_selector('#productId', state='visible')
    await page.focus('#productId')
    await page.keyboard.type(username, delay=random.uniform(100, 200))
    await human_delay(page, 500, 1000)

    await page.wait_for_selector('#pass', state='visible')
    await page.focus('#pass')
    await page.keyboard.type(password, delay=random.uniform(100, 200))
    await human_delay(page, 1000, 2000)

    logger.info('Clicking login button...')
    # INFINITE RETRY for login process
    login_retry_count = 0
    while True:
        login_retry_count += 1
        try:
            logger.info(f"Login attempt #{login_retry_count}")
            await human_click(page, '#confirm')
            await page.wait_for_load_state('networkidle', timeout=60000)
            logger.info('Login successful')
            break
        except Exception as e:
            logger.error(f"Login attempt #{login_retry_count} failed: {e}")
            logger.info(f"Waiting 5 seconds before login retry #{login_retry_count + 1}...")
            await asyncio.sleep(5)
    
    await human_delay(page, 3000, 4000)

    # Handle possible "Continuar" button
    try:
        logger.info('Checking for Continuar button...')
        continuar_button_selector = 'div.button-form.button-enabled.button-position-right[name="back"]'
        # Wait for the button to be visible with a short timeout.
        await page.locator(continuar_button_selector).wait_for(state='visible', timeout=10000)
        logger.info('Continuar button found, clicking it.')
        await human_click(page, continuar_button_selector)
        await page.wait_for_load_state('networkidle', timeout=30000)
        logger.info('Clicked Continuar button and waited for navigation.')
        await human_delay(page, 2000, 3000)
    except Exception:
        logger.info('No Continuar button found or it timed out, proceeding.')

    # --- Navigation and Download ---
    logger.info('Accessing account balance...')
    await human_click(page, 'button.bel-btn.bel-btn-tertiary.bel-btn-tertiary-active.bel-btn-tertiary-icon.bel-tooltip-generic')
    await human_delay(page, 3000, 4000)

    logger.info('Selecting month...')
    await human_click(page, '#selectMonthLabel')
    await human_delay(page, 1000, 2000)
    await human_click(page, '#selectMonthList .bel-option[index="1"]') # Select previous month
    await human_delay(page, 2000, 3000)

    # --- Download CSV ---
    # Set download behavior (matching JavaScript approach)
    await page.context.set_extra_http_headers({
        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8'
    })
    
    # Configure download path explicitly (Playwright way)
    cdp = await page.context.new_cdp_session(page)
    await cdp.send('Page.setDownloadBehavior', {
        'behavior': 'allow',
        'downloadPath': DOWNLOAD_PATH
    })
    
    logger.info('Initiating download...')
    await human_click(page, '#button-download')
    await human_delay(page, 1000, 2000)
    
    await human_click(page, '.bel-download-option[value="0"]') # CSV option
    # Critical: Wait 5-6 seconds for file to be fully written (matching JavaScript)
    await human_delay(page, 5000, 6000)
    
    # Use simple filename generation like JavaScript approach
    file_name = f"Transacciones_{datetime.now().strftime('%Y%m%d%H%M%S')}.csv"
    file_path = os.path.join(DOWNLOAD_PATH, file_name)
    
    # Find the most recently downloaded file in the directory
    import glob
    csv_files = glob.glob(os.path.join(DOWNLOAD_PATH, '*.csv'))
    if csv_files:
        # Get the most recent file
        latest_file = max(csv_files, key=os.path.getctime)
        # Rename it to our expected filename
        os.rename(latest_file, file_path)
        logger.info(f"File downloaded and saved to {file_path}")
    else:
        logger.error("No CSV file found after download")
        raise Exception("Download failed - no file found")
    
    return file_path

async def sync_bank_transfers() -> dict:
    """
    Main function to orchestrate the bank transfer sync process.
    INFINITE RETRY: Never give up on syncing bank transfers to avoid leaving customers hanging.
    """
    username = os.getenv('BAC_USERNAME')
    password = os.getenv('BAC_PASSWORD')

    if not username or not password:
        error_msg = "BAC credentials not found in environment variables"
        logger.error(error_msg)
        return {"success": False, "data": {}, "error": error_msg}

    # INFINITE RETRY LOOP - Never give up on bank transfer sync
    retry_count = 0
    while True:
        retry_count += 1
        logger.info(f"Bank transfer sync attempt #{retry_count}")
        
        async with async_playwright() as p:
            browser = None
            try:
                logger.info('Launching browser...')
                browser = await p.chromium.launch(
                    headless=True, 
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-web-security',
                        '--disable-features=IsolateOrigins',
                        '--disable-site-isolation-trials',
                    ]
                )

                context = await browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    extra_http_headers={
                        'Accept-Language': 'es-ES,es;q=0.9,en;q=0.8',
                        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,image/apng,*/*;q=0.8'
                    }
                )
                page = await context.new_page()

                # --- Browser Evasion Setup from JS example ---
                await page.add_init_script("""
                    () => {
                        Object.defineProperty(navigator, 'webdriver', { get: () => false });
                        window.chrome = { runtime: {} };
                        Object.defineProperty(navigator, 'languages', { get: () => ['es-ES', 'es', 'en-US', 'en'] });
                        window.Notification = { permission: 'default' };
                        Object.defineProperty(navigator, 'plugins', { 
                            get: () => [ { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' } ]
                        });
                    }
                """)
                
                client = await context.new_cdp_session(page)
                await client.send('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': DOWNLOAD_PATH})

                file_path = await login_and_download_csv(page, username, password)
                
                # Perform logout within the main try block if login was successful
                try:
                    logger.info('Logging out...')
                    await human_click(page, 'p.bel-typography.bel-typography-p.header-text-color.padding-right-xs', options={'timeout': 10000})
                    await page.wait_for_load_state('networkidle', timeout=30000)
                    logger.info('Logout successful')
                except Exception as e:
                    logger.error(f'Logout failed: {e}')

                # Process data after logging out
                result = await process_csv_and_insert_to_db(file_path)
                
                conclusion = f"Successfully synced {result['rows_inserted']} new bank transfers."
                logger.info(conclusion)
                return {
                    "success": True,
                    "data": {
                        "rows_inserted": result['rows_inserted'],
                        "rows_skipped": result['rows_skipped'],
                        "last_sync_timestamp": datetime.utcnow().isoformat()
                    },
                    "error": ""
                }

            except Exception as e:
                logger.exception(f"Bank transfer sync attempt #{retry_count} failed: {e}")
                if 'page' in locals() and page and not page.is_closed():
                    timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                    screenshot_path = f"error-{timestamp}.png"
                    await page.screenshot(path=screenshot_path, full_page=True)
                    logger.info(f"Error screenshot saved to {screenshot_path}")
                    logger.error(f"Current URL at error: {page.url}")
                
                # Wait 10 seconds before retrying
                logger.info(f"Waiting 10 seconds before retry #{retry_count + 1}...")
                await asyncio.sleep(10)
                
            finally:
                if browser:
                    await browser.close()
                    logger.info('Browser closed')
                
                # Continue the infinite retry loop

async def process_csv_and_insert_to_db(file_path: str) -> dict:
    """Processes the downloaded CSV file and inserts data into the bac table."""
    conn = get_db_connection()
    if not conn:
        raise Exception("Database connection failed")
    cursor = conn.cursor()

    create_table_query = """
    CREATE TABLE IF NOT EXISTS bac (
        id INT AUTO_INCREMENT PRIMARY KEY,
        date VARCHAR(255),
        reference VARCHAR(255),
        code VARCHAR(255),
        description VARCHAR(255),
        debit VARCHAR(255),
        credit VARCHAR(255),
        balance VARCHAR(255),
        used DECIMAL(10, 2) NOT NULL DEFAULT 0.00,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_transaction (date, reference, code, debit, credit)
    )
    """
    cursor.execute(create_table_query)
    conn.commit()
    logger.info("'bac' table is ready.")

    rows_inserted = 0
    rows_skipped = 0
    header_found = False
    headers = ['date', 'reference', 'code', 'description', 'debit', 'credit', 'balance']

    with open(file_path, mode='r', encoding='latin-1') as csvfile:
        reader = csv.reader(csvfile)
        for row_values in reader:
            if not header_found and any(cell and 'Fecha de Transacci' in cell for cell in row_values):
                header_found = True
                logger.info('CSV header found.')
                continue
            
            if header_found:
                if not (row_values and row_values[0]) or any('Resumen de Estado Bancario' in cell for cell in row_values if cell):
                    logger.info('Reached end of relevant data in CSV.')
                    break
                
                if len(row_values) == len(headers):
                    row_data = {h: v.strip() if v else '' for h, v in zip(headers, row_values)}
                    
                    # Exactly match JavaScript duplicate detection logic
                    check_query = """SELECT * FROM bac WHERE date = %s AND reference = %s AND code = %s AND debit = %s AND credit = %s"""
                    cursor.execute(check_query, (row_data['date'], row_data['reference'], row_data['code'], row_data['debit'], row_data['credit']))
                    
                    if cursor.fetchone():
                        rows_skipped += 1
                        continue

                    # Insert new record if no duplicate was found
                    try:
                        insert_query = """INSERT INTO bac (date, reference, code, description, debit, credit, balance, used) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"""
                        cursor.execute(insert_query, (
                            row_data['date'], 
                            row_data['reference'], 
                            row_data['code'], 
                            row_data['description'], 
                            row_data['debit'], 
                            row_data['credit'], 
                            row_data['balance'],
                            0.00
                        ))
                        rows_inserted += 1
                    except Exception as e:
                        logger.error(f"Error inserting row: {row_data}. Error: {e}")
                        rows_skipped += 1

    conn.commit()
    cursor.close()
    conn.close()

    logger.info(f"Rows inserted: {rows_inserted}, Rows skipped: {rows_skipped}")
    return {"rows_inserted": rows_inserted, "rows_skipped": rows_skipped}

def validate_bank_transfer(slip_date: str, slip_amount: float, booking_amount: float) -> dict:
    """
    Validates a bank transfer by finding a deposit matching the slip details
    and checking if it has enough balance to cover the booking amount.
    
    IMPORTANT: This function only validates - it does NOT update the database.
    Use reserve_bank_transfer() to actually reserve the amount after booking succeeds.

    Args:
        slip_date: The date on the payment slip ('YYYY-MM-DD').
        slip_amount: The total amount on the payment slip.
        booking_amount: The amount of the booking to validate.

    Returns:
        A dictionary with the validation result including transfer_id for reservation.
    """
    logger.info(f"Attempting to validate booking of {booking_amount} using slip for {slip_amount} on {slip_date}")
    db_connection = None
    try:
        db_connection = get_db_connection()
        cursor = db_connection.cursor(dictionary=True)

        # Find an available transfer matching the slip amount and date.
        query = """
            SELECT id, credit, used FROM bac 
            WHERE credit = %s AND STR_TO_DATE(date, '%d/%m/%Y') = %s AND used < credit
            LIMIT 1
        """
        cursor.execute(query, (slip_amount, slip_date))
        transfer_record = cursor.fetchone()

        if not transfer_record:
            logger.warning(f"Validation failed: No unused transfer found for amount {slip_amount} on {slip_date}.")
            return {"success": False, "message": f"No se encontró una transferencia bancaria disponible por {slip_amount:.2f} en la fecha {slip_date}."}

        record_id = transfer_record['id']
        credit_amount = float(transfer_record['credit'])
        used_amount = float(transfer_record['used'])
        available_balance = credit_amount - used_amount

        logger.info(f"Found transfer ID {record_id}: Credit={credit_amount}, Used={used_amount}, Available={available_balance}")

        # Check if the requested booking amount is valid and available
        if booking_amount <= 0:
            return {"success": False, "message": "El monto a validar debe ser mayor que cero."}

        if available_balance < booking_amount:
            logger.warning(f"Validation failed for transfer ID {record_id}: Insufficient balance. Available: {available_balance}, Requested: {booking_amount}")
            return {"success": False, "message": f"Fondos insuficientes. Saldo disponible: {available_balance:.2f}, se requiere: {booking_amount:.2f}"}

        # Validation successful - return transfer info without updating database
        logger.info(f"Successfully validated transfer ID {record_id}. Available balance: {available_balance}")
        return {
            "success": True, 
            "message": f"Pago de {booking_amount:.2f} validado exitosamente usando la transferencia del {slip_date}.",
            "transfer_id": record_id,
            "available_balance": available_balance
        }

    except MySQLError as e:
        logger.exception(f"Database error during bank transfer validation: {e}")
        return {"success": False, "message": "Error de base de datos durante la validación."}
    except Exception as e:
        logger.exception(f"An unexpected error occurred during bank transfer validation: {e}")
        return {"success": False, "message": "Ocurrió un error inesperado durante la validación."}
    finally:
        if db_connection and db_connection.is_connected():
            try:
                cursor.close()
            except:
                pass
            try:
                db_connection.close()
            except:
                pass


def reserve_bank_transfer(transfer_id: int, booking_amount: float) -> dict:
    """
    Reserves a bank transfer amount by updating the 'used' column.
    This should only be called after successful booking validation and just before the booking HTTP call.

    Args:
        transfer_id: The ID of the bank transfer record to reserve
        booking_amount: The amount to reserve

    Returns:
        A dictionary with the reservation result
    """
    logger.info(f"Attempting to reserve {booking_amount} from transfer ID {transfer_id}")
    db_connection = None
    try:
        db_connection = get_db_connection()
        cursor = db_connection.cursor(dictionary=True)

        # First, verify the transfer still has sufficient balance
        query = "SELECT id, credit, used FROM bac WHERE id = %s"
        cursor.execute(query, (transfer_id,))
        transfer_record = cursor.fetchone()

        if not transfer_record:
            logger.warning(f"Transfer ID {transfer_id} not found for reservation")
            return {"success": False, "message": "Transfer record not found"}

        credit_amount = float(transfer_record['credit'])
        used_amount = float(transfer_record['used'])
        available_balance = credit_amount - used_amount

        if available_balance < booking_amount:
            logger.warning(f"Insufficient balance for transfer ID {transfer_id}. Available: {available_balance}, Requested: {booking_amount}")
            return {"success": False, "message": f"Fondos insuficientes. Saldo disponible: {available_balance:.2f}, se requiere: {booking_amount:.2f}"}

        # Update the used amount with the booking_amount
        update_query = "UPDATE bac SET used = used + %s WHERE id = %s"
        cursor.execute(update_query, (booking_amount, transfer_id))
        db_connection.commit()

        logger.info(f"Successfully reserved {booking_amount} from transfer ID {transfer_id}. New used amount: {used_amount + booking_amount}")
        return {"success": True, "message": f"Cantidad {booking_amount:.2f} reservada exitosamente"}

    except Error as e:
        logger.exception(f"Database error during bank transfer reservation: {e}")
        return {"success": False, "message": "Error de base de datos durante la reserva"}
    except Exception as e:
        logger.exception(f"An unexpected error occurred during bank transfer reservation: {e}")
        return {"success": False, "message": "Ocurrió un error inesperado durante la reserva"}
    finally:
        if db_connection and db_connection.is_connected():
            try:
                cursor.close()
            except:
                pass
            try:
                db_connection.close()
            except:
                pass
