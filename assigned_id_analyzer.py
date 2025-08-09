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

def analyze_assigned_id_patterns(wa_id, pages=3):
    print("[ANALYSIS] Analyzing assignedId patterns for waId: " + wa_id)
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
    
    # Analyze assignedId patterns
    analyze_assignment_patterns(all_messages)
    
    # Show conversation flows with assignment context
    analyze_conversation_flows(all_messages[:50])  # Analyze first 50 messages

def analyze_assignment_patterns(messages):
    print("\n" + "=" * 80)
    print("[ANALYSIS] ASSIGNED ID PATTERNS")
    print("=" * 80)
    
    assignment_stats = {}
    
    for msg in messages:
        assigned_id = msg.get('assignedId', 'NULL')
        operator_name = msg.get('operatorName', 'NULL')
        event_type = msg.get('eventType', 'unknown')
        
        if assigned_id not in assignment_stats:
            assignment_stats[assigned_id] = {
                'count': 0,
                'operators': set(),
                'event_types': set(),
                'sample_texts': []
            }
        
        assignment_stats[assigned_id]['count'] += 1
        assignment_stats[assigned_id]['operators'].add(str(operator_name) if operator_name is not None else 'None')
        assignment_stats[assigned_id]['event_types'].add(event_type)
        
        # Store sample text for analysis
        text = msg.get('text', '')
        if text and len(assignment_stats[assigned_id]['sample_texts']) < 3:
            assignment_stats[assigned_id]['sample_texts'].append(text[:100])
    
    print("[PATTERNS] Assignment ID Analysis:")
    for assigned_id in sorted(assignment_stats.keys(), key=lambda x: assignment_stats[x]['count'], reverse=True):
        stats = assignment_stats[assigned_id]
        print("\n   assignedId: '" + str(assigned_id) + "':")
        print("      Message count: " + str(stats['count']))
        print("      Operators seen: " + ', '.join(['"' + str(op) + '"' for op in stats['operators']]))
        print("      Event types: " + ', '.join(stats['event_types']))
        if stats['sample_texts']:
            print("      Sample texts:")
            for i, text in enumerate(stats['sample_texts'], 1):
                print("        " + str(i) + ". \"" + text + "...\"")

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

def is_customer_message(msg):
    """Identify customer messages - messages that are not from human agents or bots"""
    operator_name = msg.get('operatorName', '')
    event_type = msg.get('eventType', '')
    
    # Customer messages typically have:
    # - eventType = 'message' 
    # - operatorName = 'None' or 'NULL' or empty
    # - But we need to distinguish from assistant messages
    return (
        event_type == 'message' and
        not is_human_agent_message(msg) and
        operator_name != 'Bot '  # Not the assistant bot
    )

def analyze_conversation_flows(messages):
    print("\n" + "=" * 80)
    print("[ANALYSIS] CONVERSATION FLOW ANALYSIS")
    print("=" * 80)
    
    print("Analyzing message sequences to identify agent-customer interactions...")
    
    for i, msg in enumerate(messages):
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
            assigned_id = msg.get('assignedId', 'NULL')
            text = msg.get('text', 'N/A')
            if len(text) > 80:
                text = text[:80] + '...'
            
            # Classify message type
            if is_human_agent_message(msg):
                msg_type = "HUMAN_AGENT"
            elif operator_name == 'Bot ':
                msg_type = "ASSISTANT"
            elif is_customer_message(msg):
                msg_type = "CUSTOMER"
            else:
                msg_type = "OTHER"
            
            print("\n[" + str(i+1) + "] " + formatted_time + " | " + msg_type + " | assignedId: " + str(assigned_id))
            print("    operatorName: '" + str(operator_name) + "'")
            print("    text: \"" + text + "\"")
            
            # Look for patterns: customer message followed by human agent response
            if msg_type == "CUSTOMER" and i > 0:
                # Check previous and next few messages for context
                print("    [CONTEXT] Looking for related agent interactions...")
                context_found = False
                
                # Check next 5 messages for human agent response
                for j in range(max(0, i-3), min(len(messages), i+4)):
                    if j != i:
                        context_msg = messages[j]
                        if is_human_agent_message(context_msg):
                            context_assigned = context_msg.get('assignedId', 'NULL')
                            if context_assigned == assigned_id or assigned_id == 'NULL':
                                print("    [MATCH] Found related human agent message at position " + str(j+1))
                                context_found = True
                                break
                
                if not context_found:
                    print("    [NO_MATCH] No related human agent interaction found")
                    
        except Exception as e:
            print("[ERROR] Error processing message " + str(i+1) + ": " + str(e))

def main():
    if len(sys.argv) < 2:
        print("Usage: python assigned_id_analyzer.py <wa_id> [pages]")
        print("Example: python assigned_id_analyzer.py 50376973593 3")
        sys.exit(1)
    
    wa_id = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    
    analyze_assigned_id_patterns(wa_id, pages)

if __name__ == "__main__":
    main()
