#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Standalone script to analyze WATI messages and examine operatorName field
to distinguish human agent messages from assistant messages.

This script is for analysis only and doesn't affect operational code.
"""

import requests
import json
from datetime import datetime
import os
import sys
import time

# Add the app directory to Python path to import config
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
import config

def analyze_messages(wa_id, pages=5):
    """
    Fetch and analyze messages to examine operatorName field patterns
    """
    print(f"[ANALYSIS] Analyzing messages for waId: {wa_id}")
    print(f"[ANALYSIS] Fetching up to {pages} pages...")
    print("=" * 80)
    
    all_messages = []
    page_number = 1
    PAGE_SIZE = 100
    
    while page_number <= pages:
        try:
            print(f"\n[FETCH] Fetching page {page_number}...")
            wati_url = f"{config.WATI_API_URL}/api/v1/getMessages/{wa_id}?pageSize={PAGE_SIZE}&pageNumber={page_number}"
            headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}
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
                print(f"[WARNING] 'messages' field is not a dictionary. Value: {messages_data}")
                messages = []
            else:
                messages = messages_data.get("items", [])
            
            if not messages:
                print(f"[INFO] No more messages found on page {page_number}. Stopping.")
                break
            
            print(f"[SUCCESS] Found {len(messages)} messages on page {page_number}")
            all_messages.extend(messages)
            page_number += 1
            
            time.sleep(1)  # Be respectful to the API
            
        except requests.HTTPError as e:
            print(f"[ERROR] HTTP error fetching page {page_number}: {e}")
            break
        except Exception as e:
            print(f"[ERROR] Unexpected error on page {page_number}: {e}")
            break
    
    print(f"\n[STATS] Total messages collected: {len(all_messages)}")
    
    # Analyze operatorName patterns
    analyze_operator_patterns(all_messages)
    
    # Show recent messages with full details
    show_recent_detailed_messages(all_messages[:20])  # Show first 20 (most recent)

def analyze_operator_patterns(messages):
    """
    Analyze operatorName field patterns to understand agent vs assistant distinction
    """
    print("\n" + "=" * 80)
    print("🔬 OPERATOR NAME ANALYSIS")
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
    
    print(f"[STATS] Owner Distribution:")
    print(f"   isOwner=true (Business):  {owner_stats['true']}")
    print(f"   isOwner=false (Customer): {owner_stats['false']}")
    
    print(f"\n[PATTERNS] Operator Name Patterns:")
    for operator, stats in sorted(operator_stats.items(), key=lambda x: x[1]['count'], reverse=True):
        print(f"   '{operator}':")
        print(f"      Count: {stats['count']}")
        print(f"      When isOwner=true:  {stats['isOwner_true']}")
        print(f"      When isOwner=false: {stats['isOwner_false']}")
        print(f"      Event types: {', '.join(stats['event_types'])}")
        print()

def show_recent_detailed_messages(messages):
    """
    Show detailed view of recent messages with all relevant fields
    """
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
                            ts_str = f"{main_part}.{frac_part}+{tz_part}"
                    parsed_time = datetime.fromisoformat(ts_str)
                    formatted_time = parsed_time.strftime('%Y-%m-%d %H:%M:%S')
                else:
                    formatted_time = 'N/A'
            except:
                formatted_time = f'PARSE_ERROR: {created}'
            
            print(f"\n--- Message {i} ---")
            print(f"[TIME] Time: {formatted_time}")
            print(f"[OWNER] isOwner: {msg.get('isOwner', 'N/A')}")
            print(f"[OPERATOR] operatorName: '{msg.get('operatorName', 'NULL')}'")
            print(f"[EMAIL] operatorEmail: '{msg.get('operatorEmail', 'NULL')}'")
            print(f"[ASSIGNED] assignedId: '{msg.get('assignedId', 'NULL')}'")
            print(f"[EVENT] eventType: '{msg.get('eventType', 'N/A')}'")
            print(f"[TYPE] type: '{msg.get('type', 'N/A')}'")
            print(f"[TEXT] text: '{msg.get('text', 'N/A')[:100]}{'...' if len(msg.get('text', '')) > 100 else ''}'")
            
            # Show if this looks like an agent vs assistant message
            is_owner = msg.get('isOwner', False)
            operator_name = msg.get('operatorName', '')
            
            if is_owner and operator_name and operator_name != 'NULL':
                print(f"[CLASSIFICATION] Likely HUMAN AGENT message")
            elif is_owner and not operator_name:
                print(f"[CLASSIFICATION] Likely ASSISTANT/BOT message")
            else:
                print(f"[CLASSIFICATION] Customer message")
                
        except Exception as e:
            print(f"[ERROR] Error processing message {i}: {e}")

def main():
    if len(sys.argv) < 2:
        print("Usage: python analyze_operator_messages.py <wa_id> [pages]")
        print("Example: python analyze_operator_messages.py 50376973593 3")
        sys.exit(1)
    
    wa_id = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    
    analyze_messages(wa_id, pages)

if __name__ == "__main__":
    main()
