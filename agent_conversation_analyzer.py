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

def analyze_agent_conversations(wa_id, pages=3):
    print("[ANALYSIS] Analyzing agent conversation patterns for waId: " + wa_id)
    print("=" * 80)
    
    all_messages = []
    page_number = 1
    PAGE_SIZE = 100
    
    # Fetch messages
    while page_number <= pages:
        try:
            print("\n[FETCH] Fetching page " + str(page_number) + "...")
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
                print("[INFO] No more messages found on page " + str(page_number) + ". Stopping.")
                break
            
            print("[SUCCESS] Found " + str(len(messages)) + " messages on page " + str(page_number))
            all_messages.extend(messages)
            page_number += 1
            time.sleep(1)
            
        except Exception as e:
            print("[ERROR] Error on page " + str(page_number) + ": " + str(e))
            break
    
    print("\n[STATS] Total messages collected: " + str(len(all_messages)))
    
    # Find human agent assignedIds and analyze conversations
    analyze_agent_customer_conversations(all_messages)

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

def analyze_agent_customer_conversations(messages):
    print("\n" + "=" * 80)
    print("[ANALYSIS] AGENT-CUSTOMER CONVERSATION DETECTION")
    print("=" * 80)
    
    # Step 1: Find all human agent messages and their assignedIds
    human_agent_assigned_ids = set()
    human_agent_messages = []
    
    for msg in messages:
        if is_human_agent_message(msg):
            assigned_id = msg.get('assignedId', 'NULL')
            if assigned_id and assigned_id != 'NULL':
                human_agent_assigned_ids.add(assigned_id)
                human_agent_messages.append(msg)
    
    print("[DISCOVERY] Found " + str(len(human_agent_messages)) + " human agent messages")
    print("[DISCOVERY] Human agent assignedIds: " + str(list(human_agent_assigned_ids)))
    
    # Step 2: Find all messages with those assignedIds
    agent_conversation_messages = []
    customer_to_agent_messages = []
    
    for msg in messages:
        assigned_id = msg.get('assignedId', 'NULL')
        if assigned_id in human_agent_assigned_ids:
            agent_conversation_messages.append(msg)
            
            # Check if this is a customer message (not human agent, not assistant)
            operator_name = msg.get('operatorName', '')
            event_type = msg.get('eventType', '')
            
            if (event_type == 'message' and 
                not is_human_agent_message(msg) and 
                operator_name not in ['Bot ', 'None']):  # Not assistant either
                customer_to_agent_messages.append(msg)
    
    print("\n[DISCOVERY] Messages in agent conversations: " + str(len(agent_conversation_messages)))
    print("[DISCOVERY] Potential customer-to-agent messages: " + str(len(customer_to_agent_messages)))
    
    # Step 3: Show detailed analysis of agent conversations
    print("\n" + "=" * 80)
    print("[DETAILED] AGENT CONVERSATION ANALYSIS")
    print("=" * 80)
    
    for assigned_id in human_agent_assigned_ids:
        print("\n[CONVERSATION] assignedId: " + str(assigned_id))
        print("-" * 60)
        
        # Get all messages for this assignedId, sorted by time
        conversation_messages = [msg for msg in messages if msg.get('assignedId') == assigned_id]
        
        # Sort by timestamp (most recent first in API, so reverse for chronological)
        conversation_messages.reverse()
        
        for i, msg in enumerate(conversation_messages):
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
                
                operator_name = msg.get('operatorName', 'NULL')
                event_type = msg.get('eventType', 'N/A')
                text = msg.get('text', 'N/A')
                if len(text) > 80:
                    text = text[:80] + '...'
                
                # Classify message type
                if is_human_agent_message(msg):
                    msg_type = "HUMAN_AGENT"
                elif operator_name in ['Bot ', 'None']:
                    msg_type = "ASSISTANT"
                else:
                    msg_type = "CUSTOMER(?)"
                
                print("  [" + str(i+1) + "] " + formatted_time + " | " + msg_type + " | op: '" + str(operator_name) + "'")
                print("      \"" + text + "\"")
                
            except Exception as e:
                print("  [ERROR] Error processing message: " + str(e))
    
    # Step 4: Summary of findings
    print("\n" + "=" * 80)
    print("[SUMMARY] CUSTOMER-TO-AGENT MESSAGE DETECTION")
    print("=" * 80)
    
    if customer_to_agent_messages:
        print("[SUCCESS] Found " + str(len(customer_to_agent_messages)) + " potential customer-to-agent messages!")
        print("\nSample customer-to-agent messages:")
        for i, msg in enumerate(customer_to_agent_messages[:5], 1):
            text = msg.get('text', 'N/A')
            if len(text) > 100:
                text = text[:100] + '...'
            print("  " + str(i) + ". \"" + text + "\"")
            print("     assignedId: " + str(msg.get('assignedId', 'NULL')))
            print("     operatorName: '" + str(msg.get('operatorName', 'NULL')) + "'")
    else:
        print("[ISSUE] No customer-to-agent messages found with this method.")
        print("This could mean:")
        print("  - Customer messages use different operatorName patterns")
        print("  - assignedId approach needs refinement")
        print("  - Need to examine more data or different conversation patterns")

def main():
    if len(sys.argv) < 2:
        print("Usage: python agent_conversation_analyzer.py <wa_id> [pages]")
        print("Example: python agent_conversation_analyzer.py 50376973593 3")
        sys.exit(1)
    
    wa_id = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    
    analyze_agent_conversations(wa_id, pages)

if __name__ == "__main__":
    main()
