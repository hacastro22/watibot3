#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
from datetime import datetime
import os
import sys
import time
import sqlite3

# Add the app directory to Python path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
import config

def compare_webhook_vs_api_messages(wa_id, pages=3):
    print("[ANALYSIS] Comparing webhook-received vs WATI API messages for waId: " + wa_id)
    print("=" * 80)
    
    # Step 1: Get all messages from WATI API
    api_messages = fetch_wati_api_messages(wa_id, pages)
    
    # Step 2: Get webhook-received messages from OpenAI thread/database  
    webhook_messages = get_webhook_received_messages(wa_id)
    
    # Step 3: Compare and find customer messages sent to agents
    find_customer_to_agent_messages(api_messages, webhook_messages, wa_id)

def fetch_wati_api_messages(wa_id, pages):
    print("\n[FETCH] Getting messages from WATI API...")
    all_messages = []
    page_number = 1
    PAGE_SIZE = 100
    
    while page_number <= pages:
        try:
            print("[FETCH] Page " + str(page_number) + "...")
            wati_url = config.WATI_API_URL + "/api/v1/getMessages/" + wa_id + "?pageSize=" + str(PAGE_SIZE) + "&pageNumber=" + str(page_number)
            headers = {"Authorization": "Bearer " + config.WATI_API_KEY}
            response = requests.get(wati_url, headers=headers, timeout=30)
            
            if response.status_code == 429:
                print("[WAIT] Rate limit hit. Waiting 30 seconds...")
                time.sleep(30)
                continue
            
            response.raise_for_status()
            response_data = response.json()
            
            messages_data = response_data.get("messages", {})
            if not isinstance(messages_data, dict):
                messages = []
            else:
                messages = messages_data.get("items", [])
            
            if not messages:
                print("[INFO] No more messages found on page " + str(page_number))
                break
            
            print("[SUCCESS] Found " + str(len(messages)) + " messages on page " + str(page_number))
            all_messages.extend(messages)
            page_number += 1
            time.sleep(1)
            
        except Exception as e:
            print("[ERROR] Error on page " + str(page_number) + ": " + str(e))
            break
    
    print("[STATS] Total API messages: " + str(len(all_messages)))
    return all_messages

def get_webhook_received_messages(wa_id):
    print("\n[WEBHOOK] Getting webhook-received messages from database...")
    
    # Check if thread_store.db exists and get webhook messages
    db_path = "thread_store.db"
    webhook_messages = []
    
    try:
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # Get the OpenAI thread for this waId
            cursor.execute("SELECT thread_id FROM threads WHERE wa_id = ?", (wa_id,))
            thread_result = cursor.fetchone()
            
            if thread_result:
                thread_id = thread_result[0]
                print("[WEBHOOK] Found OpenAI thread: " + thread_id)
                
                # Try to get messages from OpenAI API
                webhook_messages = get_openai_thread_messages(thread_id)
            else:
                print("[WEBHOOK] No OpenAI thread found for waId: " + wa_id)
            
            conn.close()
        else:
            print("[WEBHOOK] No thread_store.db found")
            
    except Exception as e:
        print("[ERROR] Error accessing database: " + str(e))
    
    print("[STATS] Webhook messages found: " + str(len(webhook_messages)))
    return webhook_messages

def get_openai_thread_messages(thread_id):
    print("[OPENAI] Fetching messages from OpenAI thread: " + thread_id)
    
    try:
        import openai
        openai.api_key = config.OPENAI_API_KEY
        
        # Get all messages from the thread
        response = openai.beta.threads.messages.list(thread_id=thread_id, limit=100)
        messages = response.data
        
        # Extract user messages (those sent via webhook)
        user_messages = []
        for msg in messages:
            if msg.role == 'user':
                # Extract text content
                content = ""
                for content_block in msg.content:
                    if content_block.type == 'text':
                        content += content_block.text.value
                
                user_messages.append({
                    'content': content,
                    'created_at': msg.created_at
                })
        
        print("[OPENAI] Found " + str(len(user_messages)) + " user messages in thread")
        return user_messages
        
    except Exception as e:
        print("[ERROR] Error fetching OpenAI thread messages: " + str(e))
        return []

