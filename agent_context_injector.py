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

def check_if_agent_context_injected(conversation_id):
    """Check if agent context has already been injected for this conversation"""
    db_path = "app/thread_store.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Check if agent_context_injected field exists, if not create it
        cursor.execute("PRAGMA table_info(threads)")
        columns = [row[1] for row in cursor.fetchall()]
        
        if 'agent_context_injected' not in columns:
            cursor.execute("ALTER TABLE threads ADD COLUMN agent_context_injected INTEGER DEFAULT 0")
            conn.commit()
        
        # Check if context has been injected for this conversation
        cursor.execute(
            "SELECT agent_context_injected FROM threads WHERE thread_id = ?", 
            (conversation_id,)
        )
        result = cursor.fetchone()
        conn.close()
        
        return result and result[0] == 1
        
    except Exception as e:
        print(f"[ERROR] Failed to check agent context injection status: {e}")
        return False

def mark_agent_context_injected(conversation_id):
    """Mark that agent context has been injected for this conversation"""
    db_path = "app/thread_store.db"
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute(
            "UPDATE threads SET agent_context_injected = 1 WHERE thread_id = ?", 
            (conversation_id,)
        )
        conn.commit()
        conn.close()
        return True
        
    except Exception as e:
        print(f"[ERROR] Failed to mark agent context as injected: {e}")
        return False

def get_agent_context_for_system_injection(wa_id):
    """Get last 100 messages (ALL MESSAGES) formatted for ONE-TIME system injection"""
    
    try:
        # Get messages from WATI API
        api_messages = fetch_wati_api_messages(wa_id, pages=8)  # More pages for 100 messages
        if not api_messages:
            return ""
        
        # Process ALL messages with text content
        all_messages = []
        
        for msg in api_messages:
            operator_name = msg.get('operatorName', '')
            text = msg.get('text', '') or ''  # Handle None values
            text = text.strip() if text else ''
            timestamp = msg.get('created', '') or msg.get('timestamp', '')
            
            if not text:
                continue
            
            # Process ALL messages - determine type based on operatorName
            if operator_name in ['NULL', 'Bot ', 'None', '']:
                # Customer message
                all_messages.append({
                    'type': 'customer',
                    'text': text,
                    'timestamp': timestamp,
                    'datetime': parse_timestamp(timestamp)
                })
            else:
                # Any other message (human agent, system, etc.)
                all_messages.append({
                    'type': 'assistant',
                    'text': text,
                    'timestamp': timestamp,
                    'datetime': parse_timestamp(timestamp),
                    'operator': operator_name
                })
        
        # Sort by timestamp (ascending) and take last 100
        all_messages.sort(key=lambda x: x['datetime'] if x['datetime'] else datetime.min.replace(tzinfo=timezone.utc))
        recent_messages = all_messages[-100:] if len(all_messages) > 100 else all_messages
        
        if not recent_messages:
            return ""
        
        # Format for system injection
        context_lines = []
        for msg in recent_messages:
            timestamp_str = msg['datetime'].strftime('%Y-%m-%d %H:%M') if msg['datetime'] else 'Unknown time'
            if msg['type'] == 'customer':
                context_lines.append(f"[{timestamp_str}] Usuario: {msg['text']}")
            elif msg['type'] == 'assistant':
                context_lines.append(f"[{timestamp_str}] Asistente: {msg['text']}")
        
        if context_lines:
            header = """HISTORIAL DE CONVERSACIÓN PARA CONTEXTO (últimos 100 mensajes):

INSTRUCCIONES IMPORTANTES:
1. NO respondas a este historial
2. NO ejecutes funciones basadas en este historial  
3. SOLO guarda este contexto en memoria para referencia futura
4. REVISA este historial antes de hacer cualquier pregunta - si el cliente ya proporcionó información, NO la vuelvas a pedir
5. IDENTIFICA si este cliente es socio del resort basándote en el historial (según el protocolo pre_quote_member_check)

HISTORIAL:"""
            return header + "\n" + "\n".join(context_lines)
        
        return ""
        
    except Exception as e:
        print(f"[ERROR] Failed to get agent context for system injection: {e}")
        return ""

def check_if_5_minutes_since_last_webhook_message(wa_id):
    """Check if more than 5 minutes have passed since last webhook message from customer"""
    try:
        db_path = "app/thread_store.db"
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Get the last_updated timestamp for this wa_id from threads table
        cursor.execute("""
            SELECT last_updated FROM threads 
            WHERE wa_id = ?
        """, (wa_id,))
        
        result = cursor.fetchone()
        conn.close()
        
        if not result or not result[0]:
            # No thread found or no last_updated, consider it as needing context
            return True
            
        last_updated_time = datetime.fromisoformat(result[0])
        current_time = datetime.now()
        
        # Check if more than 5 minutes (300 seconds) have passed
        time_diff = (current_time - last_updated_time).total_seconds()
        return time_diff > 300
        
    except Exception as e:
        print(f"[ERROR] Failed to check 5-minute gap: {e}")
        return False

