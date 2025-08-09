#!/usr/bin/env python3
import asyncio
import httpx
import json
import sys

sys.path.append('/home/robin/watibot3/app')
from config import WATI_API_URL, WATI_API_KEY

async def debug_fetch_messages(wa_id: str):
    """Debug WATI API call"""
    url = f"{WATI_API_URL}/api/v1/getMessages/{wa_id}"
    headers = {"Authorization": f"Bearer {WATI_API_KEY}"}
    params = {"pageSize": 5, "pageNumber": 1}
    
    print(f"URL: {url}")
    print(f"Headers: Authorization: Bearer {WATI_API_KEY[:10]}...")
    print(f"Params: {params}")
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            print(f"Status Code: {response.status_code}")
            print(f"Response Headers: {dict(response.headers)}")
            print(f"Response Text: {response.text}")
            
            if response.status_code == 200:
                data = response.json()
                print(f"Parsed JSON: {json.dumps(data, indent=2)}")
            else:
                print(f"HTTP Error: {response.status_code}")
                
        except Exception as e:
            print(f"Exception: {e}")

if __name__ == "__main__":
    wa_id = "50377371971"
    asyncio.run(debug_fetch_messages(wa_id))
