"""
CompraClick Payment Link Generation and Validation Tool

This module provides functions to automate CompraClick tasks:
1. Creating payment links.
2. Synchronizing transaction reports.
3. Validating payments from the database.
"""
import os
import time
import logging
import pandas as pd
from typing import Dict, Optional, Any
from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PlaywrightTimeoutError
from . import config
from .database_client import get_db_connection
from .wati_client import send_wati_message
from .compraclick_retry import start_compraclick_retry_process
from datetime import datetime

# Configure logging
logger = logging.getLogger(__name__)

# Constants for the CompraClick workflow
COMPRACLICK_URL = 'https://miposafiliados.credomatic.com'
DOWNLOAD_PATH = '/tmp/compraclick'

# Ensure download directory exists
if not os.path.exists(DOWNLOAD_PATH):
    os.makedirs(DOWNLOAD_PATH)

# --- Playwright Browser Auto-Recovery ---
def check_playwright_browsers():
    """Check if Playwright browsers are installed."""
    from pathlib import Path
    cache_dir = Path.home() / '.cache' / 'ms-playwright'
    chromium_dirs = list(cache_dir.glob('chromium-*')) if cache_dir.exists() else []
    return len(chromium_dirs) > 0

def reinstall_playwright_browsers():
    """Automatically reinstall Playwright browsers if missing."""
    import subprocess
    from pathlib import Path
    try:
        logger.warning("[PLAYWRIGHT_RECOVERY] Browsers missing! Attempting auto-install...")
        # Get the venv python path
        venv_python = Path(__file__).parent.parent / 'venv' / 'bin' / 'python'
        
        if venv_python.exists():
            result = subprocess.run(
                [str(venv_python), '-m', 'playwright', 'install', 'chromium'],
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout
            )
            
            if result.returncode == 0:
                logger.info("[PLAYWRIGHT_RECOVERY] Successfully reinstalled Chromium browser!")
                return True
            else:
                logger.error(f"[PLAYWRIGHT_RECOVERY] Failed to install: {result.stderr}")
                return False
        else:
            logger.error(f"[PLAYWRIGHT_RECOVERY] venv python not found at {venv_python}")
            return False
            
    except Exception as e:
        logger.error(f"[PLAYWRIGHT_RECOVERY] Auto-install failed: {e}")
        return False

async def sync_compraclick_payments() -> dict:
    """
    Synchronizes CompraClick payments by downloading the report and updating the database.
    """
    chain_of_thought = {"steps": [], "conclusion": ""}
    email = os.getenv('COMPRACLICK_EMAIL')
    password = os.getenv('COMPRACLICK_PASSWORD')

    if not email or not password:
        error_msg = "CompraClick credentials not found in environment variables"
        logger.error(error_msg)
        return {"success": False, "data": {}, "error": error_msg}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox', '--disable-setuid-sandbox'])
            page = await browser.new_page()
            try:
                # Step 1: Login and Download
                chain_of_thought["steps"].append({"step": 1, "action": "Login and Download Report", "reasoning": "Access CompraClick portal to get latest transactions", "result": "In progress..."})
                file_path = await login_and_download_report(page, email, password)
                chain_of_thought["steps"][-1]["result"] = f"File downloaded to {file_path}"

                # Step 2: Process XLS and Insert to DB
                chain_of_thought["steps"].append({"step": 2, "action": "Process XLS and Update DB", "reasoning": "Parse the report and sync to local database", "result": "In progress..."})
                result = await process_xls_and_insert_to_db(file_path)
                chain_of_thought["steps"][-1]["result"] = f"Inserted: {result.get('inserted', 0)}, Skipped: {result.get('skipped', 0)}"

                conclusion = f"Successfully synced {result.get('inserted', 0)} new CompraClick transactions."
                chain_of_thought["conclusion"] = conclusion
                logger.info(conclusion)

                return {
                    "success": True,
                    "chain_of_thought": chain_of_thought,
                    "data": {
                        "rows_inserted": result.get('inserted', 0),
                        "rows_skipped": result.get('skipped', 0),
                        "last_sync_timestamp": datetime.utcnow().isoformat()
                    },
                    "error": ""
                }

            except Exception as e:
                logger.exception("Error during CompraClick sync process")
                chain_of_thought["steps"].append({"step": "error", "action": "Sync Failed", "reasoning": str(e), "result": "Failure"})
                return {"success": False, "chain_of_thought": chain_of_thought, "data": {}, "error": f"An error occurred: {e}"}
            finally:
                await logout(page)
                await browser.close()
    except Exception as e:
        error_message = str(e)
        logger.exception(f"Failed to initialize browser for CompraClick sync: {error_message}")
        
        # Check if it's a Playwright browser missing error
        if "Executable doesn't exist" in error_message or "playwright" in error_message.lower():
            logger.warning("[PLAYWRIGHT_ERROR] Detected missing browser error in CompraClick sync!")
            
            # Attempt auto-recovery
            if reinstall_playwright_browsers():
                logger.info("[PLAYWRIGHT_RECOVERY] Browsers reinstalled! Please retry the sync.")
                return {
                    "success": False, 
                    "data": {}, 
                    "error": "Browser was missing but has been auto-installed. Please retry the sync."
                }
            else:
                logger.error("[PLAYWRIGHT_RECOVERY] Auto-recovery failed!")
                return {
                    "success": False, 
                    "data": {}, 
                    "error": "Browser missing and auto-recovery failed. Please contact support."
                }
        
        return {"success": False, "data": {}, "error": f"Browser initialization failed: {error_message}"}

