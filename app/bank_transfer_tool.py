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
import fcntl
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict
from playwright.async_api import async_playwright, Page
import mysql.connector
from mysql.connector import Error as MySQLError
from .database_client import get_db_connection, execute_with_retry
from .wati_client import update_chat_status, send_wati_message
from dotenv import load_dotenv

# Process-safe lock to prevent concurrent bank sync sessions across workers/processes
BANK_SYNC_LOCK_FILE = "/tmp/bank_sync.lock"
_last_sync_time = None
_sync_cooldown_seconds = 30  # Minimum time between sync operations

class ProcessSafeLock:
    """File-based lock that works across multiple processes/workers"""
    
    def __init__(self, lock_file_path):
        self.lock_file_path = lock_file_path
        self.lock_file = None
    
    async def __aenter__(self):
        """Acquire the lock"""
        logger.info(f"[LOCK] Attempting to acquire process-safe lock: {self.lock_file_path}")
        
        # Try to acquire lock with timeout
        max_wait = 300  # 5 minutes maximum wait
        start_time = time.time()
        
        while time.time() - start_time < max_wait:
            try:
                self.lock_file = open(self.lock_file_path, 'w')
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                logger.info(f"[LOCK] Successfully acquired process-safe lock")
                return self
            except (IOError, OSError) as e:
                if self.lock_file:
                    self.lock_file.close()
                    self.lock_file = None
                # Lock is held by another process, wait and retry
                logger.info(f"[LOCK] Lock held by another process, waiting...")
                await asyncio.sleep(5)  # Wait 5 seconds before retrying
        
        # Timeout reached
        raise Exception(f"Failed to acquire process lock within {max_wait} seconds")
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Release the lock"""
        if self.lock_file:
            try:
                fcntl.flock(self.lock_file.fileno(), fcntl.LOCK_UN)
                self.lock_file.close()
                os.unlink(self.lock_file_path)
                logger.info(f"[LOCK] Released process-safe lock")
            except Exception as e:
                logger.error(f"[LOCK] Error releasing lock: {e}")
            finally:
                self.lock_file = None

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

# --- Playwright Browser Auto-Recovery ---
def check_playwright_browsers():
    """Check if Playwright browsers are installed."""
    import subprocess
    cache_dir = Path.home() / '.cache' / 'ms-playwright'
    chromium_dirs = list(cache_dir.glob('chromium-*')) if cache_dir.exists() else []
    return len(chromium_dirs) > 0

def reinstall_playwright_browsers():
    """Automatically reinstall Playwright browsers if missing."""
    import subprocess
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
    
    # Debug: Check page content before looking for login elements
    logger.info('Checking page content after navigation...')
    page_title = await page.title()
    logger.info(f"Page title: {page_title}")
    
    # Check if page contains expected login elements
    all_inputs = await page.locator('input').all()
    logger.info(f"Found {len(all_inputs)} input elements on page")
    
    # Log all input element IDs/names for debugging
    for i, input_element in enumerate(all_inputs):
        try:
            element_id = await input_element.get_attribute('id')
            element_name = await input_element.get_attribute('name') 
            element_type = await input_element.get_attribute('type')
            logger.info(f"Input {i}: id='{element_id}', name='{element_name}', type='{element_type}'")
        except Exception as e:
            logger.warning(f"Could not inspect input element {i}: {e}")
    
    # Page loaded successfully - no screenshot needed for normal operation
    
    await page.wait_for_selector('#productId', state='visible')
    await page.focus('#productId')
    await page.keyboard.type(username, delay=random.uniform(100, 200))
    await human_delay(page, 500, 1000)

    # Before looking for password field, check if it exists
    logger.info('Looking for password field...')
    pass_field_exists = await page.locator('#pass').count() > 0
    logger.info(f"Password field '#pass' exists: {pass_field_exists}")
    
    if not pass_field_exists:
        # Try alternative selectors for password field
        alternative_selectors = [
            'input[type="password"]',
            '#password',
            '[name="password"]',
            '[name="pass"]',
            'input[name*="pass"]'
        ]
        
        for selector in alternative_selectors:
            try:
                count = await page.locator(selector).count()
                if count > 0:
                    logger.info(f"Found password field with selector: {selector} (count: {count})")
                    # Use this selector instead
                    await page.wait_for_selector(selector, state='visible')
                    await page.focus(selector)
                    await page.keyboard.type(password, delay=random.uniform(100, 200))
                    await human_delay(page, 1000, 2000)
                    break
            except Exception as e:
                logger.debug(f"Selector {selector} failed: {e}")
        else:
            # If no password field found, take screenshot and raise error
            timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
            no_pass_screenshot_path = f"no-password-field-{timestamp}.png"
            await page.screenshot(path=no_pass_screenshot_path, full_page=True)
            logger.error(f"No password field found. Screenshot saved to {no_pass_screenshot_path}")
            raise Exception("Password field not found with any known selector")
    else:
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
            
            # Verify we're actually logged in by checking the URL and page content
            current_url = page.url
            logger.info(f"Post-login URL: {current_url}")
            
            # Check if we're still on the login page (indicates login failure)
            if 'showLogin.go' in current_url:
                logger.warning(f"Still on login page after attempt #{login_retry_count}, login likely failed")
                # Take a screenshot for debugging
                timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
                screenshot_path = f"login-failed-{timestamp}.png"
                await page.screenshot(path=screenshot_path, full_page=True)
                logger.info(f"Login failure screenshot saved to {screenshot_path}")
                raise Exception("Login failed - redirected back to login page")
            
            logger.info('Login successful - navigated away from login page')
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
    
    # Check current URL before attempting account access
    current_url = page.url
    logger.info(f"Current URL before account access: {current_url}")
    
    if 'showLogin.go' in current_url:
        logger.error("ERROR: Still on login page! Session likely expired or invalid.")
        # Take screenshot for debugging
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        screenshot_path = f"session-expired-{timestamp}.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.info(f"Session error screenshot saved to {screenshot_path}")
        raise Exception("Session expired or invalid - redirected back to login page")
    
    await human_click(page, 'button.bel-btn.bel-btn-tertiary.bel-btn-tertiary-active.bel-btn-tertiary-icon.bel-tooltip-generic')
    await human_delay(page, 3000, 4000)

    logger.info('Selecting month...')
    
    # Check URL again before trying to select month
    current_url = page.url
    logger.info(f"Current URL before month selection: {current_url}")
    
    if 'showLogin.go' in current_url:
        logger.error("ERROR: Redirected to login page during account access!")
        # Take screenshot for debugging
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        screenshot_path = f"redirected-to-login-{timestamp}.png"
        await page.screenshot(path=screenshot_path, full_page=True)
        logger.info(f"Redirect error screenshot saved to {screenshot_path}")
        raise Exception("Redirected to login page during account access")
    
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
    Uses global locking to prevent concurrent sessions that could be terminated by the bank.
    """
    global _last_sync_time
    
    async with ProcessSafeLock(BANK_SYNC_LOCK_FILE):
        # Check cooldown period to prevent too frequent sync operations
        now = datetime.now()
        if _last_sync_time and (now - _last_sync_time).total_seconds() < _sync_cooldown_seconds:
            remaining_cooldown = _sync_cooldown_seconds - (now - _last_sync_time).total_seconds()
            logger.info(f"Bank sync in cooldown period. {remaining_cooldown:.1f} seconds remaining.")
            return {
                "success": True, 
                "data": {"message": f"Sync skipped - cooldown active ({remaining_cooldown:.1f}s remaining)"}, 
                "error": None
            }
        
        logger.info("Starting bank transfer sync (with concurrency protection)")
        
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
                    
                    # Update last sync time on success
                    _last_sync_time = datetime.now()
                    
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
                    error_message = str(e)
                    logger.exception(f"Bank transfer sync attempt #{retry_count} failed: {e}")
                    
                    # Check if it's a Playwright browser missing error
                    if "Executable doesn't exist" in error_message or "playwright" in error_message.lower():
                        logger.warning("[PLAYWRIGHT_ERROR] Detected missing browser error!")
                        
                        # Attempt auto-recovery
                        if reinstall_playwright_browsers():
                            logger.info("[PLAYWRIGHT_RECOVERY] Browsers reinstalled! Retrying immediately...")
                            await asyncio.sleep(2)  # Short delay before retry
                            continue
                        else:
                            logger.error("[PLAYWRIGHT_RECOVERY] Auto-recovery failed! Continuing with normal retry...")
                    
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

