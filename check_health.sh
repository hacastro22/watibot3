#!/bin/bash
# watibot4 Health Check Script
# Verifies all components are working correctly

echo "=== WATIBOT4 HEALTH CHECK ==="
echo "Timestamp: $(date)"

cd /home/robin/watibot4
source venv/bin/activate

# Check service status
echo "📊 SERVICE STATUS"
sudo systemctl is-active watibot4.service && echo "✅ Service is active" || echo "❌ Service is not active"

# Check Playwright
echo "📊 PLAYWRIGHT STATUS"
python -c "
try:
    import playwright
    print(f'✅ Playwright version: {playwright.__version__}')
except ImportError:
    print('❌ Playwright not installed')
    exit(1)
"

# Check browsers
echo "📊 BROWSER STATUS"
BROWSERS=$(ls /home/robin/.cache/ms-playwright/chromium-*/chrome-linux/chrome 2>/dev/null | wc -l)
echo "🌐 Available browsers: $BROWSERS"
for browser in /home/robin/.cache/ms-playwright/chromium-*/chrome-linux/chrome; do
    if [ -f "$browser" ]; then
        VERSION=$(basename $(dirname $(dirname "$browser")))
        echo "  ✅ $VERSION"
    fi
done

# Test browser launch
echo "📊 BROWSER LAUNCH TEST"
python -c "
import asyncio
from playwright.async_api import async_playwright
import sys

async def test_browser():
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await browser.close()
            print('✅ Browser launch successful')
            return True
    except Exception as e:
        print(f'❌ Browser launch failed: {e}')
        return False

result = asyncio.run(test_browser())
sys.exit(0 if result else 1)
"

BROWSER_TEST=$?
if [ $BROWSER_TEST -eq 0 ]; then
    echo "✅ All systems operational"
else
    echo "❌ Browser test failed - run ./fix_playwright.sh"
fi

# Disk space check
echo "📊 DISK USAGE"
df -h / | tail -1 | awk '{print "  Disk usage: " $3 "/" $2 " (" $5 ")"}'

echo "=== HEALTH CHECK COMPLETE ==="
