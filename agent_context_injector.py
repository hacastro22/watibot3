#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent Context Injector for watibot3
Injects agent-customer conversations into OpenAI assistant threads for full context
"""

import os
import sys
import sqlite3
from datetime import datetime, timezone
from dateutil.parser import isoparse
import openai

# Add the app directory to the path
sys.path.append(os.path.join(os.path.dirname(__file__), 'app'))
import config
from webhook_vs_api_comparator import (
    fetch_wati_api_messages, 
    find_customer_to_agent_messages,
    get_webhook_received_messages
)

# Define the go-live date for the OpenAI assistant (timezone-aware)
ASSISTANT_GO_LIVE_DATE = datetime(2025, 7, 5, tzinfo=timezone.utc)

def format_agent_context_message(customer_to_agent_messages, api_messages):
    """Format agent-customer interactions as a system message"""
    
    # Group messages by conversation/assignedId
    conversations = {}
    
    # First, collect all customer-to-agent messages
    for msg in customer_to_agent_messages:
        assigned_id = msg.get('assignedId', 'unassigned')
        timestamp = msg.get('createdDateTime', '')
        
        if assigned_id not in conversations:
            conversations[assigned_id] = []
        
        conversations[assigned_id].append({
            'type': 'customer_to_agent',
            'text': msg.get('text', ''),
            'timestamp': timestamp,
            'operator': msg.get('operatorName', 'None')
        })
    
    # Then, find corresponding agent responses
    for msg in api_messages:
        operator_name = msg.get('operatorName', '')
        assigned_id = msg.get('assignedId', 'unassigned')
        
        # Check if this is a human agent message (and not the automated WATI bot)
        if (operator_name and 
            operator_name not in ['NULL', 'Bot ', 'None', ''] and
            'bot' not in operator_name.lower() and # General check for 'bot'
            assigned_id in conversations):
            
            conversations[assigned_id].append({
                'type': 'agent_response',
                'text': msg.get('text', ''),
                'timestamp': msg.get('createdDateTime', ''),
                'operator': operator_name
            })
    
    # Format the context message
    context_lines = [
        "=== CONVERSACIONES CON AGENTES HUMANOS ===",
        "El siguiente historial muestra conversaciones previas entre el cliente y agentes humanos de Las Hojas Resort.",
        "Esta información te ayudará a brindar un mejor servicio al cliente.",
        ""
    ]
    
    conversation_count = 0
    for assigned_id, messages in conversations.items():
        if not messages:
            continue
            
        conversation_count += 1
        
        # Sort messages by timestamp
        messages.sort(key=lambda x: x['timestamp'])
        
        context_lines.append("--- Conversación {} ---".format(conversation_count))
        
        for msg in messages:
            if msg['type'] == 'customer_to_agent':
                context_lines.append("Cliente: {}".format(msg['text']))
            elif msg['type'] == 'agent_response':
                context_lines.append("Agente ({}): {}".format(msg['operator'], msg['text']))
        
        context_lines.append("")
    
    if conversation_count == 0:
        return None
    
    context_lines.extend([
        "=== FIN DEL HISTORIAL DE AGENTES ===",
        "Usa esta información como contexto adicional para ayudar mejor al cliente.",
        "No menciones explícitamente que tienes acceso a estas conversaciones previas."
    ])
    
    return "\n".join(context_lines)

def inject_agent_context_to_thread(wa_id, context_message):
    """Inject agent context as a system message to OpenAI thread"""
    
    print(f"[INJECTION] Injecting agent context for waId: {wa_id}")
    
    # Get thread ID from database
    db_path = "thread_store.db"
    
    try:
        if not os.path.exists(db_path):
            print("[ERROR] thread_store.db not found!")
            return False
        
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT thread_id FROM threads WHERE wa_id = ?", (wa_id,))
        thread_result = cursor.fetchone()
        
        if not thread_result:
            print(f"[ERROR] No thread found for waId: {wa_id}")
            conn.close()
            return False
        
        thread_id = thread_result[0]
        print(f"[DEBUG] Found thread_id: {thread_id} for waId: {wa_id}")
        conn.close()
        
        # Add system message to OpenAI thread
        openai.api_key = config.OPENAI_API_KEY
        print(f"[DEBUG] Attempting OpenAI API call to create message in thread: {thread_id}")
        
        response = openai.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",  # Use "user" role for context injection
            content=context_message
        )
        
        # CRITICAL FIX: Verify the response and message creation
        if not response or not hasattr(response, 'id') or not response.id:
            print(f"[ERROR] OpenAI API call returned invalid response: {response}")
            return False
            
        message_id = response.id
        print(f"[DEBUG] OpenAI API returned message ID: {message_id}")
        
        # CRITICAL FIX: Verify the message was actually created by retrieving it
        try:
            verification = openai.beta.threads.messages.retrieve(
                thread_id=thread_id,
                message_id=message_id
            )
            
            if not verification or verification.id != message_id:
                print(f"[ERROR] Message verification failed - message not found in thread")
                print(f"[ERROR] Expected message ID: {message_id}, Got: {verification.id if verification else 'None'}")
                return False
                
            print(f"[VERIFICATION] Message successfully verified in thread: {thread_id}")
            
        except Exception as verify_error:
            print(f"[ERROR] Message verification failed: {str(verify_error)}")
            return False
        
        print(f"[SUCCESS] Agent context injected and verified to thread: {thread_id}")
        print(f"[MESSAGE] Message ID: {message_id}")
        
        return True
        
    except openai.OpenAIError as openai_error:
        print(f"[ERROR] OpenAI API error during context injection: {str(openai_error)}")
        print(f"[ERROR] Error type: {type(openai_error).__name__}")
        return False
    except Exception as e:
        print(f"[ERROR] Unexpected error during context injection: {str(e)}")
        print(f"[ERROR] Error type: {type(e).__name__}")
        return False

def update_last_agent_context_check(wa_id):
    """Update the timestamp of last agent context check for this user"""
    
    db_path = "thread_store.db"
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if last_agent_context_check column exists, if not add it
        cursor.execute("PRAGMA table_info(threads)")
        columns = [column[1] for column in cursor.fetchall()]
        
        if 'last_agent_context_check' not in columns:
            print("[DB] Adding last_agent_context_check column...")
            cursor.execute("ALTER TABLE threads ADD COLUMN last_agent_context_check TEXT")
        
        # Update timestamp
        current_time = datetime.now().isoformat()
        cursor.execute(
            "UPDATE threads SET last_agent_context_check = ? WHERE wa_id = ?", 
            (current_time, wa_id)
        )
        
        conn.commit()
        conn.close()
        
        print(f"[DB] Updated last agent context check for waId: {wa_id}")
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to update timestamp: {str(e)}")
        return False

def should_inject_agent_context(wa_id, min_gap_minutes=30):
    """Check if agent context should be injected based on message timing gaps"""
    
    print(f"[CHECK] Checking if agent context injection needed for waId: {wa_id}")
    
    try:
        # For now, always return True since we're testing the system
        # In production, this would check:
        # 1. Time since last message from any party
        # 2. Time since last agent context check
        # 3. Whether new agent interactions exist
        
        print(f"[CHECK] Agent context injection needed for waId: {wa_id}")
        return True
        
    except Exception as e:
        print(f"[ERROR] Error checking injection eligibility: {str(e)}")
        return True  # Default to inject if unsure

def process_agent_context_for_user(wa_id, pages=10):
    """Complete process to inject agent context for a specific user"""
    
    print("=" * 80)
    print(f"[AGENT_CONTEXT] Processing agent context injection for waId: {wa_id}")
    print("=" * 80)
    
    # Check if we should inject
    if not should_inject_agent_context(wa_id):
        return False
    
    try:
        # Get messages from WATI API
        print("[FETCH] Getting messages from WATI API...")
        api_messages = fetch_wati_api_messages(wa_id, pages)
        
        if not api_messages:
            print("[ERROR] No messages found from WATI API")
            return False

        # Get webhook messages
        print("[WEBHOOK] Getting webhook messages...")
        webhook_messages = get_webhook_received_messages(wa_id)
        
        # Filter messages to only post-go-live date
        print("[FILTER] Filtering messages to post-go-live date (July 5, 2025)...")
        post_golive_messages = []
        for msg in api_messages:
            timestamp_str = msg.get('created', '')
            try:
                if timestamp_str:
                    # Use the robust dateutil.parser.isoparse to handle various ISO 8601 formats
                    msg_datetime = isoparse(timestamp_str)

                    # If the parsed datetime is naive, make it timezone-aware (assume UTC)
                    if msg_datetime.tzinfo is None:
                        msg_datetime = msg_datetime.replace(tzinfo=timezone.utc)
                    
                    # Now, safely compare timezone-aware datetime objects
                    if msg_datetime >= ASSISTANT_GO_LIVE_DATE:
                        post_golive_messages.append(msg)

            except Exception as e:
                # If timestamp parsing fails, we cannot reliably filter the message, so we skip it.
                print(f"[WARNING] Could not parse timestamp '{timestamp_str}'. Skipping message. Error: {str(e)}")
        
        print(f"[FILTER] Messages after July 5, 2025: {len(post_golive_messages)} of {len(api_messages)}")
        
        # Find customer-to-agent messages from post-go-live messages only
        print("[ANALYSIS] Finding customer-to-agent messages...")
        from webhook_vs_api_comparator import find_customer_to_agent_messages
        customer_to_agent_messages = find_customer_to_agent_messages(post_golive_messages, webhook_messages, wa_id)
        
        if not customer_to_agent_messages:
            print("[INFO] No customer-to-agent messages found for injection")
            # Still update timestamp to avoid immediate re-checking
            update_last_agent_context_check(wa_id)
            return True
        
        # Format context message
        print("[FORMAT] Formatting agent context message...")
        context_message = format_agent_context_message(customer_to_agent_messages, api_messages)
        
        if not context_message:
            print("[INFO] No meaningful agent context to inject")
            update_last_agent_context_check(wa_id)
            return True
        
        # Inject to OpenAI thread
        print("[INJECT] Injecting context to OpenAI thread...")
        success = inject_agent_context_to_thread(wa_id, context_message)
        
        if success:
            # Update timestamp of last check
            update_last_agent_context_check(wa_id)
            print(f"[SUCCESS] Agent context successfully injected for waId: {wa_id}")
            return True
        else:
            print(f"[ERROR] Failed to inject agent context for waId: {wa_id}")
            return False
            
    except Exception as e:
        print(f"[ERROR] Error processing agent context: {str(e)}")
        return False

def main():
    """Main function for testing"""
    if len(sys.argv) < 2:
        print("Usage: python agent_context_injector.py <wa_id> [pages]")
        sys.exit(1)
    
    wa_id = sys.argv[1]
    pages = int(sys.argv[2]) if len(sys.argv) > 2 else 10
    
    success = process_agent_context_for_user(wa_id, pages)
    
    if success:
        print(f"\n✅ Agent context injection completed for waId: {wa_id}")
    else:
        print(f"\n❌ Agent context injection failed for waId: {wa_id}")

if __name__ == "__main__":
    main()