def get_assistant_responses_from_thread(wa_id):
    print("[ASSISTANT] Getting assistant responses from OpenAI thread...")
    
    # Get thread ID
    db_path = "thread_store.db"
    assistant_messages = []
    
    try:
        if os.path.exists(db_path):
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            cursor.execute("SELECT thread_id FROM threads WHERE wa_id = ?", (wa_id,))
            thread_result = cursor.fetchone()
            
            if thread_result:
                thread_id = thread_result[0]
                
                import openai
                openai.api_key = config.OPENAI_API_KEY
                
                # Get all messages from the thread
                response = openai.beta.threads.messages.list(thread_id=thread_id, limit=100)
                messages = response.data
                
                # Extract assistant messages
                for msg in messages:
                    if msg.role == 'assistant':
                        # Extract text content
                        content = ""
                        for content_block in msg.content:
                            if content_block.type == 'text':
                                content += content_block.text.value
                        
                        assistant_messages.append({
                            'content': content,
                            'created_at': msg.created_at
                        })
                
                print("[ASSISTANT] Found " + str(len(assistant_messages)) + " assistant messages")
            
            conn.close()
            
    except Exception as e:
        print("[ERROR] Error fetching assistant messages: " + str(e))
    
    return assistant_messages

def find_customer_to_agent_messages(api_messages, webhook_messages, wa_id):
    print("\n" + "=" * 80)
    print("[COMPARISON] Finding customer messages sent to agents (not webhook)")
    print("=" * 80)
    
    # Get ALL webhook interactions (received + sent)
    webhook_contents = set()
    assistant_responses = get_assistant_responses_from_thread(wa_id)
    
    # Add webhook-received customer messages
    for webhook_msg in webhook_messages:
        content = normalize_message_content(webhook_msg['content'])
        webhook_contents.add(content)
    
    # Add assistant responses sent back to customer
    for assistant_msg in assistant_responses:
        content = normalize_message_content(assistant_msg['content'])
        webhook_contents.add(content)
    
    print("[WEBHOOK] Total webhook interactions (received + sent): " + str(len(webhook_contents)))
    
    # Filter API messages to exclude obvious non-customer messages
    potential_messages = []
    for msg in api_messages:
        text = msg.get('text', '')
        if msg.get('eventType') == 'message' and text and text.strip():
            potential_messages.append(msg)
    
    print("[FILTER] Total API messages with text: " + str(len(potential_messages)))
    
    # Find messages NOT in webhook interactions
    customer_to_agent_messages = []
    
    for api_msg in potential_messages:
        api_content = normalize_message_content(api_msg.get('text', ''))
        
        # Check if this message was part of webhook interactions
        found_in_webhook = False
        for webhook_content in webhook_contents:
            if messages_are_similar(api_content, webhook_content):
                found_in_webhook = True
                break
        
        if not found_in_webhook and api_content.strip():
            customer_to_agent_messages.append(api_msg)
    
    # Display results
    print("\n[RESULTS] Customer-to-agent messages found: " + str(len(customer_to_agent_messages)))
    print("-" * 60)
    
    if customer_to_agent_messages:
        print("🎯 SUCCESS! Found customer messages sent to human agents:")
        
        for i, msg in enumerate(customer_to_agent_messages[:10], 1):  # Show first 10
            try:
                # Parse timestamp
                created = msg.get('created', '')
                try:
                    if created:
                        ts_str = created.replace('Z', '+00:00')
                        if '.' in ts_str and '+' in ts_str:
                            base_part, tz_part = ts_str.split('+')
                            if '.' in base_part:
                                main_part, frac_part = base_part.split('.')
                                frac_part = frac_part.ljust(6, '0')[:6]
                                ts_str = main_part + '.' + frac_part + '+' + tz_part
                        parsed_time = datetime.fromisoformat(ts_str)
                        formatted_time = parsed_time.strftime('%Y-%m-%d %H:%M:%S')
                    else:
                        formatted_time = 'N/A'
                except:
                    formatted_time = 'PARSE_ERROR'
                
                text = msg.get('text', 'N/A')
                if len(text) > 150:
                    text = text[:150] + '...'
                
                assigned_id = msg.get('assignedId', 'NULL')
                operator_name = msg.get('operatorName', 'NULL')
                event_type = msg.get('eventType', 'NULL')
                
                print("\n[" + str(i) + "] " + formatted_time)
                print("    Text: \"" + text + "\"")
                print("    assignedId: " + str(assigned_id))
                print("    operatorName: '" + str(operator_name) + "'")
                print("    eventType: " + str(event_type))
                print("    ✅ NOT FOUND IN WEBHOOK - Likely sent to human agent")
                
            except Exception as e:
                print("[ERROR] Error processing message " + str(i) + ": " + str(e))
        
        if len(customer_to_agent_messages) > 10:
            print("\n... and " + str(len(customer_to_agent_messages) - 10) + " more messages")
    else:
        print("❌ No customer-to-agent messages found with this method.")
        print("   All potential customer messages were also found in webhook.")
    
    # Summary
    print("\n" + "=" * 80)
    print("[SUMMARY] MESSAGE CLASSIFICATION")
    print("=" * 80)
    print("Total API messages analyzed: " + str(len(api_messages)))
    print("API messages with text: " + str(len(potential_messages)))
    print("Webhook interactions (received + sent): " + str(len(webhook_contents)))
    print("Customer-to-agent messages: " + str(len(customer_to_agent_messages)))
    
    if customer_to_agent_messages:
        print("\n🎯 NEXT STEPS:")
        print("✅ Implement injection of these " + str(len(customer_to_agent_messages)) + " customer-to-agent messages")
        print("✅ Add corresponding human agent responses for full context")
        print("✅ Format as system messages in OpenAI threads")
    
    return customer_to_agent_messages