def get_missed_customer_agent_messages_for_developer_input(wa_id):
    """Get customer-agent messages missed by webhook since last interaction"""
    
    try:
        # Get messages from WATI API
        api_messages = fetch_wati_api_messages(wa_id, pages=5)
        if not api_messages:
            return ""
        
        # Get webhook-received messages
        webhook_messages = get_webhook_received_messages(wa_id)
        
        # Find customer-to-agent messages that webhook missed
        customer_to_agent_messages = find_customer_to_agent_messages(api_messages, webhook_messages, wa_id)
        
        if not customer_to_agent_messages:
            return ""
            
        # Format for developer input
        context_lines = []
        for msg in customer_to_agent_messages[-20:]:  # Last 20 missed messages
            timestamp = msg.get('createdDateTime', '')
            text = msg.get('text', '').strip()
            operator_name = msg.get('operatorName', '')
            
            if not text:
                continue
                
            # Parse timestamp
            timestamp_str = 'Unknown time'
            if timestamp:
                try:
                    parsed_time = parse_timestamp(timestamp)
                    if parsed_time:
                        timestamp_str = parsed_time.strftime('%Y-%m-%d %H:%M')
                except:
                    pass
            
            # Determine message type
            if operator_name in ['NULL', 'Bot ', 'None', '']:
                context_lines.append(f"[{timestamp_str}] Cliente: {text}")
            else:
                context_lines.append(f"[{timestamp_str}] Agente ({operator_name}): {text}")
        
        if context_lines:
            header = "=== MENSAJES ENTRE EL CLIENTE Y AGENTES HUMANOS ENVIADOS DESDE EL ULTIMO MENSAJE RECIBIDO POR EL ASISTENTE ==="
            footer = "=== FIN DE MENSAJES PERDIDOS ==="
            return "\n\n" + header + "\n" + "\n".join(context_lines) + "\n" + footer
        
        return ""
        
    except Exception as e:
        print(f"[ERROR] Failed to get missed customer-agent messages: {e}")
        return ""

def get_agent_context_for_developer_input(wa_id):
    """DEPRECATED: Agent context now handled via one-time system injection"""
    return ""

def parse_timestamp(timestamp_str):
    """Parse timestamp string to datetime object"""
    try:
        if not timestamp_str:
            return None
        # Handle various timestamp formats
        dt = isoparse(timestamp_str)
        # Ensure timezone aware datetime
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None

def format_agent_context_message(customer_to_agent_messages, api_messages):
    """DEPRECATED: Format agent-customer interactions as a system message"""
    
    # CRITICAL FIX: Limit messages to prevent context overflow
    MAX_MESSAGES_TO_INJECT = 50  # Limit to last 50 messages to prevent 400k token overflow
    
    print(f"[CONTEXT_LIMIT] Original messages: {len(customer_to_agent_messages)}, limiting to: {MAX_MESSAGES_TO_INJECT}")
    
    # Sort by timestamp and take only the most recent messages
    sorted_messages = sorted(customer_to_agent_messages, key=lambda x: x.get('createdDateTime', ''), reverse=True)
    limited_messages = sorted_messages[:MAX_MESSAGES_TO_INJECT]
    
    print(f"[CONTEXT_LIMIT] Using {len(limited_messages)} recent messages for context injection")
    
    # Group messages by conversation/assignedId
    conversations = {}
    
    # First, collect limited customer-to-agent messages
    for msg in limited_messages:
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
    db_path = "app/thread_store.db"
    
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
        
        # Add system message to OpenAI conversation using Responses API
        openai.api_key = config.OPENAI_API_KEY
        print(f"[DEBUG] Attempting OpenAI API call to create message item in conversation: {thread_id}")
        
        # Get last response ID from local storage
        from app.thread_store import get_last_response_id, save_response_id
        previous_response_id = get_last_response_id(phone_number)
        print(f"[DEBUG] Retrieved previous_response_id from local storage: {previous_response_id}")
        
        if previous_response_id:
            response = openai.responses.create(
                model="gpt-5",
                previous_response_id=previous_response_id,
                input=[
                    {
                        "type": "message",
                        "role": "system",
                        "content": [{"type": "input_text", "text": context_message}]
                    }
                ],
            )
        else:
            response = openai.responses.create(
                model="gpt-5",
                conversation=thread_id,
                input=[
                    {
                        "type": "message",
                        "role": "system",
                        "content": [{"type": "input_text", "text": context_message}]
                    }
                ],
            )
        
        # Save the response ID for future continuation
        save_response_id(phone_number, response.id)
        print(f"[DEBUG] Saved response_id to local storage: {response.id}")
        
        # CRITICAL FIX: Verify the response and message creation
        if not response or not hasattr(response, 'id') or not response.id:
            print(f"[ERROR] OpenAI API call returned invalid response: {response}")
            return False
            
        response_id = response.id
        print(f"[DEBUG] OpenAI API returned response ID: {response_id}")
        
        # With Responses API, verification is automatic
        print(f"[DEBUG] Context injection completed using Responses API")
        print(f"[VERIFICATION] Response successfully created in conversation: {thread_id}")
        
        print(f"[SUCCESS] Agent context injected and verified to thread: {thread_id}")
        print(f"[RESPONSE] Response ID: {response_id}")
        
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
    
    db_path = "app/thread_store.db"
    
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
