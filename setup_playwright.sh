#!/bin/bash
# Playwright Browser Setup Script for watibot4
# This script ensures Playwright browsers are always available after service restarts
# VERSION-AGNOSTIC: Works with any Playwright version

set -e  # Exit on any error

echo "=== PLAYWRIGHT BROWSER SETUP ==="
echo "Starting at: $(date)"

# Change to watibot4 directory
cd /home/robin/watibot4

# Activate virtual environment
echo "Activating virtual environment..."
source venv/bin/activate

# Check if Playwright is installed
echo "Checking Playwright installation..."
if ! python -c "import playwright" 2>/dev/null; then
    echo "❌ Playwright not found! Installing..."
    pip install playwright
fi

# Get current Playwright version
PLAYWRIGHT_VERSION=$(python -c "import playwright; print(playwright.__version__)" 2>/dev/null || echo "unknown")
echo "Playwright version: $PLAYWRIGHT_VERSION"

# SIMPLIFIED APPROACH: Always run playwright install chromium
# This command is smart enough to:
# 1. Skip if browsers already exist for this version
# 2. Download the correct version for the installed Playwright
# 3. Handle version upgrades automatically

echo "Ensuring Chromium browser is installed..."
playwright install chromium --with-deps 2>&1 | grep -v "^$" || true

echo "Installing ffmpeg (required for some features)..."
playwright install ffmpeg 2>&1 | grep -v "^$" || true

# Verify installation by checking if any chromium directory exists
BROWSER_BASE="/home/robin/.cache/ms-playwright"
if ls -d ${BROWSER_BASE}/chromium-* 1> /dev/null 2>&1; then
    LATEST_CHROMIUM=$(ls -t ${BROWSER_BASE}/chromium-*/chrome-linux/chrome 2>/dev/null | head -1)
    if [ -f "$LATEST_CHROMIUM" ] && [ -x "$LATEST_CHROMIUM" ]; then
        echo "✅ Browser found at: $LATEST_CHROMIUM"
    else
        echo "❌ Browser executable not found or not executable!"
        echo "Attempting force reinstall..."
        playwright install --force chromium
    fi
else
    echo "❌ No Chromium installation found!"
    echo "Attempting installation..."
    playwright install chromium
fi

# Test Playwright import and browser launch capability
echo "Testing Playwright functionality..."
python -c "
import asyncio
from playwright.async_api import async_playwright

async def test_browser():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await browser.close()
            print('✅ Playwright browser test successful')
    except Exception as e:
        print(f'❌ Playwright browser test failed: {e}')
        raise

asyncio.run(test_browser())
"

echo "=== PLAYWRIGHT SETUP COMPLETE ==="
echo "Finished at: $(date)"