def is_potential_customer_message(msg):
    """Check if message could be from a customer"""
    event_type = msg.get('eventType', '')
    operator_name = msg.get('operatorName', '')
    
    # Must be a message event
    if event_type != 'message':
        return False
    
    # Exclude human agent messages (have real names)
    if operator_name and operator_name not in ['NULL', 'Bot ', 'None', '']:
        return False
    
    # Exclude assistant messages (operatorName is 'Bot ' or 'None')
    # This is the key fix - we need to identify actual customer messages, not assistant responses
    if operator_name in ['Bot ', 'None']:
        return False
    
    # Must have text content
    text = msg.get('text', '')
    if not text or text.strip() == '':
        return False
    
    # At this point, we should have messages that are neither human agent nor assistant
    # These could be genuine customer messages
    return True

def normalize_message_content(content):
    """Normalize message content for comparison"""
    if not content:
        return ""
    
    # Remove extra whitespace, convert to lowercase
    normalized = ' '.join(content.strip().lower().split())
    
    # Remove common prefixes that might be added by webhook processing
    prefixes_to_remove = [
        '(user sent a voice note:',
        '(customer is replying to:',
        '(user sent an image',
    ]
    
    for prefix in prefixes_to_remove:
        if normalized.startswith(prefix.lower()):
            # Find the closing parenthesis and remove the prefix
            paren_count = 0
            end_index = 0
            for i, char in enumerate(normalized):
                if char == '(':
                    paren_count += 1
                elif char == ')':
                    paren_count -= 1
                    if paren_count == 0:
                        end_index = i + 1
                        break
            if end_index > 0:
                normalized = normalized[end_index:].strip()
            break
    
    return normalized

def messages_are_similar(content1, content2, threshold=0.9):
    """Check if two message contents are similar enough to be the same message"""
    if not content1 or not content2:
        return False
    
    # Exact match
    if content1 == content2:
        return True
    
    # Check if one is a substring of the other (for truncated messages)
    if content1 in content2 or content2 in content1:
        return True
    
    # Simple similarity check based on word overlap
    words1 = set(content1.split())
    words2 = set(content2.split())
    
    if not words1 or not words2:
        return False
    
    intersection = len(words1.intersection(words2))
    union = len(words1.union(words2))
    
    if union == 0:
        return False
    
    similarity = intersection / union
    return similarity >= threshold

def main():
    if len(sys.argv) < 2:
        print("Usage: python webhook_vs_api_comparator.py <wa_id> [pages]")
        print("Example: python webhook_vs_api_comparator.py 50376973593 3")
        sys.exit(1)
    
    wa_id = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    
    compare_webhook_vs_api_messages(wa_id, pages)

if __name__ == "__main__":
    main()
