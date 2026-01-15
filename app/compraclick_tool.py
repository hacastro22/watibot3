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
from .database_client import get_db_connection, execute_with_retry, check_room_availability, check_room_availability_counts
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

    # Retry loop: If Playwright browser is missing, install and retry once
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
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
            logger.exception(f"Failed to initialize browser for CompraClick sync (attempt {attempt}/{max_attempts}): {error_message}")
            
            # Check if it's a Playwright browser missing error
            if "Executable doesn't exist" in error_message or "playwright" in error_message.lower():
                logger.warning("[PLAYWRIGHT_ERROR] Detected missing browser error in CompraClick sync!")
                
                # Attempt auto-recovery and retry
                if attempt < max_attempts and reinstall_playwright_browsers():
                    logger.info("[PLAYWRIGHT_RECOVERY] Browsers reinstalled! Retrying automatically...")
                    continue  # Retry the loop
                elif attempt >= max_attempts:
                    logger.error("[PLAYWRIGHT_RECOVERY] Auto-recovery failed after max attempts!")
                    return {
                        "success": False, 
                        "data": {}, 
                        "error": "Browser missing and auto-recovery failed. Please contact support."
                    }
            
            # For non-Playwright errors, return immediately
            return {"success": False, "data": {}, "error": f"Browser initialization failed: {error_message}"}
    
    # Should not reach here, but just in case
    return {"success": False, "data": {}, "error": "Unexpected error in browser automation"}

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
    """
    Processes the downloaded XLS file and inserts data into the compraclick table.
    Uses infinite retry for database operations.
    """
    logger.info(f"Starting process with file: {file_path}")
    
    # --- FILE PARSING PHASE (no retry needed - file is read once) ---
    try:
        # Read Excel file without header assumption (read as raw data)
        df = pd.read_excel(file_path, header=None)
        logger.info(f"Found {len(df)} rows in the file")
        
        # Skip first row which might be header or empty (matching JS logic)
        data_rows = df.iloc[1:].values.tolist()
        logger.info(f"Processing {len(data_rows)} data rows (skipped first row)")
        
        # Define column mappings from Excel file to database schema (matching JS reference)
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
            string_value = str(value).strip()
            if string_value == '':
                return None
            cleaned_value = string_value.replace(',', '')
            try:
                return float(cleaned_value)
            except ValueError:
                logger.warning(f"Could not parse decimal value '{string_value}' - returning None")
                return None
        
        def safe_string(value):
            """Convert None or NaN values to empty string for NOT NULL columns"""
            if value is None or pd.isna(value):
                return ''
            return str(value).strip()
        
        # Collect valid rows to be processed
        valid_rows = []
        
        # First pass: collect valid rows (matching JS logic)
        for i, row in enumerate(data_rows):
            if not row or len(row) < max(column_map.values()) + 1:
                logger.info(f"Skipping row {i + 1}: insufficient columns")
                continue
            
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
                
                importe = clean_decimal_value(raw_importe)
                
                if not date or importe is None:
                    logger.info(f"Skipping row {i + 1}: missing date or importe")
                    continue
                
                if not isinstance(date, pd.Timestamp):
                    try:
                        date = pd.to_datetime(date)
                    except:
                        logger.warning(f"Could not parse date '{date}' in row {i + 1}")
                        continue
                
                valid_rows.append({
                    'row_index': i + 1,
                    'date': date,
                    'sucursal': safe_string(sucursal),
                    'usuario': safe_string(usuario),
                    'estado': safe_string(estado),
                    'cliente': safe_string(cliente),
                    'documento': safe_string(documento),
                    'email': safe_string(email),
                    'direccion': safe_string(direccion),
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
        
    except Exception as e:
        logger.error(f"Fatal error during file parsing: {e}")
        return {"success": False, "inserted": 0, "skipped": 0, "message": str(e)}
    
    # --- DATABASE INSERTION PHASE (with infinite retry) ---
    def _execute_database_insertion():
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            
            inserted_count = 0
            skipped_count = 0
            error_count = 0

            # Schema Migration: Ensure 'used' column exists
            try:
                cursor.execute("ALTER TABLE compraclick ADD COLUMN used DECIMAL(10, 2) NOT NULL DEFAULT 0.00")
                conn.commit()
                logger.info("Added 'used' column to 'compraclick' table.")
            except Exception as e:
                if "Duplicate column name" in str(e):
                    logger.info("'used' column already exists.")
                    conn.rollback()
                else:
                    raise e
            
            insert_query = """
                INSERT INTO compraclick 
                (date, sucursal, usuario, estado, cliente, documento, email, direccion, 
                 marcatarjeta, tarjeta, importe, autorizacion, descripcion, used)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            check_duplicate_query = """
                SELECT COUNT(*) as count FROM compraclick 
                WHERE importe = %s AND autorizacion = %s
            """
            
            for row in valid_rows:
                try:
                    mysql_date = row['date'] if isinstance(row['date'], str) else row['date'].strftime('%Y-%m-%d %H:%M:%S')
                    
                    cursor.execute(check_duplicate_query, (row['importe'], row['autorizacion']))
                    duplicate_result = cursor.fetchone()
                    
                    if duplicate_result and duplicate_result[0] > 0:
                        logger.info(f"Skipping row {row['row_index']}: duplicate entry detected ({row['cliente']}, {row['date']})")
                        skipped_count += 1
                        continue
                    
                    cursor.execute(insert_query, (
                        mysql_date,
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
                        0.0
                    ))
                    
                    inserted_count += 1
                    logger.info(f"Inserted row {row['row_index']}: {row['cliente']}, {row['date']}, {row['importe']}")
                    
                except Exception as e:
                    logger.error(f"Error inserting row {row['row_index']}: {e}")
                    error_count += 1
            
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
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
            logger.info("Database connection closed")
    
    return execute_with_retry(_execute_database_insertion, f"process_xls_and_insert_to_db({file_path})")

async def validate_compraclick_payment(authorization_number: str, booking_total: float) -> dict:
    """
    Validates a CompraClick payment by checking the remaining balance against the booking total.
    Uses infinite retry for database operations.
    
    IMPORTANT: This function only validates - it does NOT update the database.
    Use reserve_compraclick_payment() to actually reserve the amount after booking succeeds.
    """
    def _execute_validation():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True, buffered=True)
            query = "SELECT importe, used, codreser, dateused FROM compraclick WHERE autorizacion = %s"
            
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
            codreser = result.get('codreser') or ''
            remaining_amount = importe - used

            # Check for ORPHANED reservation (used but no booking completed)
            if remaining_amount < booking_total and (not codreser or codreser.strip() == ''):
                # Payment was reserved but booking never completed - reset and allow reuse
                logger.warning(f"[ORPHAN_RECOVERY] CompraClick {authorization_number}: used={used}, importe={importe}, codreser='{codreser}' - resetting")
                reset_query = "UPDATE compraclick SET used = 0 WHERE autorizacion = %s"
                cursor.execute(reset_query, (authorization_number,))
                conn.commit()
                logger.info(f"[ORPHAN_RECOVERY] Reset orphaned CompraClick {authorization_number} - now available for booking")
                
                # Recalculate with reset values
                used = 0
                remaining_amount = importe

            is_valid = remaining_amount >= (booking_total * 0.5)

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
                logger.warning(f"CompraClick payment '{authorization_number}' validation failed. Remaining: {remaining_amount}, Required: {booking_total * 0.5}")
                
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
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    return execute_with_retry(_execute_validation, f"validate_compraclick_payment({authorization_number})")


async def validate_compraclick_payment_fallback(
    card_last_four: str, 
    charged_amount: float, 
    payment_date: str,
    booking_total: float
) -> dict:
    """
    Fallback validation for CompraClick payments when authorization code is not available.
    Validates by matching credit card last 4 digits, amount, and payment date.
    Uses infinite retry for database operations.
    
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
    
    # --- INPUT VALIDATION PHASE (no retry - user input errors) ---
    # Parse the customer-provided date flexibly
    parsed_date = None
    payment_date_lower = payment_date.lower().strip()
    
    if payment_date_lower in ['hoy', 'today']:
        parsed_date = datetime.now()
    elif payment_date_lower in ['ayer', 'yesterday']:
        parsed_date = datetime.now() - timedelta(days=1)
    elif payment_date_lower in ['anteayer', 'antier', 'before yesterday', 'day before yesterday']:
        parsed_date = datetime.now() - timedelta(days=2)
    else:
        date_formats = [
            '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
            '%m/%d/%Y', '%m-%d-%Y', '%m.%d.%Y',
            '%Y-%m-%d', '%Y/%m/%d',
            '%d/%m', '%d-%m',
            '%m/%d', '%m-%d',
            '%d de %B', '%d %B',
            '%B %d', '%B %d, %Y',
            '%d de %B de %Y', '%d de %B del %Y'
        ]
        
        for fmt in date_formats:
            try:
                if fmt in ['%d/%m', '%d-%m', '%m/%d', '%m-%d']:
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
    
    date_start = parsed_date.replace(hour=0, minute=0, second=0)
    date_end = parsed_date.replace(hour=23, minute=59, second=59)
    
    # Ensure card_last_four is exactly 4 digits
    card_last_four_clean = re.sub(r'\D', '', str(card_last_four))[-4:]
    if len(card_last_four_clean) != 4:
        return {
            "success": False,
            "data": {"is_valid": False},
            "error": "Invalid card digits",
            "customer_message": "Por favor, proporcione exactamente los últimos 4 dígitos de su tarjeta de crédito. 💳"
        }
    
    # --- DATABASE QUERY PHASE (with infinite retry) ---
    def _execute_fallback_validation():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            
            query = """
                SELECT autorizacion, documento, importe, used, codreser, dateused, date, tarjeta
                FROM compraclick 
                WHERE tarjeta LIKE %s
                AND ABS(importe - %s) < 0.01
                AND date BETWEEN %s AND %s
                ORDER BY date DESC
                LIMIT 1
            """
            
            cursor.execute(query, (
                f"%{card_last_four_clean}%",
                charged_amount,
                date_start.strftime('%Y-%m-%d %H:%M:%S'),
                date_end.strftime('%Y-%m-%d %H:%M:%S')
            ))
            
            result = cursor.fetchone()
            
            if not result:
                logger.warning(f"No payment found matching: Card ending {card_last_four_clean}, Amount ${charged_amount}, Date {parsed_date.strftime('%Y-%m-%d')}")
                
                broader_query = """
                    SELECT COUNT(*) as count, 
                           SUM(CASE WHEN tarjeta LIKE %s THEN 1 ELSE 0 END) as card_matches,
                           SUM(CASE WHEN ABS(importe - %s) < 0.01 THEN 1 ELSE 0 END) as amount_matches,
                           SUM(CASE WHEN date BETWEEN %s AND %s THEN 1 ELSE 0 END) as date_matches
                    FROM compraclick
                    WHERE date BETWEEN %s AND %s
                """
                
                date_range_start = (parsed_date - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                date_range_end = (parsed_date + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                
                cursor.execute(broader_query, (
                    f"%{card_last_four_clean}%",
                    charged_amount,
                    date_start.strftime('%Y-%m-%d %H:%M:%S'),
                    date_end.strftime('%Y-%m-%d %H:%M:%S'),
                    date_range_start,
                    date_range_end
                ))
                
                debug_info = cursor.fetchone()
                
                customer_message = (
                    f"No encontramos un pago que coincida con los datos proporcionados:\n\n"
                    f"💳 **Tarjeta terminada en:** {card_last_four_clean}\n"
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
            
            autorizacion = result['autorizacion']
            importe = float(result['importe'])
            used = float(result['used'])
            codreser_check = result.get('codreser') or ''
            remaining_amount = importe - used
            
            # Check for ORPHANED reservation (used but no booking completed)
            if remaining_amount < booking_total and (not codreser_check or codreser_check.strip() == ''):
                # Payment was reserved but booking never completed - reset and allow reuse
                logger.warning(f"[ORPHAN_RECOVERY] CompraClick fallback {autorizacion}: used={used}, importe={importe}, codreser='{codreser_check}' - resetting")
                reset_query = "UPDATE compraclick SET used = 0 WHERE autorizacion = %s"
                cursor.execute(reset_query, (autorizacion,))
                conn.commit()
                logger.info(f"[ORPHAN_RECOVERY] Reset orphaned CompraClick {autorizacion} via fallback - now available for booking")
                
                # Recalculate with reset values
                used = 0
                remaining_amount = importe
            
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
                        "card_last_four": card_last_four_clean,
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
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    return execute_with_retry(_execute_fallback_validation, f"validate_compraclick_payment_fallback({card_last_four_clean})")


async def reserve_compraclick_payment(authorization_number: str, booking_total: float) -> dict:
    """
    Reserves a CompraClick payment amount by updating the 'used' column.
    Uses infinite retry for database operations.
    This should only be called after successful booking validation and just before the booking HTTP call.

    Args:
        authorization_number: CompraClick authorization code
        booking_total: The amount to reserve

    Returns:
        A dictionary with the reservation result
    """
    logger.info(f"Attempting to reserve {booking_total} from CompraClick auth '{authorization_number}'")
    
    def _execute_reservation():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)
            query = "SELECT importe, used FROM compraclick WHERE autorizacion = %s FOR UPDATE"
            
            cursor.execute(query, (authorization_number,))
            result = cursor.fetchone()
            
            if not result:
                return {"success": False, "error": "Authorization code not found"}

            importe = float(result['importe'])
            used = float(result['used'])
            remaining_amount = importe - used

            if remaining_amount < (booking_total * 0.5):
                logger.warning(f"CompraClick payment '{authorization_number}' insufficient balance. Remaining: {remaining_amount}, Required: {booking_total * 0.5}")
                return {"success": False, "error": f"Fondos insuficientes. Saldo disponible: {remaining_amount:.2f}, se requiere: {booking_total * 0.5:.2f}"}

            new_used_amount = used + booking_total
            update_query = "UPDATE compraclick SET used = %s WHERE autorizacion = %s"
            cursor.execute(update_query, (new_used_amount, authorization_number))
            conn.commit()
            
            logger.info(f"Successfully reserved {booking_total} from CompraClick auth '{authorization_number}'. New used amount: {new_used_amount}")
            return {"success": True, "message": f"Cantidad {booking_total:.2f} reservada exitosamente"}
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    return execute_with_retry(_execute_reservation, f"reserve_compraclick_payment({authorization_number})")


async def create_compraclick_link(
    customer_name: str, 
    payment_amount: float, 
    calculation_explanation: str, 
    payment_percentage: str = "100%",
    service_type: str = "pasadia",
    check_in_date: str = None,
    check_out_date: str = None,
    bungalow_type: str = None,
    adults: int = 0,
    children_6_10: int = 0,
    num_rooms: int = 1
) -> dict:
    """
    Creates a CompraClick payment link for the customer. 

    IMPORTANT: You must first perform a step-by-step calculation and explain it in the `calculation_explanation` parameter before calling this tool.
    For example: "The user wants to pay 50% of $406. Calculation: $406 * 0.5 = $203. The final payment amount is $203."
    
    AVAILABILITY GATE: For hospedaje/lodging, this function will verify room availability
    before creating the payment link. If no rooms are available, no link will be created.
    If bungalow_type is specified, it will verify that SPECIFIC room type is available.
    
    Args:
        customer_name: Name to use for the payment link.
        payment_amount: Total booking amount.
        calculation_explanation: A step-by-step explanation of how the payment amount was calculated.
        payment_percentage: "50%" or "100%" of total amount.
        service_type: "pasadia" or "hospedaje" - determines if availability check is needed.
        check_in_date: Check-in date (YYYY-MM-DD) - REQUIRED for hospedaje.
        check_out_date: Check-out date (YYYY-MM-DD) - REQUIRED for hospedaje.
        bungalow_type: Room type being booked (Familiar, Junior, Habitación, Matrimonial) - REQUIRED for hospedaje.
        adults: Number of adults in the group - used to filter compatible room alternatives.
        children_6_10: Number of children aged 6-10 - used to calculate occupancy.
        num_rooms: Number of rooms being booked (default 1) - for multi-room bookings.
    
    Returns:
        dict: {"success": bool, "link": str, "error": str, "availability_blocked": bool}
    """
    # Log the calculation explanation received from the assistant
    logger.info(f"Calculation explanation: {calculation_explanation}")
    logger.info(f"Service type: {service_type}, Check-in: {check_in_date}, Check-out: {check_out_date}, Bungalow type: {bungalow_type}")
    logger.info(f"Group: {adults} adults, {children_6_10} children 6-10, {num_rooms} room(s)")
    
    # 🚨 PLACEHOLDER VALIDATION: Reject placeholder customer names
    placeholder_values = [
        'cliente', 'pendiente', 'pendiente_nombre', 'no_provided', 'not_provided',
        'n/a', 'na', 'sin_dato', 'por_definir', 'unknown', 'desconocido', 'auto'
    ]
    customer_name_lower = customer_name.strip().lower() if customer_name else ''
    
    if not customer_name or len(customer_name.strip()) < 3 or customer_name_lower in placeholder_values:
        logger.warning(f"[PLACEHOLDER_BLOCKED] Rejected invalid customer_name: '{customer_name}'")
        return {
            "success": False,
            "link": "",
            "error": f"🚨 BLOQUEADO: El nombre '{customer_name}' no es válido. Debes solicitar el nombre REAL del cliente antes de crear el enlace de pago.",
            "assistant_instruction": "PREGUNTA AL CLIENTE: '¿Me proporciona su nombre completo para el enlace de pago?' NUNCA uses placeholders como 'Cliente', 'PENDIENTE', 'NO_PROVIDED', etc."
        }
    
    # 🚨 AVAILABILITY GATE: For lodging, verify room availability BEFORE creating payment link
    if service_type.lower() in ["hospedaje", "estadía", "estadia", "lodging", "las hojas", "escapadita", "romántico", "romantico"]:
        if not check_in_date or not check_out_date:
            logger.error(f"[AVAILABILITY_GATE] Lodging payment blocked - missing dates. Service: {service_type}")
            return {
                "success": False,
                "link": "",
                "error": "🚨 BLOQUEADO: Para hospedaje/estadía DEBES proporcionar check_in_date y check_out_date. No se puede crear enlace de pago sin verificar disponibilidad primero.",
                "availability_blocked": True,
                "assistant_instruction": "Debes llamar check_room_availability con las fechas antes de intentar crear el enlace de pago."
            }
        
        # 🚨 CRITICAL: Require bungalow_type for hospedaje to prevent paying for unavailable room types
        if not bungalow_type:
            logger.error(f"[AVAILABILITY_GATE] Lodging payment blocked - missing bungalow_type. Service: {service_type}")
            return {
                "success": False,
                "link": "",
                "error": "🚨 BLOQUEADO: Para hospedaje/estadía DEBES proporcionar bungalow_type (Familiar, Junior, Habitación, Matrimonial) para verificar que ese tipo específico esté disponible antes de cobrar.",
                "availability_blocked": True,
                "assistant_instruction": "DEBES especificar bungalow_type al llamar create_compraclick_link para hospedaje. Opciones: Familiar, Junior, Habitación, Matrimonial."
            }
        
        # Check room availability
        logger.info(f"[AVAILABILITY_GATE] Checking room availability for {check_in_date} to {check_out_date}")
        availability = await check_room_availability(check_in_date, check_out_date)
        
        if "error" in availability:
            logger.error(f"[AVAILABILITY_GATE] Availability check failed: {availability['error']}")
            return {
                "success": False,
                "link": "",
                "error": f"Error al verificar disponibilidad: {availability['error']}",
                "availability_blocked": True,
                "assistant_instruction": "Hubo un error técnico. Intenta llamar check_room_availability manualmente."
            }
        
        # Check if any rooms are available
        # availability returns: {'bungalow_familiar': 'Available'/'Not Available', 'bungalow_junior': '...', 'habitacion': '...'}
        has_availability = any(status == 'Available' for status in availability.values())
        available_types = [room_type for room_type, status in availability.items() if status == 'Available']
        
        if not has_availability:
            logger.warning(f"[AVAILABILITY_GATE] NO ROOMS AVAILABLE for {check_in_date} to {check_out_date} - Payment link BLOCKED")
            return {
                "success": False,
                "link": "",
                "error": "🚨 NO HAY DISPONIBILIDAD - No se puede crear enlace de pago porque no hay habitaciones disponibles para las fechas solicitadas.",
                "availability_blocked": True,
                "assistant_instruction": f"INFORMA AL CLIENTE: Lamentablemente, en el momento de procesar su pago detectamos que otra persona acaba de reservar la última habitación disponible para las fechas {check_in_date} al {check_out_date}. Le pedimos disculpas por el inconveniente. ¿Le gustaría que busquemos disponibilidad para fechas alternativas usando check_smart_availability?",
                "check_in_date": check_in_date,
                "check_out_date": check_out_date
            }
        
        # 🚨 CRITICAL FIX: If bungalow_type specified, check that SPECIFIC type is available
        # For multi-room bookings, also verify ENOUGH rooms are available
        if bungalow_type:
            # Map bungalow_type to availability dictionary key
            type_mapping = {
                'familiar': 'bungalow_familiar',
                'bungalow familiar': 'bungalow_familiar',
                'junior': 'bungalow_junior',
                'bungalow junior': 'bungalow_junior',
                'habitación': 'habitacion',
                'habitacion': 'habitacion',
                'doble': 'habitacion',
                'matrimonial': 'bungalow_junior',  # Matrimonial uses Junior range
            }
            
            normalized_type = bungalow_type.lower().strip()
            availability_key = type_mapping.get(normalized_type)
            
            if availability_key:
                specific_availability = availability.get(availability_key, 'Not Available')
                
                # 🚨 MULTI-ROOM COUNT CHECK: For multi-room bookings, verify enough rooms available
                not_enough_rooms = False
                available_count = 0
                if num_rooms > 1 and specific_availability == 'Available':
                    try:
                        room_counts = await check_room_availability_counts(check_in_date, check_out_date)
                        if "error" not in room_counts:
                            available_count = room_counts.get(availability_key, 0)
                            if available_count < num_rooms:
                                not_enough_rooms = True
                                logger.warning(f"[AVAILABILITY_GATE] MULTI-ROOM: Need {num_rooms} {bungalow_type} but only {available_count} available")
                    except Exception as e:
                        logger.warning(f"[AVAILABILITY_GATE] Multi-room count check failed: {e}")
                
                if specific_availability != 'Available' or not_enough_rooms:
                    logger.warning(f"[AVAILABILITY_GATE] SPECIFIC TYPE '{bungalow_type}' NOT AVAILABLE for {check_in_date} to {check_out_date} - Payment link BLOCKED")
                    logger.warning(f"[AVAILABILITY_GATE] Available types: {available_types}, Requested: {bungalow_type} ({availability_key})")
                    
                    # 🚨 OCCUPANCY-BASED FILTERING: Suggest single-room AND multi-room alternatives
                    compatible_single_room = []
                    compatible_multi_room = []
                    total_occupancy = adults + (children_6_10 * 0.5) if adults > 0 else 0
                    
                    # Room capacity rules: {type: (min, max)}
                    capacity_rules = {
                        'bungalow_familiar': (5, 8),
                        'bungalow_junior': (2, 8),
                        'habitacion': (2, 4),
                    }
                    type_display_names = {
                        'bungalow_familiar': 'Bungalow Familiar',
                        'bungalow_junior': 'Bungalow Junior',
                        'habitacion': 'Habitación Doble',
                    }
                    
                    if adults > 0:
                        # Check single-room alternatives
                        for avail_type in available_types:
                            if avail_type in capacity_rules:
                                min_cap, max_cap = capacity_rules[avail_type]
                                if min_cap <= total_occupancy <= max_cap:
                                    compatible_single_room.append(type_display_names.get(avail_type, avail_type))
                                    logger.info(f"[AVAILABILITY_GATE] Single-room compatible: {avail_type} (occupancy {total_occupancy} fits {min_cap}-{max_cap})")
                        
                        # 🚨 MULTI-ROOM ALTERNATIVES: If no single-room fits, check multi-room options
                        if not compatible_single_room and total_occupancy > 0:
                            try:
                                room_counts = await check_room_availability_counts(check_in_date, check_out_date)
                                if "error" not in room_counts:
                                    logger.info(f"[AVAILABILITY_GATE] Room counts for multi-room check: {room_counts}")
                                    
                                    for room_type, (min_cap, max_cap) in capacity_rules.items():
                                        available_count = room_counts.get(room_type, 0)
                                        if available_count >= 2:
                                            # Calculate rooms needed: distribute group evenly
                                            for num_rooms_needed in range(2, min(available_count + 1, 5)):  # Max 4 rooms
                                                per_room = total_occupancy / num_rooms_needed
                                                if min_cap <= per_room <= max_cap:
                                                    display_name = type_display_names.get(room_type, room_type)
                                                    multi_option = f"{num_rooms_needed}x {display_name} ({available_count} disponibles)"
                                                    if multi_option not in compatible_multi_room:
                                                        compatible_multi_room.append(multi_option)
                                                        logger.info(f"[AVAILABILITY_GATE] Multi-room compatible: {num_rooms_needed}x {room_type} (per-room {per_room:.1f} fits {min_cap}-{max_cap})")
                                                    break  # Found minimum rooms needed for this type
                            except Exception as e:
                                logger.warning(f"[AVAILABILITY_GATE] Multi-room check failed: {e}")
                        
                        logger.info(f"[AVAILABILITY_GATE] Group occupancy: {total_occupancy}, Single-room options: {compatible_single_room}, Multi-room options: {compatible_multi_room}")
                    
                    # Build instruction based on available alternatives
                    all_alternatives = compatible_single_room + compatible_multi_room
                    if all_alternatives:
                        alternatives_str = ', '.join(all_alternatives)
                        # Special message for multi-room bookings that can't be fulfilled
                        if not_enough_rooms and compatible_single_room:
                            instruction = f"INFORMA AL CLIENTE: Solo hay {available_count} {bungalow_type}(s) disponibles pero usted necesita {num_rooms}. Sin embargo, su grupo de {adults} adultos{' y ' + str(children_6_10) + ' niños' if children_6_10 > 0 else ''} SÍ cabe en UNA SOLA habitación: {', '.join(compatible_single_room)}. El precio se recalculará. ¿Desea cambiar a una sola habitación o buscar otras fechas?"
                        elif not_enough_rooms:
                            instruction = f"INFORMA AL CLIENTE: Solo hay {available_count} {bungalow_type}(s) disponibles pero usted necesita {num_rooms}. Alternativas disponibles: {alternatives_str}. ¿Desea cambiar o buscar otras fechas?"
                        elif compatible_multi_room and not compatible_single_room:
                            instruction = f"INFORMA AL CLIENTE: El tipo {bungalow_type} ya NO está disponible para {check_in_date} al {check_out_date}. Su grupo de {adults} adultos{' y ' + str(children_6_10) + ' niños' if children_6_10 > 0 else ''} NO cabe en una sola habitación, pero SÍ pueden usar MÚLTIPLES habitaciones: {alternatives_str}. El precio se calcula por persona. ¿Desea esta opción o buscar otras fechas?"
                        else:
                            instruction = f"INFORMA AL CLIENTE: El tipo {bungalow_type} ya NO está disponible para {check_in_date} al {check_out_date}. Alternativas para su grupo: {alternatives_str}. El precio puede variar. ¿Desea cambiar o buscar otras fechas?"
                    elif adults > 0:
                        if not_enough_rooms:
                            instruction = f"🚨 Solo hay {available_count} {bungalow_type}(s) disponibles (necesita {num_rooms}) y NO HAY ALTERNATIVAS compatibles. DEBES ofrecer: 1) Buscar otras fechas con check_smart_availability, o 2) Reembolso completo."
                        else:
                            instruction = f"🚨 El tipo {bungalow_type} ya NO está disponible y NO HAY ALTERNATIVAS para su grupo de {adults} adultos{' y ' + str(children_6_10) + ' niños' if children_6_10 > 0 else ''}. DEBES ofrecer: 1) Buscar otras fechas con check_smart_availability, o 2) Reembolso completo."
                    else:
                        instruction = f"INFORMA AL CLIENTE: El tipo {bungalow_type} ya NO está disponible para {check_in_date} al {check_out_date}. Los tipos disponibles son: {', '.join(available_types)}. Pregunta si desea cambiar o buscar otras fechas."
                    
                    return {
                        "success": False,
                        "link": "",
                        "error": f"🚨 NO HAY DISPONIBILIDAD del tipo de habitación solicitado ({bungalow_type}) - No se puede crear enlace de pago.",
                        "availability_blocked": True,
                        "available_types": available_types,
                        "compatible_single_room": compatible_single_room,
                        "compatible_multi_room": compatible_multi_room,
                        "requested_type": bungalow_type,
                        "group_size": {"adults": adults, "children_6_10": children_6_10, "num_rooms": num_rooms},
                        "assistant_instruction": instruction,
                        "check_in_date": check_in_date,
                        "check_out_date": check_out_date
                    }
                logger.info(f"[AVAILABILITY_GATE] Specific type '{bungalow_type}' confirmed available.")
        
        logger.info(f"[AVAILABILITY_GATE] Availability confirmed: {available_types} available. Proceeding with payment link.")

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
    
    # Retry loop: If Playwright browser is missing, install and retry once
    max_attempts = 2
    for attempt in range(1, max_attempts + 1):
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
            logger.exception(f"Failed to initialize browser automation (attempt {attempt}/{max_attempts}): {error_message}")
            
            # Check if it's a Playwright browser missing error
            if "Executable doesn't exist" in error_message or "playwright" in error_message.lower():
                logger.warning("[PLAYWRIGHT_ERROR] Detected missing browser error in CompraClick tool!")
                
                # Attempt auto-recovery and retry
                if attempt < max_attempts and reinstall_playwright_browsers():
                    logger.info("[PLAYWRIGHT_RECOVERY] Browsers reinstalled! Retrying automatically...")
                    continue  # Retry the loop
                elif attempt >= max_attempts:
                    logger.error("[PLAYWRIGHT_RECOVERY] Auto-recovery failed after max attempts!")
                    return {
                        "success": False, 
                        "link": "", 
                        "error": "Browser missing and auto-recovery failed. Please contact support."
                    }
            
            # For non-Playwright errors, return immediately
            return {"success": False, "link": "", "error": f"Browser automation initialization failed: {error_message}"}
    
    # Should not reach here, but just in case
    return {"success": False, "link": "", "error": "Unexpected error in browser automation"}


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
    # 🚨 VALIDATION: Check for empty phone number to prevent 404 errors
    if not phone_number or phone_number.strip() == "" or phone_number == "AUTO":
        logger.error(f"INVALID PHONE NUMBER: phone_number is empty or 'AUTO'. Cannot start retry process.")
        return {
            "success": False,
            "error": "invalid_phone_number",
            "message": "🚨 ERROR: No se proporcionó un número de teléfono válido. El phone_number no puede estar vacío ni ser 'AUTO'. Use el wa_id del cliente para este parámetro."
        }
    
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
