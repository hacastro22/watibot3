#!/usr/bin/env python3
"""
Conversation Migration Utility: OpenAI Threads → Vertex AI Sessions

This utility migrates existing conversations from OpenAI Assistant API threads
to Google Vertex AI Agent Engine sessions with zero downtime.
"""

import asyncio
import json
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import config, thread_store
from app.vertex_agent import create_vertex_session, get_conversation_context
import openai
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('migration.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class ConversationMigrator:
    """Handles migration of conversations from OpenAI threads to Vertex sessions"""
    
    def __init__(self):
        self.openai_client = openai.AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self.stats = {
            'total': 0,
            'successful': 0,
            'failed': 0,
            'skipped': 0
        }
        
    async def export_thread_messages(self, thread_id: str) -> List[Dict]:
        """Export all messages from an OpenAI thread"""
        try:
            messages = []
            
            # Get all messages from the thread
            response = await self.openai_client.beta.threads.messages.list(
                thread_id=thread_id,
                order="asc",  # Chronological order
                limit=100
            )
            
            for message in response.data:
                # Extract message content
                content = ""
                if message.content:
                    for content_part in message.content:
                        if hasattr(content_part, 'text'):
                            content += content_part.text.value
                        elif hasattr(content_part, 'value'):
                            content += str(content_part.value)
                
                messages.append({
                    'id': message.id,
                    'role': message.role,
                    'content': content,
                    'created_at': message.created_at,
                    'thread_id': thread_id
                })
            
            logger.info(f"Exported {len(messages)} messages from thread {thread_id}")
            return messages
            
        except Exception as e:
            logger.error(f"Failed to export messages from thread {thread_id}: {e}")
            return []
    
    async def create_vertex_session_with_history(self, wa_id: str, messages: List[Dict]) -> Optional[str]:
        """Create a new Vertex session and import conversation history"""
        try:
            # Create new Vertex session
            session_id = await create_vertex_session(wa_id)
            if not session_id:
                logger.error(f"Failed to create Vertex session for wa_id: {wa_id}")
                return None
            
            # Import conversation history to the new session
            if messages:
                await self.import_messages_to_vertex(session_id, messages, wa_id)
            
            logger.info(f"Created Vertex session {session_id} for wa_id: {wa_id}")
            return session_id
            
        except Exception as e:
            logger.error(f"Failed to create Vertex session for wa_id {wa_id}: {e}")
            return None
    
    async def import_messages_to_vertex(self, session_id: str, messages: List[Dict], wa_id: str):
        """Import conversation messages into a Vertex session"""
        try:
            # Prepare conversation context for injection
            context_parts = []
            
            for message in messages[-20:]:  # Limit to last 20 messages to avoid token limits
                role = message['role']
                content = message['content']
                timestamp = datetime.fromtimestamp(message['created_at']).strftime('%Y-%m-%d %H:%M')
                
                if role == 'user':
                    context_parts.append(f"[{timestamp}] Cliente: {content}")
                elif role == 'assistant':
                    context_parts.append(f"[{timestamp}] Asistente: {content}")
            
            if context_parts:
                # Format history message in Spanish
                history_message = (
                    "=== HISTORIAL DE CONVERSACIÓN MIGRADO ===\n"
                    "Este es el historial de la conversación anterior migrada desde OpenAI:\n\n"
                    + "\n".join(context_parts) +
                    "\n\n=== FIN DEL HISTORIAL ===\n"
                    "Continúa la conversación naturalmente basándote en este contexto."
                )
                
                # Inject history into Vertex session
                await self.inject_context_to_vertex_session(session_id, history_message)
                logger.info(f"Imported {len(context_parts)} messages to Vertex session {session_id}")
            
        except Exception as e:
            logger.error(f"Failed to import messages to Vertex session {session_id}: {e}")
    
    async def inject_context_to_vertex_session(self, session_id: str, context_message: str):
        """Inject context message into Vertex session"""
        try:
            # Use vertex_agent to send context message
            from app.vertex_agent import inject_session_context
            await inject_session_context(session_id, context_message)
            
        except Exception as e:
            logger.error(f"Failed to inject context to Vertex session {session_id}: {e}")
    
    async def migrate_single_conversation(self, wa_id: str, thread_id: str) -> bool:
        """Migrate a single conversation from OpenAI thread to Vertex session"""
        try:
            logger.info(f"Migrating conversation for wa_id: {wa_id}")
            
            # Export messages from OpenAI thread
            messages = await self.export_thread_messages(thread_id)
            
            # Create Vertex session with imported history
            session_id = await self.create_vertex_session_with_history(wa_id, messages)
            
            if session_id:
                # Update database to mark as migrated
                thread_store.mark_vertex_migrated(wa_id, session_id)
                logger.info(f"Successfully migrated conversation {wa_id}: {thread_id} → {session_id}")
                return True
            else:
                logger.error(f"Failed to migrate conversation for wa_id: {wa_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error migrating conversation {wa_id}: {e}")
            return False
    
    async def migrate_batch(self, batch_size: int = 50) -> Dict:
        """Migrate a batch of conversations"""
        logger.info(f"Starting batch migration of {batch_size} conversations")
        
        # Get conversations to migrate
        conversations = thread_store.get_conversations_to_migrate(batch_size)
        
        if not conversations:
            logger.info("No conversations to migrate")
            return {'total': 0, 'successful': 0, 'failed': 0}
        
        logger.info(f"Found {len(conversations)} conversations to migrate")
        
        batch_stats = {'total': len(conversations), 'successful': 0, 'failed': 0}
        
        for conversation in conversations:
            wa_id = conversation['wa_id']
            thread_id = conversation['thread_id']
            
            try:
                success = await self.migrate_single_conversation(wa_id, thread_id)
                
                if success:
                    batch_stats['successful'] += 1
                    self.stats['successful'] += 1
                else:
                    batch_stats['failed'] += 1
                    self.stats['failed'] += 1
                
                # Rate limiting to avoid overwhelming APIs
                await asyncio.sleep(1)
                
            except Exception as e:
                logger.error(f"Batch migration error for {wa_id}: {e}")
                batch_stats['failed'] += 1
                self.stats['failed'] += 1
        
        self.stats['total'] += batch_stats['total']
        
        logger.info(f"Batch completed: {batch_stats['successful']} successful, {batch_stats['failed']} failed")
        return batch_stats
    
    async def migrate_all_conversations(self, batch_size: int = 50):
        """Migrate all conversations in batches"""
        logger.info("Starting full conversation migration")
        
        # Get initial stats
        initial_stats = thread_store.get_migration_stats()
        logger.info(f"Migration stats: {initial_stats['pending']} pending, {initial_stats['migrated']} already migrated")
        
        if initial_stats['pending'] == 0:
            logger.info("All conversations already migrated!")
            return
        
        # Migrate in batches
        while True:
            batch_result = await self.migrate_batch(batch_size)
            
            if batch_result['total'] == 0:
                break
            
            # Progress update
            current_stats = thread_store.get_migration_stats()
            logger.info(f"Progress: {current_stats['migrated']}/{current_stats['total']} migrated ({current_stats['pending']} remaining)")
            
            # Brief pause between batches
            await asyncio.sleep(5)
        
        # Final stats
        final_stats = thread_store.get_migration_stats()
        logger.info(f"Migration completed! Final stats: {final_stats}")
        logger.info(f"Overall stats: {self.stats}")

async def main():
    """Main migration function"""
    print("=== WatiBot3 Conversation Migration ===")
    print("OpenAI Threads → Vertex AI Sessions")
    print()
    
    # Initialize database with new schema
    thread_store.init_db()
    
    # Get current migration status
    stats = thread_store.get_migration_stats()
    print(f"Current status:")
    print(f"  Total conversations: {stats['total']}")
    print(f"  Already migrated: {stats['migrated']}")
    print(f"  Pending migration: {stats['pending']}")
    print()
    
    if stats['pending'] == 0:
        print("✅ All conversations already migrated!")
        return
    
    # Confirm migration
    response = input(f"Migrate {stats['pending']} conversations? (y/N): ")
    if response.lower() != 'y':
        print("Migration cancelled")
        return
    
    # Start migration
    migrator = ConversationMigrator()
    await migrator.migrate_all_conversations()
    
    print("\n=== Migration Complete ===")

if __name__ == "__main__":
    asyncio.run(main())
