#!/usr/bin/env python3
"""
Test script to verify the payment workflow fix for bank transfers.
This tests the exact scenario that caused the issue with waId 50373765556.
"""

import asyncio
import sys
import os
sys.path.append('/home/robin/watibot3')

from app.openai_agent import send_to_openai_assistant

async def test_bank_transfer_workflow():
    """Test the bank transfer workflow with pre-analyzed payment data."""
    
    # This simulates the exact message that was sent to the OpenAI assistant
    # for waId 50373765556 that caused the issue
    test_message = "(User sent a payment receipt: bank_transfer, Amount: 236.0, Name: None, Transaction ID: 3376154855, Time: None. The system will analyze this automatically.)"
    
    print("Testing bank transfer workflow fix...")
    print(f"Test message: {test_message}")
    print("-" * 80)
    
    try:
        # Send to OpenAI assistant
        response = await send_to_openai_assistant("test_wa_id", test_message)
        
        print(f"Assistant response: {response}")
        
        # Check if the response indicates the fix worked
        if "can't visualize" in response.lower() or "no se pudo visualizar" in response.lower():
            print("\n❌ ISSUE STILL EXISTS: Assistant still says it can't visualize the payment proof")
            return False
        elif "transferencia" in response.lower() or "pago" in response.lower():
            print("\n✅ FIX APPEARS TO WORK: Assistant is processing the bank transfer correctly")
            return True
        else:
            print(f"\n⚠️  UNCLEAR RESULT: Assistant response doesn't clearly indicate success or failure")
            return False
            
    except Exception as e:
        print(f"\n❌ ERROR: {str(e)}")
        return False

async def main():
    """Main test function."""
    print("=" * 80)
    print("TESTING PAYMENT WORKFLOW FIX")
    print("=" * 80)
    
    bank_result = await test_bank_transfer_workflow()
    
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print(f"Bank Transfer Test: {'✅ PASSED' if bank_result else '❌ FAILED'}")
    
    if bank_result:
        print("\n🎉 TEST PASSED - FIX APPEARS TO BE WORKING!")
    else:
        print("\n⚠️  TEST FAILED - FIX MAY NEED ADDITIONAL WORK")
    
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(main())
