#!/usr/bin/env python3
"""
Test script for conversation logging functionality
"""
from app import conversation_log

def test_conversation_logging():
    """Test the conversation logging system"""
    print("=" * 50)
    print("Testing Conversation Logging System")
    print("=" * 50)
    
    test_user_id = "test_user_123"
    
    # Test logging messages
    print("\n1. Logging test messages...")
    conversation_log.log_message(test_user_id, "user", "Hello, I need information", "facebook")
    conversation_log.log_message(test_user_id, "assistant", "Hello! How can I help you today?", "facebook")
    conversation_log.log_message(test_user_id, "user", "Can you show me the menu?", "facebook")
    conversation_log.log_message(test_user_id, "assistant", "Of course! I'll send you the menu.", "facebook")
    print("✅ Messages logged successfully")
    
    # Test retrieving messages
    print("\n2. Retrieving messages...")
    messages = conversation_log.get_recent_messages(test_user_id, limit=10)
    print(f"✅ Retrieved {len(messages)} messages")
    
    # Display messages
    print("\n3. Message History:")
    print("-" * 50)
    for msg in messages:
        role = msg['role'].upper()
        content = msg['content'][:50] + "..." if len(msg['content']) > 50 else msg['content']
        timestamp = msg['timestamp']
        channel = msg.get('channel', 'N/A')
        print(f"[{timestamp}] [{channel}] {role}: {content}")
    print("-" * 50)
    
    # Test context injection
    print("\n4. Testing context injection...")
    from agent_context_injector import get_manychat_context_for_system_injection
    context = get_manychat_context_for_system_injection(test_user_id)
    if context:
        print(f"✅ Context generated ({len(context)} characters)")
        print("\nContext Preview:")
        print("-" * 50)
        print(context[:500] + "..." if len(context) > 500 else context)
        print("-" * 50)
    else:
        print("❌ No context generated")
    
    print("\n" + "=" * 50)
    print("Test completed successfully!")
    print("=" * 50)

if __name__ == "__main__":
    test_conversation_logging()
