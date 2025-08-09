#!/usr/bin/env python3
import asyncio
import httpx
import json
import sys
import os

# Add the app directory to Python path
sys.path.append('/home/robin/watibot3/app')
from config import WATI_API_URL, WATI_API_KEY

async def fetch_last_messages(wa_id: str, limit: int = 2):
    """Fetch the last N messages for a specific waId from WATI API"""
    url = f"{WATI_API_URL}/api/v1/getMessages/{wa_id}"
    headers = {"Authorization": f"Bearer {WATI_API_KEY}"}
    params = {"pageSize": limit, "pageNumber": 1}
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            print(f"=== Last {limit} messages for waId {wa_id} ===")
            
            if 'data' in data and data['data']:
                messages = data['data'][:limit]  # Get only the requested number
                
                for i, msg in enumerate(messages, 1):
                    print(f"\n--- Message {i} ---")
                    print(f"ID: {msg.get('id', 'N/A')}")
                    print(f"Time: {msg.get('created', 'N/A')}")
                    print(f"Type: {msg.get('type', 'N/A')}")
                    print(f"Owner: {'Us' if msg.get('isOwner') else 'User'}")
                    
                    if msg.get('type') == 'text':
                        print(f"Text: {msg.get('text', 'N/A')}")
                    elif msg.get('type') == 'image':
                        print(f"Image URL: {msg.get('media', {}).get('url', 'N/A')}")
                        print(f"Caption: {msg.get('text', 'N/A')}")
                    elif msg.get('type') == 'file':
                        print(f"File URL: {msg.get('media', {}).get('url', 'N/A')}")
                        print(f"Filename: {msg.get('media', {}).get('filename', 'N/A')}")
                        print(f"Caption: {msg.get('text', 'N/A')}")
                    
                    # Show raw message for debugging
                    print(f"Raw: {json.dumps(msg, indent=2)}")
            else:
                print("No messages found or empty response")
                
        except Exception as e:
            print(f"Error fetching messages: {e}")
            print(f"Response: {response.text if 'response' in locals() else 'No response'}")

if __name__ == "__main__":
    wa_id = "50377371971"
    asyncio.run(fetch_last_messages(wa_id, 3))  # Fetch last 3 messages to be safe
