#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import requests
import json
from datetime import datetime
import os
import sys
import time

# Add the app directory to Python path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
import config

def analyze_webhook_vs_api_messages(wa_id, pages=3):
    print("[ANALYSIS] Comparing webhook-received vs WATI API messages for waId: " + wa_id)
    print("=" * 80)
    
    # Step 1: Get all messages from WATI API
    all_api_messages = fetch_wati_api_messages(wa_id, pages)
    
    # Step 2: Check for webhook logs or database entries
    # For now, we'll examine the message patterns to understand the structure
    analyze_message_patterns_for_webhook_detection(all_api_messages)

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

def analyze_message_patterns_for_webhook_detection(messages):
    print("\n" + "=" * 80)
    print("[ANALYSIS] MESSAGE PATTERNS FOR WEBHOOK DETECTION")
    print("=" * 80)
    
    # Look for patterns that might indicate webhook vs non-webhook messages
    customer_messages = []
    assistant_messages = []
    human_agent_messages = []
    
    for msg in messages:
        operator_name = msg.get('operatorName', '')
        event_type = msg.get('eventType', '')
        
        if is_human_agent_message(msg):
            human_agent_messages.append(msg)
        elif is_assistant_message(msg):
            assistant_messages.append(msg)
        elif event_type == 'message' and not is_human_agent_message(msg) and not is_assistant_message(msg):
            # This might be a customer message - let's examine it
            customer_messages.append(msg)
    
    print("[CLASSIFICATION] Message breakdown:")
    print("  Human agent messages: " + str(len(human_agent_messages)))
    print("  Assistant messages: " + str(len(assistant_messages)))
    print("  Potential customer messages: " + str(len(customer_messages)))
    
    # Analyze customer messages for webhook indicators
    print("\n[ANALYSIS] Examining potential customer messages...")
    print("-" * 60)
    
    webhook_likely = []
    agent_likely = []
    
    for i, msg in enumerate(customer_messages[:20]):  # Examine first 20
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
                    formatted_time = parsed_time.strftime('%H:%M:%S')
                else:
                    formatted_time = 'N/A'
            except:
                formatted_time = 'PARSE_ERROR'
            
            text = msg.get('text', 'N/A')
            if len(text) > 100:
                text = text[:100] + '...'
            
            assigned_id = msg.get('assignedId', 'NULL')
            operator_name = msg.get('operatorName', 'NULL')
            
            print("\n[MSG " + str(i+1) + "] " + formatted_time)
            print("  Text: \"" + text + "\"")
            print("  assignedId: " + str(assigned_id))
            print("  operatorName: '" + str(operator_name) + "'")
            
            # Try to classify based on patterns we've seen
            # Webhook messages might have different assignedId patterns
            if assigned_id == 'None' or assigned_id is None:
                classification = "LIKELY_WEBHOOK (unassigned)"
                webhook_likely.append(msg)
            elif assigned_id and assigned_id != 'NULL':
                # Check if this assignedId is associated with human agents
                agent_associated = False
                for agent_msg in human_agent_messages:
                    if agent_msg.get('assignedId') == assigned_id:
                        agent_associated = True
                        break
                
                if agent_associated:
                    classification = "LIKELY_AGENT (shares assignedId with human agent)"
                    agent_likely.append(msg)
                else:
                    classification = "LIKELY_WEBHOOK (assigned but not to human agent)"
                    webhook_likely.append(msg)
            else:
                classification = "UNKNOWN"
            
            print("  CLASSIFICATION: " + classification)
            
        except Exception as e:
            print("[ERROR] Error processing message " + str(i+1) + ": " + str(e))
    
    # Summary
    print("\n" + "=" * 80)
    print("[SUMMARY] WEBHOOK vs AGENT MESSAGE DETECTION")
    print("=" * 80)
    
    print("[LIKELY WEBHOOK] Messages likely sent to webhook (assistant): " + str(len(webhook_likely)))
    print("[LIKELY AGENT] Messages likely sent to human agents: " + str(len(agent_likely)))
    
    if webhook_likely:
        print("\nSample webhook-likely customer messages:")
        for i, msg in enumerate(webhook_likely[:3], 1):
            text = msg.get('text', 'N/A')[:80]
            print("  " + str(i) + ". \"" + text + "...\"")
    
    if agent_likely:
        print("\nSample agent-likely customer messages:")
        for i, msg in enumerate(agent_likely[:3], 1):
            text = msg.get('text', 'N/A')[:80]
            print("  " + str(i) + ". \"" + text + "...\"")
    
    # Suggest next steps
    print("\n[NEXT_STEPS] Recommendations:")
    if len(agent_likely) > 0:
        print("✅ Found " + str(len(agent_likely)) + " potential customer-to-agent messages!")
        print("   These could be injected into assistant context for better understanding.")
    else:
        print("❌ No clear customer-to-agent messages identified with this method.")
        print("   May need to investigate webhook logs or database tracking.")
    
    return webhook_likely, agent_likely

def is_human_agent_message(msg):
    """Identify human agent messages based on operatorName"""
    operator_name = msg.get('operatorName', '')
    event_type = msg.get('eventType', '')
    
    return (
        event_type == 'message' and
        operator_name and 
        operator_name not in ['NULL', 'Bot ', 'None'] and
        operator_name != ''
    )

def is_assistant_message(msg):
    """Identify assistant messages"""
    operator_name = msg.get('operatorName', '')
    event_type = msg.get('eventType', '')
    
    return (
        event_type == 'message' and
        operator_name in ['Bot ', 'None']
    )

def main():
    if len(sys.argv) < 2:
        print("Usage: python webhook_message_analyzer.py <wa_id> [pages]")
        print("Example: python webhook_message_analyzer.py 50376973593 3")
        sys.exit(1)
    
    wa_id = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    
    analyze_webhook_vs_api_messages(wa_id, pages)

if __name__ == "__main__":
    main()
