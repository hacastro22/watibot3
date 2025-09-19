#!/usr/bin/env python3
"""
Test script to verify the corrected Responses API implementation in watibot4.
"""
import asyncio
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

from app.openai_agent import get_openai_response

async def test_responses_api():
    """Test the corrected Responses API implementation."""
    print("🧪 Testing corrected Responses API implementation...")
    
    try:
        # Test 1: Simple greeting without tools
        print("\n📝 Test 1: Simple greeting")
        response, conversation_id = await get_openai_response(
            message="Hola, ¿cómo están?",
            phone_number="50312345678"
        )
        print(f"✅ Response: {response[:100]}...")
        print(f"✅ Conversation ID: {conversation_id}")
        
        # Test 2: Request that should trigger a tool call
        print("\n📝 Test 2: Availability check (should trigger tool)")
        response, conversation_id = await get_openai_response(
            message="¿Tienen disponibilidad para el 1 de diciembre?",
            phone_number="50312345678"
        )
        print(f"✅ Response: {response[:100]}...")
        print(f"✅ Conversation ID: {conversation_id}")
        
        print("\n🎉 All tests completed successfully!")
        return True
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = asyncio.run(test_responses_api())
    sys.exit(0 if success else 1)