async def download_bank_csv_standalone() -> str:
    """
    Standalone function to download bank CSV file.
    Used for re-downloading when file is missing during processing.
    Returns the path to the downloaded file.
    """
    logger.info("[CSV_REDOWNLOAD] Starting standalone CSV download...")
    
    username = os.getenv('BAC_USERNAME')
    password = os.getenv('BAC_PASSWORD')
    
    if not username or not password:
        raise Exception("BAC credentials not found in environment variables")
    
    async with async_playwright() as p:
        browser = None
        try:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            )
            page = await context.new_page()
            
            client = await context.new_cdp_session(page)
            await client.send('Page.setDownloadBehavior', {'behavior': 'allow', 'downloadPath': DOWNLOAD_PATH})
            
            file_path = await login_and_download_csv(page, username, password)
            
            # Logout
            try:
                await human_click(page, 'p.bel-typography.bel-typography-p.header-text-color.padding-right-xs', options={'timeout': 10000})
                await page.wait_for_load_state('networkidle', timeout=30000)
            except Exception:
                pass
            
            logger.info(f"[CSV_REDOWNLOAD] Successfully downloaded: {file_path}")
            return file_path
        finally:
            if browser:
                await browser.close()


async def process_csv_and_insert_to_db(file_path: str) -> dict:
    """
    Processes the downloaded CSV file and inserts data into the bac table.
    Uses infinite retry for database operations.
    
    If file is missing mid-retry, triggers re-download and continues.
    """
    # Use mutable container so we can update file_path during retry
    current_file = [file_path]
    
    def _execute_csv_processing():
        conn = get_db_connection()
        try:
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

            with open(current_file[0], mode='r', encoding='latin-1') as csvfile:
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
                            check_query = """SELECT 1 FROM bac WHERE date = %s AND reference = %s AND code = %s AND debit = %s AND credit = %s LIMIT 1"""
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
            logger.info(f"Rows inserted: {rows_inserted}, Rows skipped: {rows_skipped}")
            return {"rows_inserted": rows_inserted, "rows_skipped": rows_skipped}
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    # Custom retry loop that handles FileNotFoundError by re-downloading
    retry_count = 0
    delay = 60  # Start with 60 second delay
    max_delay = 300  # Cap at 5 minutes
    
    while True:
        try:
            result = _execute_csv_processing()
            if retry_count > 0:
                logger.info(f"[CSV_SYNC] Succeeded after {retry_count} retries")
            return result
        except FileNotFoundError as e:
            retry_count += 1
            logger.warning(f"[CSV_SYNC] File not found (attempt #{retry_count}): {current_file[0]}. Triggering re-download...")
            try:
                # Re-download the CSV file
                new_file_path = await download_bank_csv_standalone()
                current_file[0] = new_file_path
                logger.info(f"[CSV_SYNC] Re-downloaded CSV to: {new_file_path}. Retrying processing...")
                # Short delay before retry after successful download
                await asyncio.sleep(2)
            except Exception as download_err:
                logger.error(f"[CSV_SYNC] Re-download failed: {download_err}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                delay = min(delay * 2, max_delay)
        except Exception as err:
            retry_count += 1
            logger.error(f"[CSV_SYNC] Attempt #{retry_count} failed: {err}. Retrying in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, max_delay)

def validate_bank_transfer(slip_date: str, slip_amount: float, booking_amount: float) -> dict:
    """
    Validates a bank transfer by finding a deposit matching the slip details
    and checking if it has enough balance to cover the booking amount.
    Uses infinite retry for database operations.
    
    IMPORTANT: This function only validates - it does NOT update the database.
    Use reserve_bank_transfer() to actually reserve the amount after booking succeeds.

    Args:
        slip_date: The date on the payment slip ('YYYY-MM-DD').
        slip_amount: The total amount on the payment slip.
        booking_amount: The amount of the booking to validate.

    Returns:
        A dictionary with the validation result including transfer_id for reservation.
    """
    # 🚨 VALIDATION: Check for invalid or illogical dates BEFORE entering retry loop
    try:
        from datetime import datetime as dt
        slip_date_obj = dt.strptime(slip_date, "%Y-%m-%d")
        today = dt.now().replace(hour=0, minute=0, second=0, microsecond=0)
        if slip_date_obj > today:
            logger.error(f"ILLOGICAL DATE: slip_date {slip_date} is in the future! OCR likely misread the year.")
            return {
                "success": False, 
                "error": "future_date",
                "message": f"🚨 FECHA ILÓGICA: La fecha del comprobante ({slip_date}) está en el futuro. Esto es imposible para una transferencia ya realizada. ACCIÓN REQUERIDA: (1) Primero intente re-analizar el comprobante con analyze_payment_proof (el OCR probablemente leyó mal el año), (2) Si aún sale incorrecta, pregunte al cliente: '¿En qué fecha realizó la transferencia?' (ejemplo: 13/12/2025)"
            }
    except ValueError as e:
        # 🚨 CRITICAL: Return error immediately for invalid date format - do NOT proceed to infinite retry loop
        logger.error(f"INVALID DATE FORMAT: slip_date '{slip_date}' is not a valid date: {e}")
        return {
            "success": False,
            "error": "invalid_date_format",
            "message": f"🚨 FECHA NO DETECTADA: No se pudo extraer la fecha del comprobante (valor recibido: '{slip_date}'). ACCIÓN REQUERIDA: (1) Primero intente re-analizar el comprobante con analyze_payment_proof para ver si el OCR puede detectar la fecha, (2) Si aún no se detecta, pregunte al cliente: '¿En qué fecha realizó la transferencia?' (ejemplo: 13/12/2025)"
        }
    
    logger.info(f"Attempting to validate booking of {booking_amount} using slip for {slip_amount} on {slip_date}")
    
    def _execute_validation():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)

            # Find an available transfer matching the slip amount and date.
            query = """
                SELECT id, credit, used, codreser FROM bac 
                WHERE credit = %s AND STR_TO_DATE(date, '%d/%m/%Y') = %s AND used < credit
                LIMIT 1
            """
            cursor.execute(query, (slip_amount, slip_date))
            transfer_record = cursor.fetchone()

            # If no available transfer, check for ORPHANED reservations
            # (used >= credit but codreser IS NULL means reserved but booking failed)
            if not transfer_record:
                orphan_query = """
                    SELECT id, credit, used, codreser FROM bac 
                    WHERE credit = %s AND STR_TO_DATE(date, '%d/%m/%Y') = %s 
                    AND used >= credit AND (codreser IS NULL OR codreser = '')
                    LIMIT 1
                """
                cursor.execute(orphan_query, (slip_amount, slip_date))
                orphan_record = cursor.fetchone()
                
                if orphan_record:
                    # Found orphaned reservation - reset it and allow reuse
                    logger.warning(f"[ORPHAN_RECOVERY] Found orphaned reservation ID {orphan_record['id']}: "
                                   f"used={orphan_record['used']}, credit={orphan_record['credit']}, codreser={orphan_record['codreser']}")
                    
                    # Reset the used amount to allow rebooking
                    reset_query = "UPDATE bac SET used = 0 WHERE id = %s"
                    cursor.execute(reset_query, (orphan_record['id'],))
                    conn.commit()
                    logger.info(f"[ORPHAN_RECOVERY] Reset orphaned transfer ID {orphan_record['id']} - now available for booking")
                    
                    # Return as valid with full credit available
                    return {
                        "success": True,
                        "message": f"Pago de {booking_amount:.2f} recuperado y validado exitosamente (reserva previa incompleta).",
                        "transfer_id": orphan_record['id'],
                        "available_balance": float(orphan_record['credit']),
                        "orphan_recovered": True
                    }

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
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    return execute_with_retry(_execute_validation, f"validate_bank_transfer({slip_date}, {slip_amount}, {booking_amount})")


def reserve_bank_transfer(transfer_id: int, booking_amount: float) -> dict:
    """
    Reserves a bank transfer amount by updating the 'used' column.
    Uses infinite retry for database operations.
    This should only be called after successful booking validation and just before the booking HTTP call.

    Args:
        transfer_id: The ID of the bank transfer record to reserve
        booking_amount: The amount to reserve

    Returns:
        A dictionary with the reservation result
    """
    logger.info(f"Attempting to reserve {booking_amount} from transfer ID {transfer_id}")
    
    def _execute_reservation():
        conn = get_db_connection()
        try:
            cursor = conn.cursor(dictionary=True)

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
            conn.commit()

            logger.info(f"Successfully reserved {booking_amount} from transfer ID {transfer_id}. New used amount: {used_amount + booking_amount}")
            return {"success": True, "message": f"Cantidad {booking_amount:.2f} reservada exitosamente"}
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    return execute_with_retry(_execute_reservation, f"reserve_bank_transfer({transfer_id}, {booking_amount})")
