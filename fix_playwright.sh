#!/bin/bash
# Emergency Playwright Fix Script
# Run this if Playwright browsers get corrupted or missing

echo "=== EMERGENCY PLAYWRIGHT RECOVERY ==="
echo "Starting at: $(date)"

cd /home/robin/watibot4
source venv/bin/activate

# Force reinstall Playwright and browsers
echo "Reinstalling Playwright..."
pip uninstall -y playwright
pip install playwright==1.44.0

echo "Installing browsers..."
playwright install chromium

# Also install the specific version we need for compatibility
echo "Installing chromium-1117 for compatibility..."
mkdir -p /home/robin/.cache/ms-playwright/chromium-1117
cd /home/robin/.cache/ms-playwright/chromium-1117

# Download if not exists
if [ ! -f "chrome-linux/chrome" ]; then
    wget -q https://playwright.azureedge.net/builds/chromium/1117/chromium-linux.zip -O /tmp/chromium-1117.zip
    unzip -q /tmp/chromium-1117.zip
    rm /tmp/chromium-1117.zip
fi

echo "Testing browser..."
cd /home/robin/watibot4
python -c "
import asyncio
from playwright.async_api import async_playwright

async def test():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        await browser.close()
        print('✅ Recovery successful!')

asyncio.run(test())
"

echo "=== RECOVERY COMPLETE ==="
