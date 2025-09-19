#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenAI Thread Inspector
Inspects messages in an OpenAI thread to see injected context
"""

import os
import sys
from datetime import datetime

# Add the app directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

try:
    from openai import OpenAI
    from app import config
except ImportError as e:
    print("Error importing modules: {}".format(e))
    sys.exit(1)

def inspect_thread(thread_id):
    """Inspect messages in an OpenAI thread"""
    
    # Initialize OpenAI client using config
    if not config.OPENAI_API_KEY:
        print("❌ ERROR: OPENAI_API_KEY not found in config")
        return False
    
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    
    try:
        print("="*60)
        print("OPENAI THREAD INSPECTION")
        print("="*60)
        print("Thread ID: {}".format(thread_id))
        print("")
        
        # With Responses API, conversation items are not directly accessible
        print(f"Note: Conversation inspection simplified with Responses API")
        print(f"Conversation ID: {thread_id}")
        return
        # Filter only message items
        messages = [item for item in items_response.data if item.type == "message"]
        
        print("Total messages in conversation: {}".format(len(messages)))
        print("")
        
        # Focus on July 31st context injection to see what was actually injected
        print("INVESTIGATING JULY 31ST CONTEXT INJECTION FOR LINDA")
        print("Was July 25th booking conversation injected when Linda contacted assistant?")
        print("="*60)
        
        all_dates = set()
        july_31_injections = []
        july_25_mentions = []
        
        print("\nALL MESSAGE TIMESTAMPS IN THREAD:")
        print("-" * 40)
        
        for i, message in enumerate(messages):
            # Convert timestamp to readable format
            timestamp = datetime.fromtimestamp(message.created_at)
            timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            date_only = timestamp_str[:10]
            all_dates.add(date_only)
            
            print("Message #{}: {}".format(i+1, timestamp_str))
            
            # Check for July 31st context injections (when Linda contacted assistant)
            if timestamp_str.startswith("2025-07-31"):
                july_31_injections.append((i, message, timestamp_str))
                print("  *** JULY 31ST MESSAGE FOUND ***")
            
            # Check for any mentions of July 25th in message content
            content = ""
            if hasattr(message, 'content') and message.content:
                for content_item in message.content:
                    if content_item.type == "input_text":
                        content += content_item.text
                    elif content_item.type == "output_text":
                        content += content_item.text
                    elif hasattr(content_item, 'text'):
                        content += str(content_item.text)
            
            if "2025-07-25" in content or "July 25" in content:
                july_25_mentions.append((i, message, timestamp_str))
                print("  *** JULY 25TH MENTIONED IN CONTENT ***")
        
        print("\nUNIQUE DATES IN THREAD: {}".format(sorted(list(all_dates))))
        print("JULY 31ST INJECTIONS FOUND: {}".format(len(july_31_injections)))
        print("JULY 25TH MENTIONS FOUND: {}".format(len(july_25_mentions)))
        
        # Now analyze July 31st context injections specifically
        print("\n" + "="*60)
        print("ANALYZING JULY 31ST CONTEXT INJECTIONS")
        print("Looking for July 25th booking context in July 31st injections...")
        print("="*60)
        
        found_context_injection = False
        
        if july_31_injections:
            print("\nFOUND {} JULY 31ST MESSAGES:".format(len(july_31_injections)))
            for idx, (i, message, timestamp_str) in enumerate(july_31_injections):
                print("\n" + "-"*50)
                print("JULY 31ST MESSAGE #{} - {} ({})".format(idx+1, timestamp_str, message.role.upper()))
                print("ID: {}".format(message.id))
                
                # Extract content
                content = ""
                if hasattr(message, 'content') and message.content:
                    for content_item in message.content:
                        if hasattr(content_item, 'text') and hasattr(content_item.text, 'value'):
                            content += content_item.text.value
                
                # Check if this is an agent context injection
                is_system = message.role == "system"
                contains_agent_context = "agent" in content.lower() or "conversation history" in content.lower()
                contains_july_25 = "2025-07-25" in content or "july 25" in content.lower()
                
                if is_system or contains_agent_context:
                    print("*** POTENTIAL AGENT CONTEXT INJECTION ***")
                    found_context_injection = True
                
                if contains_july_25:
                    print("*** CONTAINS JULY 25TH REFERENCES ***")
                
                print("CONTENT:")
                print("-"*40)
                if content:
                    # Show first 500 chars and indicate if truncated
                    display_content = content[:500]
                    if len(content) > 500:
                        display_content += "\n... (truncated)"
                    print(display_content)
                else:
                    print("(No text content found)")
                print("-"*40)
        else:
            print("\n❌ NO JULY 31ST MESSAGES FOUND IN THREAD")
            print("This suggests Linda's July 31st message may not have triggered context injection")
        
        # Check if we found any July 25th mentions
        if july_25_mentions:
            print("\n" + "="*60)
            print("JULY 25TH MENTIONS FOUND IN THREAD:")
            for idx, (i, message, timestamp_str) in enumerate(july_25_mentions):
                print("Message #{} - {} - Contains July 25th reference".format(i+1, timestamp_str))
        
        # Continue with timeframe analysis for completeness
        target_dates = ["2025-07-31"]
        
        for i, message in enumerate(messages.data):
            # Convert timestamp to readable format
            timestamp = datetime.fromtimestamp(message.created_at)
            timestamp_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
            
            # Check if this message is from July 31st
            in_timeframe = timestamp_str.startswith("2025-07-31")
            
            # Check if this is a system message (likely agent context injection)
            is_system = message.role == "system"
            
            # Check if this contains agent context keywords
            content = ""
            if hasattr(message, 'content') and message.content:
                for content_item in message.content:
                    if hasattr(content_item, 'text') and hasattr(content_item.text, 'value'):
                        content += content_item.text.value
            
            contains_agent_context = ("CONVERSACIONES CON AGENTES" in content or 
                                    "agentes humanos" in content or
                                    "reserva" in content.lower() or
                                    "booking" in content.lower())
            
            # Print relevant messages
            if in_timeframe or is_system or contains_agent_context:
                print("MESSAGE #{} - {} ({})".format(i+1, timestamp_str, message.role.upper()))
                print("ID: {}".format(message.id))
                
                if contains_agent_context:
                    found_context_injection = True
                    print("*** AGENT CONTEXT INJECTION FOUND ***")
                
                if content:
                    print("CONTENT:")
                    print("-" * 40)
                    print(content[:2000])  # First 2000 chars
                    if len(content) > 2000:
                        print("... (truncated)")
                    print("-" * 40)
                else:
                    print("CONTENT: (empty or no text content)")
                
                print("")
        
        if not found_context_injection:
            print("❌ NO AGENT CONTEXT INJECTION FOUND in thread")
        else:
            print("✅ AGENT CONTEXT INJECTION FOUND in thread")
            
        print("="*60)
        print("INSPECTION COMPLETE")
        print("="*60)
        
    except Exception as e:
        print("❌ ERROR inspecting thread: {}".format(str(e)))
        return False
    
    return True

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python inspect_thread.py <thread_id>")
        sys.exit(1)
    
    thread_id = sys.argv[1]
    inspect_thread(thread_id)
