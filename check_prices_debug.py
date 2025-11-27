import sys
import os

# Add current directory to path so we can import app
sys.path.append(os.getcwd())

from app.database_client import get_price_for_date

dates = ["2025-12-30", "2025-12-31", "2026-01-01"]
print("Checking prices for dates:", dates)

for date in dates:
    try:
        price = get_price_for_date(date)
        print(f"Price for {date}: {price}")
    except Exception as e:
        print(f"Error for {date}: {e}")
