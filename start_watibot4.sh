#!/bin/bash
# watibot4 Startup Script with Playwright Auto-Setup
# This script runs before the main application to ensure all dependencies are ready

set -e

echo "=== WATIBOT4 STARTUP SEQUENCE ==="
echo "Starting at: $(date)"

# Change to watibot4 directory
cd /home/robin/watibot4

# Run Playwright setup
echo "Running Playwright setup..."
./setup_playwright.sh

# Verify all components before starting main app
echo "Pre-flight checks..."

# Check if all required packages are available
echo "Checking core dependencies..."
source venv/bin/activate

python -c "
import sys
required_modules = [
    'fastapi', 'uvicorn', 'playwright', 'openai', 
    'mysql.connector', 'httpx', 'aiohttp', 'pandas'
]

missing = []
for module in required_modules:
    try:
        __import__(module)
        print(f'✅ {module}')
    except ImportError:
        missing.append(module)
        print(f'❌ {module}')

if missing:
    print(f'Missing modules: {missing}')
    sys.exit(1)
else:
    print('✅ All required modules available')
"

echo "=== PRE-FLIGHT COMPLETE - STARTING MAIN APPLICATION ==="

# Start the main application with 6 workers for concurrent webhook handling
# CRITICAL: Multiple workers prevent webhook blocking when DB operations are in progress
# Each worker can handle webhooks independently, so if one is busy with SQLite writes,
# others can still accept new webhooks from WATI
exec /home/robin/watibot4/venv/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8006 --workers 6
