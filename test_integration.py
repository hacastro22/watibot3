#!/usr/bin/env python3
"""
Quick integration test for OpenAI Responses API
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.openai_agent import get_openai_response

async def test_integration():
    try:
        print('Testing OpenAI Responses API integration...')
        response, thread_id = await get_openai_response(
            message='Hello, this is a test message',
            phone_number='test_123',
            channel='wati'
        )
        print(f'✅ Success! Response: {response[:100]}...')
        print(f'✅ Thread ID: {thread_id}')
        return True
    except Exception as e:
        print(f'❌ Error: {e}')
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_integration())
    print(f'Test result: {"PASSED" if success else "FAILED"}')
    sys.exit(0 if success else 1)
