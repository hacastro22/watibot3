import httpx
import asyncio
import os
from dotenv import load_dotenv

async def test_wati_pagination():
    """
    Calls the WATI getMessages API endpoint for a specific waId,
    iterating through all pages to retrieve the full conversation history
    and prints the total number of messages found.
    """
    # Load environment variables from the .env file in the project root
    load_dotenv('/home/robin/watibot3/.env')

    WATI_API_URL = os.getenv("WATI_API_URL")
    WATI_API_KEY = os.getenv("WATI_API_KEY")
    WA_ID_TO_TEST = "50376973593"
    PAGE_SIZE = 500  # Request a large number of messages per page for efficiency

    if not all([WATI_API_URL, WATI_API_KEY]):
        print("Error: WATI_API_URL and WATI_API_KEY must be set in the .env file.")
        return

    headers = {"Authorization": f"Bearer {WATI_API_KEY}"}
    all_messages = []
    page_number = 1

    print(f"--- Starting WATI API Paginated History Test ---")
    print(f"Testing for waId: {WA_ID_TO_TEST}")

    try:
        async with httpx.AsyncClient() as client:
            while True:
                print(f"Fetching page {page_number} with page size {PAGE_SIZE}...")
                # Note: WATI expects parameters in the URL query string for GET requests
                params = {"pageSize": PAGE_SIZE, "pageNumber": page_number}
                test_url = f"{WATI_API_URL}/api/v1/getMessages/{WA_ID_TO_TEST}"
                
                response = await client.get(test_url, headers=headers, params=params, timeout=60)

                if response.status_code != 200:
                    print(f"\n*** TEST RESULT: FAILURE ***")
                    print(f"API responded with status {response.status_code} on page {page_number}.")
                    print(f"Response content: {response.text}")
                    return

                messages_on_page = response.json()
                if not messages_on_page: # Stop when a page returns no messages
                    print(f"Page {page_number} is empty. Reached end of history.")
                    break

                all_messages.extend(messages_on_page)
                print(f"Found {len(messages_on_page)} messages on this page. Total so far: {len(all_messages)}.")
                page_number += 1
        
        total_count = len(all_messages)
        print(f"\n*** TEST RESULT: SUCCESS ***")
        print(f"The API returned a total of {total_count} messages across {page_number - 1} page(s).")
        print("CONCLUSION: The WATI API supports pagination and we can retrieve the full conversation history.")

    except Exception as e:
        print(f"An exception occurred: {e}")

    print(f"--- Test Complete ---")

if __name__ == "__main__":
    asyncio.run(test_wati_pagination())
