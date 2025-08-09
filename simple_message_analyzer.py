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

def analyze_messages(wa_id, pages=3):
    print("[ANALYSIS] Analyzing messages for waId: " + wa_id)
    print("[ANALYSIS] Fetching up to " + str(pages) + " pages...")
    print("=" * 80)
    
    all_messages = []
    page_number = 1
    PAGE_SIZE = 100
    
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
            
            # Handle WATI API response structure
            messages_data = response_data.get("messages", {})
            if not isinstance(messages_data, dict):
                print("[WARNING] 'messages' field is not a dictionary. Value: " + str(messages_data))
                messages = []
            else:
                messages = messages_data.get("items", [])
            
            if not messages:
                print("[INFO] No more messages found on page " + str(page_number) + ". Stopping.")
                break
            
            print("[SUCCESS] Found " + str(len(messages)) + " messages on page " + str(page_number))
            all_messages.extend(messages)
            page_number += 1
            
            time.sleep(1)  # Be respectful to the API
            
        except requests.HTTPError as e:
            print("[ERROR] HTTP error fetching page " + str(page_number) + ": " + str(e))
            break
        except Exception as e:
            print("[ERROR] Unexpected error on page " + str(page_number) + ": " + str(e))
            break
    
    print("\n[STATS] Total messages collected: " + str(len(all_messages)))
    
    # Analyze operatorName patterns
    analyze_operator_patterns(all_messages)
    
    # Show recent messages with full details
    show_recent_detailed_messages(all_messages[:20])  # Show first 20 (most recent)

def analyze_operator_patterns(messages):
    print("\n" + "=" * 80)
    print("[ANALYSIS] OPERATOR NAME ANALYSIS")
    print("=" * 80)
    
    operator_stats = {}
    owner_stats = {"true": 0, "false": 0}
    
    for msg in messages:
        is_owner = msg.get('isOwner', False)
        operator_name = msg.get('operatorName', 'NULL')
        event_type = msg.get('eventType', 'unknown')
        
        # Count owner distribution
        owner_key = str(is_owner).lower()
        owner_stats[owner_key] += 1
        
        # Count operator patterns
        if operator_name not in operator_stats:
            operator_stats[operator_name] = {
                'count': 0,
                'isOwner_true': 0,
                'isOwner_false': 0,
                'event_types': set()
            }
        
        operator_stats[operator_name]['count'] += 1
        if is_owner:
            operator_stats[operator_name]['isOwner_true'] += 1
        else:
            operator_stats[operator_name]['isOwner_false'] += 1
        operator_stats[operator_name]['event_types'].add(event_type)
    
    print("[STATS] Owner Distribution:")
    print("   isOwner=true (Business):  " + str(owner_stats['true']))
    print("   isOwner=false (Customer): " + str(owner_stats['false']))
    
    print("\n[PATTERNS] Operator Name Patterns:")
    for operator in sorted(operator_stats.keys(), key=lambda x: operator_stats[x]['count'], reverse=True):
        stats = operator_stats[operator]
        print("   '" + str(operator) + "':")
        print("      Count: " + str(stats['count']))
        print("      When isOwner=true:  " + str(stats['isOwner_true']))
        print("      When isOwner=false: " + str(stats['isOwner_false']))
        print("      Event types: " + ', '.join(stats['event_types']))
        print()

def show_recent_detailed_messages(messages):
    print("\n" + "=" * 80)
    print("[DETAILS] RECENT MESSAGES DETAILED VIEW")
    print("=" * 80)
    
    for i, msg in enumerate(messages, 1):
        try:
            # Parse timestamp
            created = msg.get('created', '')
            try:
                if created:
                    # Handle WATI timestamp format
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
                formatted_time = 'PARSE_ERROR: ' + created
            
            print("\n--- Message " + str(i) + " ---")
            print("[TIME] Time: " + formatted_time)
            print("[OWNER] isOwner: " + str(msg.get('isOwner', 'N/A')))
            print("[OPERATOR] operatorName: '" + str(msg.get('operatorName', 'NULL')) + "'")
            print("[EMAIL] operatorEmail: '" + str(msg.get('operatorEmail', 'NULL')) + "'")
            print("[ASSIGNED] assignedId: '" + str(msg.get('assignedId', 'NULL')) + "'")
            print("[EVENT] eventType: '" + str(msg.get('eventType', 'N/A')) + "'")
            print("[TYPE] type: '" + str(msg.get('type', 'N/A')) + "'")
            
            text = msg.get('text', 'N/A')
            if len(text) > 100:
                text = text[:100] + '...'
            print("[TEXT] text: '" + text + "'")
            
            # Show if this looks like an agent vs assistant message
            is_owner = msg.get('isOwner', False)
            operator_name = msg.get('operatorName', '')
            
            if is_owner and operator_name and operator_name != 'NULL':
                print("[CLASSIFICATION] Likely HUMAN AGENT message")
            elif is_owner and not operator_name:
                print("[CLASSIFICATION] Likely ASSISTANT/BOT message")
            else:
                print("[CLASSIFICATION] Customer message")
                
        except Exception as e:
            print("[ERROR] Error processing message " + str(i) + ": " + str(e))

def main():
    if len(sys.argv) < 2:
        print("Usage: python simple_message_analyzer.py <wa_id> [pages]")
        print("Example: python simple_message_analyzer.py 50376973593 3")
        sys.exit(1)
    
    wa_id = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    
    analyze_messages(wa_id, pages)

if __name__ == "__main__":
    main()