async def login_and_download_report(page: Page, email: str, password: str) -> str:
    """Logs in, navigates to sales, and downloads the transaction report."""
    await page.goto(COMPRACLICK_URL, timeout=120000)
    await page.type('input[placeholder="E-mail"]', email)
    await page.type('input[placeholder="Contraseña"]', password)
    await page.click('button[type="submit"].btn-secondary')
    await page.wait_for_load_state('networkidle', timeout=120000)
    logger.info("Login successful.")

    await page.locator('//span[text()="Ventas"]').click()
    logger.info("Clicked Ventas button.")

    await page.wait_for_selector("//button[contains(text(), 'Exportar')]", timeout=60000)
    await page.locator("//button[contains(text(), 'Exportar')]").click()
    logger.info("Clicked Exportar button.")

    async with page.expect_download() as download_info:
        await page.locator("//button[contains(text(), 'Generar reporte')]").click()
        logger.info("Clicked Generar Reporte button.")
    
    download = await download_info.value
    download_file_path = os.path.join(DOWNLOAD_PATH, 'compraclick.xls')
    await download.save_as(download_file_path)
    logger.info(f"File downloaded and saved to {download_file_path}")
    return download_file_path

async def process_xls_and_insert_to_db(file_path: str) -> dict:
    """Processes the downloaded XLS file and inserts data into the compraclick table."""
    logger.info(f"Starting process with file: {file_path}")
    
    inserted_count = 0
    skipped_count = 0
    error_count = 0
    
    conn = None
    cursor = None
    
    try:
        # Read Excel file without header assumption (read as raw data)
        df = pd.read_excel(file_path, header=None)
        logger.info(f"Found {len(df)} rows in the file")
        
        # Skip first row which might be header or empty (matching JS logic)
        data_rows = df.iloc[1:].values.tolist()
        logger.info(f"Processing {len(data_rows)} data rows (skipped first row)")
        
        # Define column mappings from Excel file to database schema (matching JS reference)
        # Based on JS column map - these are 0-indexed positions in the Excel file
        column_map = {
            'date': 0,           # Date column (timestamp)
            'sucursal': 2,       # Branch name 
            'usuario': 3,        # User name
            'estado': 4,         # Status
            'cliente': 5,        # Client name
            'documento': 6,      # Document number
            'email': 7,          # Email
            'direccion': 8,      # Address
            'marcatarjeta': 9,   # Card brand (Visa, Mastercard)
            'tarjeta': 10,       # Card number
            'importe': 13,       # Amount
            'autorizacion': 16,  # Authorization number
            'descripcion': 21    # Description
        }
        
        logger.info(f"Using mapped columns to database schema: {column_map}")
        
        # Function to clean decimal values (matching JS cleanDecimalValue function)
        def clean_decimal_value(value):
            if value is None or pd.isna(value):
                return None
            
            # Convert to string if it's not already
            string_value = str(value).strip()
            
            # If it's empty, return None
            if string_value == '':
                return None
            
            # Remove commas (thousands separators) and keep only numbers, dots, and minus signs
            cleaned_value = string_value.replace(',', '')
            
            # Validate that the result is a valid number
            try:
                numeric_value = float(cleaned_value)
                return numeric_value
            except ValueError:
                logger.warning(f"Could not parse decimal value '{string_value}' - returning None")
                return None
        
        # Collect valid rows to be processed
        valid_rows = []
        
        # First pass: collect valid rows (matching JS logic)
        for i, row in enumerate(data_rows):
            if not row or len(row) < max(column_map.values()) + 1:
                logger.info(f"Skipping row {i + 1}: insufficient columns")
                continue
            
            # Extract values using the column mapping
            try:
                date = row[column_map['date']] if len(row) > column_map['date'] else None
                sucursal = row[column_map['sucursal']] if len(row) > column_map['sucursal'] else ''
                usuario = row[column_map['usuario']] if len(row) > column_map['usuario'] else ''
                estado = row[column_map['estado']] if len(row) > column_map['estado'] else ''
                cliente = row[column_map['cliente']] if len(row) > column_map['cliente'] else ''
                documento = row[column_map['documento']] if len(row) > column_map['documento'] else ''
                email = row[column_map['email']] if len(row) > column_map['email'] else ''
                direccion = row[column_map['direccion']] if len(row) > column_map['direccion'] else ''
                marcatarjeta = row[column_map['marcatarjeta']] if len(row) > column_map['marcatarjeta'] else ''
                tarjeta = row[column_map['tarjeta']] if len(row) > column_map['tarjeta'] else ''
                raw_importe = row[column_map['importe']] if len(row) > column_map['importe'] else None
                autorizacion = row[column_map['autorizacion']] if len(row) > column_map['autorizacion'] else ''
                descripcion = row[column_map['descripcion']] if len(row) > column_map['descripcion'] else ''
                
                # Clean the importe value to remove comma thousands separators (matching JS logic)
                importe = clean_decimal_value(raw_importe)
                
                # Skip rows with missing essential data (matching JS logic)
                if not date or importe is None:
                    logger.info(f"Skipping row {i + 1}: missing date or importe")
                    continue
                
                # Convert date to datetime if it's not already
                if not isinstance(date, pd.Timestamp):
                    try:
                        date = pd.to_datetime(date)
                    except:
                        logger.warning(f"Could not parse date '{date}' in row {i + 1}")
                        continue
                
                # Add to valid rows for processing
                # CRITICAL: Ensure all values are properly cleaned for NOT NULL database columns
                def safe_string(value):
                    """Convert None or NaN values to empty string for NOT NULL columns"""
                    if value is None or pd.isna(value):
                        return ''
                    return str(value).strip()
                
                valid_rows.append({
                    'row_index': i + 1,
                    'date': date,
                    'sucursal': safe_string(sucursal),
                    'usuario': safe_string(usuario),
                    'estado': safe_string(estado),
                    'cliente': safe_string(cliente),
                    'documento': safe_string(documento),
                    'email': safe_string(email),
                    'direccion': safe_string(direccion),  # This was causing the null constraint error
                    'marcatarjeta': safe_string(marcatarjeta),
                    'tarjeta': safe_string(tarjeta),
                    'importe': importe,
                    'autorizacion': safe_string(autorizacion),
                    'descripcion': safe_string(descripcion)
                })
                
            except Exception as e:
                logger.error(f"Error processing row {i + 1}: {e}")
                continue
        
        # Sort rows by date in ascending order (CRITICAL - matching JS logic)
        valid_rows.sort(key=lambda x: x['date'])
        
        logger.info(f"Processing {len(valid_rows)} valid rows in date order")
        
        # Connect to database
        conn = get_db_connection()
        if not conn:
            return {"success": False, "inserted": 0, "skipped": 0, "message": "Database connection failed."}
        
        cursor = conn.cursor()

        # --- Schema Migration: Ensure 'used' column exists ---
        try:
            cursor.execute("ALTER TABLE compraclick ADD COLUMN used DECIMAL(10, 2) NOT NULL DEFAULT 0.00")
            conn.commit()
            logger.info("Added 'used' column to 'compraclick' table.")
        except Exception as e:
            if "Duplicate column name" in str(e):
                logger.info("'used' column already exists.")
                conn.rollback() # Rollback the failed ALTER TABLE statement
            else:
                raise e # Re-raise other errors
        
        # Prepare insertion query
        insert_query = """
            INSERT INTO compraclick 
            (date, sucursal, usuario, estado, cliente, documento, email, direccion, 
             marcatarjeta, tarjeta, importe, autorizacion, descripcion, used)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        # Most precise duplicate detection - use importe and autorizacion only
        check_duplicate_query = """
            SELECT COUNT(*) as count FROM compraclick 
            WHERE importe = %s AND autorizacion = %s
        """
        
        # Process sorted rows (EXACTLY matching JS logic with proper MySQL date conversion)
        for row in valid_rows:
            try:
                # Convert date to MySQL format (needed for Python, JS handles this automatically)
                mysql_date = row['date'] if isinstance(row['date'], str) else row['date'].strftime('%Y-%m-%d %H:%M:%S')
                
                # Check for duplicates (most precise logic)
                cursor.execute(check_duplicate_query, (
                    row['importe'],
                    row['autorizacion']
                ))
                
                duplicate_result = cursor.fetchone()
                
                # Skip if duplicate found (EXACT JS logic)
                if duplicate_result and duplicate_result[0] > 0:
                    logger.info(f"Skipping row {row['row_index']}: duplicate entry detected ({row['cliente']}, {row['date']})")
                    skipped_count += 1
                    continue
                
                # Insert into database (EXACT JS logic with MySQL date conversion)
                cursor.execute(insert_query, (
                    mysql_date,  # MySQL-compatible date format
                    row['sucursal'],
                    row['usuario'],
                    row['estado'],
                    row['cliente'],
                    row['documento'],
                    row['email'],
                    row['direccion'],
                    row['marcatarjeta'],
                    row['tarjeta'],
                    row['importe'],
                    row['autorizacion'],
                    row['descripcion'],
                    0.0  # Initial 'used' amount
                ))
                
                inserted_count += 1
                logger.info(f"Inserted row {row['row_index']}: {row['cliente']}, {row['date']}, {row['importe']}")
                
            except Exception as e:
                logger.error(f"Error inserting row {row['row_index']}: {e}")
                error_count += 1
        
        # Commit all changes
        conn.commit()
        
        logger.info(f"Rows inserted: {inserted_count}")
        logger.info(f"Rows skipped: {skipped_count}")
        logger.info(f"Rows with errors: {error_count}")
        
        return {
            "success": True, 
            "inserted": inserted_count, 
            "skipped": skipped_count,
            "errors": error_count
        }
        
    except Exception as e:
        logger.error(f"Fatal error during CompraClick sync process: {e}")
        if conn:
            conn.rollback()
        return {
            "success": False, 
            "inserted": inserted_count, 
            "skipped": skipped_count, 
            "message": str(e)
        }
    
    finally:
        # Clean up database connection
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        logger.info("Database connection closed")

async def validate_compraclick_payment(authorization_number: str, booking_total: float) -> dict:
    """
    Validates a CompraClick payment by checking the remaining balance against the booking total.
    
    IMPORTANT: This function only validates - it does NOT update the database.
    Use reserve_compraclick_payment() to actually reserve the amount after booking succeeds.
    """
    conn = get_db_connection()
    if not conn:
        return {"success": False, "data": {}, "error": "Database connection failed"}
    
    cursor = conn.cursor(dictionary=True)
    # Check payment without locking for update
    query = "SELECT importe, used, codreser, dateused FROM compraclick WHERE autorizacion = %s"
    
    try:
        cursor.execute(query, (authorization_number,))
        result = cursor.fetchone()
        
        if not result:
            return {
                "success": False, 
                "data": {"is_valid": False}, 
                "error": "Authorization code not found",
                "customer_message": "No hemos podido encontrar el número de autorización proporcionado. Por favor, revise su correo electrónico para encontrar el comprobante de pago de CompraClick que contiene el número de autorización y envíenoslo, ya que necesitamos ese número para verificar su pago y proceder con su reserva. Este comprobante se enví de parte del Banco de América Central como un archivo adjunto en PDF. 📧"
            }

        importe = float(result['importe'])
        used = float(result['used'])
        remaining_amount = importe - used

        # Payment is valid if the remaining balance can cover at least 50% of the booking.
        is_valid = remaining_amount >= (booking_total * 0.5)

        # Validation successful - return payment info without updating database
        if is_valid:
            logger.info(f"Successfully validated CompraClick payment '{authorization_number}'. Remaining balance: {remaining_amount}")
            data = {
                "authorization_code": authorization_number,
                "remaining_amount": remaining_amount,
                "booking_total": booking_total,
                "coverage_percentage": (remaining_amount / booking_total) * 100 if booking_total > 0 else 0,
                "is_valid": is_valid
            }
            return {"success": True, "data": data, "error": ""}
        else:
            # Payment found but insufficient balance - provide detailed usage information
            logger.warning(f"CompraClick payment '{authorization_number}' validation failed. Remaining: {remaining_amount}, Required: {booking_total * 0.5}")
            
            # Get additional details about previous usage
            codreser = result.get('codreser', '') or 'N/A'
            dateused = result.get('dateused', '') or 'N/A'
            original_amount = importe
            
            customer_message = (
                f"Hemos encontrado su pago con autorización {authorization_number} por ${original_amount:.2f}, "
                f"pero este pago ya ha sido utilizado previamente. "
                f"\n\n📋 **Detalles del uso anterior:**\n"
                f"• Reserva(s): {codreser}\n"
                f"• Fecha de uso: {dateused}\n"
                f"• Saldo restante: ${remaining_amount:.2f}\n\n"
                f"Si necesita realizar una nueva reserva, puedo generar un nuevo enlace de pago CompraClick. "
                f"¿Le gustaría que proceda con esto? 💳"
            )
            
            data = {
                "authorization_code": authorization_number,
                "remaining_amount": remaining_amount,
                "original_amount": original_amount,
                "booking_total": booking_total,
                "coverage_percentage": (remaining_amount / booking_total) * 100 if booking_total > 0 else 0,
                "is_valid": is_valid,
                "previous_bookings": codreser,
                "date_used": dateused
            }
            
            return {
                "success": False, 
                "data": data, 
                "error": "Payment already used",
                "customer_message": customer_message
            }

    except Exception as e:
        logger.exception("Error validating CompraClick payment")
        return {"success": False, "data": {}, "error": f"Database error: {e}"}
    finally:
        try:
            cursor.close()
        except:
            pass
        try:
            conn.close()
        except:
            pass


async def validate_compraclick_payment_fallback(
    card_last_four: str, 
    charged_amount: float, 
    payment_date: str,
    booking_total: float
) -> dict:
    """
    Fallback validation for CompraClick payments when authorization code is not available.
    Validates by matching credit card last 4 digits, amount, and payment date.
    
    Args:
        card_last_four: Last 4 digits of the credit card used for payment
        charged_amount: Amount charged to the credit card
        payment_date: Date when the payment was made (customer provided, various formats accepted)
        booking_total: Total booking amount to validate against
    
    Returns:
        dict: Validation result with payment details or error message
    """
    from datetime import datetime, timedelta
    import re
    
    logger.info(f"Starting fallback validation - Card ending: {card_last_four}, Amount: {charged_amount}, Date: {payment_date}")
    
    conn = get_db_connection()
    if not conn:
        return {"success": False, "data": {}, "error": "Database connection failed"}
    
    cursor = conn.cursor(dictionary=True)
    
    try:
        # Parse the customer-provided date flexibly
        # Common formats: "today", "yesterday", "12/25", "25-12-2024", "December 25", etc.
        parsed_date = None
        payment_date_lower = payment_date.lower().strip()
        
        if payment_date_lower in ['hoy', 'today']:
            parsed_date = datetime.now()
        elif payment_date_lower in ['ayer', 'yesterday']:
            parsed_date = datetime.now() - timedelta(days=1)
        elif payment_date_lower in ['anteayer', 'antier', 'before yesterday', 'day before yesterday']:
            parsed_date = datetime.now() - timedelta(days=2)
        else:
            # Try various date formats
            date_formats = [
                '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',  # DD/MM/YYYY variants
                '%m/%d/%Y', '%m-%d-%Y', '%m.%d.%Y',  # MM/DD/YYYY variants
                '%Y-%m-%d', '%Y/%m/%d',              # ISO format variants
                '%d/%m', '%d-%m',                     # DD/MM (assume current year)
                '%m/%d', '%m-%d',                     # MM/DD (assume current year)
                '%d de %B', '%d %B',                  # Spanish/English month names
                '%B %d', '%B %d, %Y',                 # English format
                '%d de %B de %Y', '%d de %B del %Y'  # Spanish full format
            ]
            
            for fmt in date_formats:
                try:
                    if fmt in ['%d/%m', '%d-%m', '%m/%d', '%m-%d']:
                        # For formats without year, add current year
                        parsed_date = datetime.strptime(f"{payment_date}/{datetime.now().year}", f"{fmt}/%Y")
                    else:
                        parsed_date = datetime.strptime(payment_date, fmt)
                    break
                except:
                    continue
        
        if not parsed_date:
            logger.error(f"Could not parse payment date: {payment_date}")
            return {
                "success": False,
                "data": {"is_valid": False},
                "error": "Could not parse payment date",
                "customer_message": f"No pude entender la fecha '{payment_date}'. Por favor, proporcione la fecha en formato DD/MM/AAAA (por ejemplo: 25/12/2024) o use palabras como 'hoy', 'ayer'. 📅"
            }
        
        # Create date range for the query (entire day)
        date_start = parsed_date.replace(hour=0, minute=0, second=0)
        date_end = parsed_date.replace(hour=23, minute=59, second=59)
        
        # Ensure card_last_four is exactly 4 digits
        card_last_four = re.sub(r'\D', '', str(card_last_four))[-4:]
        if len(card_last_four) != 4:
            return {
                "success": False,
                "data": {"is_valid": False},
                "error": "Invalid card digits",
                "customer_message": "Por favor, proporcione exactamente los últimos 4 dígitos de su tarjeta de crédito. 💳"
            }
        
        # Query to find matching payment
        # documento column ends with the card last 4 digits
        # importe matches the charged amount (with small tolerance for rounding)
        # date falls within the payment date
        query = """
            SELECT autorizacion, documento, importe, used, codreser, dateused, date
            FROM compraclick 
            WHERE documento LIKE %s
            AND ABS(importe - %s) < 0.01
            AND date BETWEEN %s AND %s
            ORDER BY date DESC
            LIMIT 1
        """
        
        cursor.execute(query, (
            f"%{card_last_four}",
            charged_amount,
            date_start.strftime('%Y-%m-%d %H:%M:%S'),
            date_end.strftime('%Y-%m-%d %H:%M:%S')
        ))
        
        result = cursor.fetchone()
        
        if not result:
            logger.warning(f"No payment found matching: Card ending {card_last_four}, Amount ${charged_amount}, Date {parsed_date.strftime('%Y-%m-%d')}")
            
            # Try a broader search to provide helpful feedback
            broader_query = """
                SELECT COUNT(*) as count, 
                       SUM(CASE WHEN documento LIKE %s THEN 1 ELSE 0 END) as card_matches,
                       SUM(CASE WHEN ABS(importe - %s) < 0.01 THEN 1 ELSE 0 END) as amount_matches,
                       SUM(CASE WHEN date BETWEEN %s AND %s THEN 1 ELSE 0 END) as date_matches
                FROM compraclick
                WHERE date BETWEEN %s AND %s
            """
            
            date_range_start = (parsed_date - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            date_range_end = (parsed_date + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            
            cursor.execute(broader_query, (
                f"%{card_last_four}",
                charged_amount,
                date_start.strftime('%Y-%m-%d %H:%M:%S'),
                date_end.strftime('%Y-%m-%d %H:%M:%S'),
                date_range_start,
                date_range_end
            ))
            
            debug_info = cursor.fetchone()
            
            customer_message = (
                f"No encontramos un pago que coincida con los datos proporcionados:\n\n"
                f"💳 **Tarjeta terminada en:** {card_last_four}\n"
                f"💰 **Monto:** ${charged_amount:.2f}\n"
                f"📅 **Fecha:** {parsed_date.strftime('%d/%m/%Y')}\n\n"
            )
            
            if debug_info:
                if debug_info['card_matches'] == 0:
                    customer_message += "❌ No encontramos pagos con esos últimos 4 dígitos de tarjeta.\n"
                if debug_info['amount_matches'] == 0:
                    customer_message += "❌ No encontramos pagos por ese monto exacto.\n"
                if debug_info['date_matches'] == 0:
                    customer_message += "❌ No encontramos pagos en esa fecha.\n"
            
            customer_message += (
                "\nPor favor verifique:\n"
                "1️⃣ Que los últimos 4 dígitos sean correctos\n"
                "2️⃣ Que el monto sea el total cargado a su tarjeta\n"
                "3️⃣ Que la fecha sea cuando realizó el pago\n\n"
                "Si los datos son correctos, es posible que el banco aún no haya reportado su transacción. "
                "Intentaremos sincronizar nuevamente en unos minutos. ⏳"
            )
            
            return {
                "success": False,
                "data": {"is_valid": False},
                "error": "Payment not found with provided details",
                "customer_message": customer_message
            }
        
        # Payment found - validate balance
        autorizacion = result['autorizacion']
        importe = float(result['importe'])
        used = float(result['used'])
        remaining_amount = importe - used
        
        # Payment is valid if the remaining balance can cover at least 50% of the booking
        is_valid = remaining_amount >= (booking_total * 0.5)
        
        if is_valid:
            logger.info(f"Successfully validated payment via fallback - Auth: {autorizacion}, Remaining: ${remaining_amount:.2f}")
            
            data = {
                "authorization_code": autorizacion,
                "remaining_amount": remaining_amount,
                "booking_total": booking_total,
                "coverage_percentage": (remaining_amount / booking_total) * 100 if booking_total > 0 else 0,
                "is_valid": is_valid,
                "validation_method": "fallback",
                "matched_criteria": {
                    "card_last_four": card_last_four,
                    "amount": charged_amount,
                    "date": parsed_date.strftime('%Y-%m-%d')
                }
            }
            
            customer_message = (
                f"✅ **¡Pago encontrado y validado exitosamente!**\n\n"
                f"📋 **Detalles del pago:**\n"
                f"• Autorización: {autorizacion}\n"
                f"• Monto original: ${importe:.2f}\n"
                f"• Saldo disponible: ${remaining_amount:.2f}\n"
                f"• Cobertura del booking: {data['coverage_percentage']:.1f}%\n\n"
                f"Procederemos con su reserva ahora. 🎉"
            )
            
            return {
                "success": True,
                "data": data,
                "error": "",
                "customer_message": customer_message
            }
        else:
            # Payment found but insufficient balance
            codreser = result.get('codreser', '') or 'N/A'
            dateused = result.get('dateused', '') or 'N/A'
            
            customer_message = (
                f"Encontramos su pago (Autorización: {autorizacion}) por ${importe:.2f}, "
                f"pero ya ha sido utilizado previamente.\n\n"
                f"📋 **Detalles del uso anterior:**\n"
                f"• Reserva(s): {codreser}\n"
                f"• Fecha de uso: {dateused}\n"
                f"• Saldo restante: ${remaining_amount:.2f}\n\n"
                f"El saldo restante no es suficiente para cubrir su nueva reserva. "
                f"¿Le gustaría que genere un nuevo enlace de pago CompraClick? 💳"
            )
            
            data = {
                "authorization_code": autorizacion,
                "remaining_amount": remaining_amount,
                "original_amount": importe,
                "booking_total": booking_total,
                "coverage_percentage": (remaining_amount / booking_total) * 100 if booking_total > 0 else 0,
                "is_valid": is_valid,
                "previous_bookings": codreser,
                "date_used": dateused,
                "validation_method": "fallback"
            }
            
            return {
                "success": False,
                "data": data,
                "error": "Insufficient balance",
                "customer_message": customer_message
            }
            
    except Exception as e:
        logger.exception(f"Error in fallback validation: {e}")
        return {
            "success": False,
            "data": {},
            "error": f"Database error: {e}",
            "customer_message": "Ocurrió un error al validar su pago. Por favor, intente nuevamente en unos momentos. 🔄"
        }
    finally:
        try:
            cursor.close()
        except:
            pass
        try:
            conn.close()
        except:
            pass


async def reserve_compraclick_payment(authorization_number: str, booking_total: float) -> dict:
    """
    Reserves a CompraClick payment amount by updating the 'used' column.
    This should only be called after successful booking validation and just before the booking HTTP call.

    Args:
        authorization_number: CompraClick authorization code
        booking_total: The amount to reserve

    Returns:
        A dictionary with the reservation result
    """
    logger.info(f"Attempting to reserve {booking_total} from CompraClick auth '{authorization_number}'")
    conn = get_db_connection()
    if not conn:
        return {"success": False, "error": "Database connection failed"}
    
    cursor = conn.cursor(dictionary=True)
    # Lock the row for update to prevent race conditions
    query = "SELECT importe, used FROM compraclick WHERE autorizacion = %s FOR UPDATE"
    
    try:
        cursor.execute(query, (authorization_number,))
        result = cursor.fetchone()
        
        if not result:
            return {"success": False, "error": "Authorization code not found"}

        importe = float(result['importe'])
        used = float(result['used'])
        remaining_amount = importe - used

        # Check if payment is still valid (remaining balance can cover at least 50% of booking)
        if remaining_amount < (booking_total * 0.5):
            logger.warning(f"CompraClick payment '{authorization_number}' insufficient balance. Remaining: {remaining_amount}, Required: {booking_total * 0.5}")
            return {"success": False, "error": f"Fondos insuficientes. Saldo disponible: {remaining_amount:.2f}, se requiere: {booking_total * 0.5:.2f}"}

        # Add the booking total to the 'used' amount for this transaction
        new_used_amount = used + booking_total
        update_query = "UPDATE compraclick SET used = %s WHERE autorizacion = %s"
        cursor.execute(update_query, (new_used_amount, authorization_number))
        conn.commit()
        
        logger.info(f"Successfully reserved {booking_total} from CompraClick auth '{authorization_number}'. New used amount: {new_used_amount}")
        return {"success": True, "message": f"Cantidad {booking_total:.2f} reservada exitosamente"}

    except Exception as e:
        logger.exception(f"Error reserving CompraClick payment: {e}")
        return {"success": False, "error": f"Database error: {e}"}
    finally:
        try:
            cursor.close()
        except:
            pass
        try:
            conn.close()
        except:
            pass


async def create_compraclick_link(customer_name: str, payment_amount: float, calculation_explanation: str, payment_percentage: str = "100%") -> dict:
    """
    Creates a CompraClick payment link for the customer. 

    IMPORTANT: You must first perform a step-by-step calculation and explain it in the `calculation_explanation` parameter before calling this tool.
    For example: "The user wants to pay 50% of $406. Calculation: $406 * 0.5 = $203. The final payment amount is $203."
    
    Args:
        customer_name: Name to use for the payment link.
        payment_amount: Total booking amount.
        calculation_explanation: A step-by-step explanation of how the payment amount was calculated.
        payment_percentage: "50%" or "100%" of total amount.
    
    Returns:
        dict: {"success": bool, "link": str, "error": str}
    """
    # Log the calculation explanation received from the assistant
    logger.info(f"Calculation explanation: {calculation_explanation}")

    # Load credentials from existing environment setup
    email = os.getenv('COMPRACLICK_EMAIL')
    password = os.getenv('COMPRACLICK_PASSWORD')
    
    if not email or not password:
        return {"success": False, "link": "", "error": "CompraClick credentials not found in environment variables"}
    
    # Calculate the actual payment amount based on the percentage
    actual_payment_amount = payment_amount
    if payment_percentage == "50%":
        actual_payment_amount = payment_amount * 0.5

    # Format amount to 2 decimal places
    actual_payment_amount = round(actual_payment_amount, 2)
    
    try:
        async with async_playwright() as p:
            # Launch browser with proper configuration
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--start-maximized']
            )
            
            # Create a context with proper download behavior
            context = await browser.new_context()
            page = await context.new_page()
            
            try:
                logger.info(f"Starting CompraClick link creation for customer: {customer_name}, "
                          f"amount: {actual_payment_amount} ({payment_percentage})")
                
                # STEP 1: Authentication
                link = await authenticate_and_navigate(page, email, password)
                if link:
                    # If we already have a link from a previous step (error handling), return it
                    return {"success": True, "link": link, "error": ""}
                
                # STEP 2: Navigate to CompraClick section and create new link
                link = await create_new_compraclick(
                    page, 
                    customer_name=customer_name,
                    payment_amount=actual_payment_amount
                )
                
                # Return the result
                if link:
                    logger.info(f"Successfully created CompraClick link for {customer_name}")
                    return {"success": True, "link": link, "error": ""}
                else:
                    return {
                        "success": False, 
                        "link": "", 
                        "error": "Failed to create or retrieve CompraClick link"
                    }
            
            except Exception as e:
                logger.exception(f"Error in CompraClick automation: {str(e)}")
                # Take a screenshot on error for debugging
                screenshot_path = f"/tmp/compraclick_error_{int(time.time())}.png"
                error_message = f"CompraClick automation error: {str(e)}"
                try:
                    await page.screenshot(path=screenshot_path, full_page=True)
                    logger.info(f"Screenshot saved to {screenshot_path} on error.")
                    error_message += f" Screenshot saved to {screenshot_path}"
                except Exception as screenshot_err:
                    logger.error(f"Failed to take screenshot: {screenshot_err}")
                
                return {"success": False, "link": "", "error": error_message}
            
            finally:
                # Always attempt to logout and close resources
                try:
                    await logout(page)
                except Exception as logout_err:
                    logger.warning(f"Error during logout (non-critical): {str(logout_err)}")
                finally:
                    await context.close()
                    await browser.close()
                    logger.info("Browser closed")
    
    except Exception as e:
        error_message = str(e)
        logger.exception(f"Failed to initialize browser automation: {error_message}")
        
        # Check if it's a Playwright browser missing error
        if "Executable doesn't exist" in error_message or "playwright" in error_message.lower():
            logger.warning("[PLAYWRIGHT_ERROR] Detected missing browser error in CompraClick tool!")
            
            # Attempt auto-recovery
            if reinstall_playwright_browsers():
                logger.info("[PLAYWRIGHT_RECOVERY] Browsers reinstalled! Please retry the operation.")
                return {
                    "success": False, 
                    "link": "", 
                    "error": "Browser was missing but has been auto-installed. Please try again in a moment."
                }
            else:
                logger.error("[PLAYWRIGHT_RECOVERY] Auto-recovery failed!")
                return {
                    "success": False, 
                    "link": "", 
                    "error": "Browser missing and auto-recovery failed. Please contact support."
                }
        
        return {"success": False, "link": "", "error": f"Browser automation initialization failed: {error_message}"}


async def authenticate_and_navigate(page: Page, email: str, password: str) -> Optional[str]:
    """
    Authenticate to the CompraClick system and navigate to the CompraClick section
    
    Args:
        page: Playwright page object
        email: Login email
        password: Login password
        
    Returns:
        Optional[str]: If there's an error but we have a link, return it, otherwise None
    """
    try:
        # Navigate to the login page and wait for it to load
        logger.info(f"Navigating to {COMPRACLICK_URL}")
        await page.goto(COMPRACLICK_URL, timeout=120000)
        
        # Wait for the login form and fill it
        logger.info("Filling login form")
        await page.wait_for_selector('input[placeholder="E-mail"]', timeout=60000)
        await page.type('input[placeholder="E-mail"]', email)
        await page.type('input[placeholder="Contraseña"]', password)
        
        # Submit login form
        await page.click('button[type="submit"].btn-secondary')
        
        # Wait for navigation to complete
        await page.wait_for_load_state('networkidle', timeout=120000)
        logger.info("Login successful")

        # Click on "COMPRA-CLICK" in the navigation menu
        logger.info("Navigating to COMPRA-CLICK section")

        # Add a delay for any post-login scripts or overlays to settle
        await page.wait_for_timeout(3000)

        # Use a robust locator to find and click the link
        compra_click_locator = page.locator("a:has-text('COMPRA-CLICK')").first
        await compra_click_locator.wait_for(state='visible', timeout=30000)
        await compra_click_locator.click()
        
        await page.wait_for_load_state('networkidle', timeout=60000)
        logger.info("Successfully navigated to COMPRA-CLICK section")
            
        return None  # No errors, continue with next step
    
    except PlaywrightTimeoutError as e:
        logger.error(f"Timeout during authentication: {str(e)}")
        raise Exception(f"Authentication timed out: {str(e)}")
    
    except Exception as e:
        logger.exception(f"Error during authentication: {str(e)}")
        raise Exception(f"Authentication failed: {str(e)}")


async def create_new_compraclick(
    page: Page, 
    customer_name: str,
    payment_amount: float
) -> Optional[str]:
    """
    Creates a new CompraClick payment link using robust locators.
    """
    try:
        # 1. Click the "Nuevo Compra-Click" button
        logger.info("Clicking 'Nuevo Compra-Click' button...")
        await page.get_by_role("button", name="Nuevo Compra-Click").click()

        # 2. Fill the creation form
        logger.info(f"Filling form for {customer_name} with amount {payment_amount}")
        await page.locator('input[name="description"]').fill(customer_name)
        await page.locator('input[field="amount"]').fill(str(payment_amount))
        await page.locator('input[name="maxElements"]').fill("1")

        # 3. Handle the custom language dropdown
        logger.info("Opening the language dropdown...")
        # The language selector is a custom 'react-select' component.
        # We click the specific placeholder div to open the dropdown, resolving the strict mode violation.
        await page.locator("div.Select__placeholder:has-text('Idioma')").click()

        logger.info("Selecting 'Español' from the options...")
        await page.get_by_text("Español", exact=True).click()
        
        # 4. Submit the form
        await page.get_by_role("button", name="Crear", exact=True).click()

        # 5. Wait for the modal to close and table to update
        logger.info("Waiting for the table to update with the new link...")
        await page.wait_for_load_state("networkidle", timeout=30000)

        # 6. Find the new row and click the share icon button
        logger.info(f"Searching for row containing '{customer_name}'...")
        # Find the first table row that contains the customer's name to avoid ambiguity
        row_locator = page.locator(f"tr:has-text('{customer_name}')").first
        await row_locator.wait_for(state='visible', timeout=15000)

        logger.info("Found the row. Clicking the share icon button...")
        share_icon_button = row_locator.locator("button.payment-button-actions:has(i.mdi-share)")
        await share_icon_button.click()

        # 7. In the new modal, click the 'Compartir' button
        logger.info("Waiting for the first modal and clicking 'Compartir'...")
        # The 'Compartir' button might not be a standard button element. Use get_by_text for flexibility.
        compartir_button = page.get_by_text("Compartir", exact=True)
        await compartir_button.click()

        # 8. Wait for the final modal and get the link
        logger.info("Waiting for the final modal with the link...")
        link_input = page.locator("input#social-link")
        await link_input.wait_for(state='visible', timeout=15000)

        logger.info("Retrieving link from input field...")
        link = await link_input.get_attribute('value')

        # 9. Close the final modal by clicking the 'x' button
        logger.info("Closing the final modal...")
        await page.locator("div.modal-content button.close").click()

        if not link:
            raise Exception("Could not extract link from the success modal.")

        logger.info(f"Successfully retrieved CompraClick link: {link}")
        
        return link
        
    except Exception as e:
        logger.exception(f"Error creating CompraClick link: {str(e)}")
        raise Exception(f"Failed to create CompraClick link: {str(e)}")


async def logout(page: Page) -> None:
    """
    Performs logout operation
    
    Args:
        page: Playwright page object
    """
    try:
        logger.info("Attempting to log out...")
        
        # Click on the account circle icon
        account_icon = await page.query_selector('i.mdi-account-circle')
        if account_icon:
            await account_icon.click()
            await page.wait_for_selector('button.nav-menu-item.dropdown-item', timeout=30000)
            
            # Find and click the logout button
            await page.evaluate('''
            () => {
                const logoutButton = Array.from(document.querySelectorAll('button.nav-menu-item.dropdown-item'))
                  .find(el => el.querySelector('span') && el.querySelector('span').textContent.trim() === 'Cerrar Sesión');
                if (logoutButton) {
                  logoutButton.click();
                }
            }
            ''')
            
            # Wait a moment for logout to complete
            await page.wait_for_timeout(5000)
            logger.info("Logged out successfully")
        else:
            logger.warning("Could not find account icon for logout")
    
    except Exception as e:
        logger.warning(f"Error during logout (non-critical): {str(e)}")


async def trigger_compraclick_retry_for_missing_payment(phone_number: str, authorization_number: str, booking_total: float, booking_data: Dict[str, Any]) -> dict:
    """
    Triggers the CompraClick retry mechanism when a valid payment proof was provided
    but the payment is not found after a successful sync. This handles the scenario
    where the bank's system takes time to update.
    
    Args:
        phone_number: Customer's phone number
        authorization_number: The authorization number from the payment proof
        booking_total: Total booking amount
        booking_data: Complete booking information
    
    Returns:
        dict: Success status and customer message
    """
    try:
        logger.info(f"Triggering CompraClick retry for missing payment - Phone: {phone_number}, Auth: {authorization_number}")
        
        # Send reassurance message to customer in Spanish
        reassurance_message = (
            f"Perfecto! El comprobante que nos ha enviado es correcto y hemos identificado su número de autorización ({authorization_number}). "
            f"Sin embargo, el sistema del banco puede tardar unos minutos en actualizar su base de datos. "
            f"\n\n⏳ **No se preocupe, solo esperaremos un poco más para que el banco actualice su información.** "
            f"Su pago aparecerá en cuestión de minutos y procederemos automáticamente con su reserva. "
            f"\n\n✅ **Le aseguramos que su pago está correcto** y será procesado tan pronto como el sistema del banco se actualice. "
            f"No necesita hacer nada más por su parte. ¡Gracias por su paciencia! 😊"
        )
        
        await send_wati_message(phone_number, reassurance_message)
        
        # Prepare payment data for retry mechanism
        payment_data = {
            "authorization_number": authorization_number,
            "booking_total": booking_total,
            "booking_data": booking_data
        }
        
        # Start the retry process
        await start_compraclick_retry_process(phone_number, payment_data)
        
        logger.info(f"CompraClick retry process started successfully for {phone_number}")
        
        return {
            "success": True,
            "data": {
                "retry_started": True,
                "authorization_number": authorization_number
            },
            "error": "",
            "customer_message": reassurance_message
        }
        
    except Exception as e:
        logger.exception(f"Error triggering CompraClick retry for {phone_number}: {e}")
        return {
            "success": False,
            "data": {},
            "error": f"Failed to start retry process: {e}"
        }
