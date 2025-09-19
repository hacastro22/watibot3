import asyncio
import json
import logging
import os
import re
from typing import Optional, Dict, Any
from openai import AsyncOpenAI
from . import config

logger = logging.getLogger(__name__)

class MessageHumanizer:
    """
    Plugin that takes bare-bones customer service responses and adds humanness, 
    warmth, and empathy using GPT-4.1 model.
    """
    
    def __init__(self):
        self.openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        self.humanization_instructions = self._load_humanization_instructions()
    
    def _load_humanization_instructions(self) -> str:
        """Load humanization instructions from the configuration file."""
        try:
            instructions_path = os.path.join(
                os.path.dirname(__file__), 
                'resources', 
                'humanization_instructions.txt'
            )
            
            with open(instructions_path, 'r', encoding='utf-8') as f:
                instructions = f.read().strip()
            
            return instructions
            
        except Exception as e:
            logger.error(f"Failed to load humanization instructions: {e}")
            # Fallback prompt if file loading fails
            return """Rewrite this answer to make it sounds more human, warm and empathetic. This conversations occurs in context and inside a logical conversation, so don't add anything else. The answers must maintain that context. You don't know how this message fits into the conversation, if it's the first message or the last, so maintain the nuances of the message."""
    
    async def humanize_message(
        self, 
        bare_bones_message: str,
        conversation_context: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        Transform a bare-bones message into a warm, empathetic response.
        
        Args:
            bare_bones_message: The factual, direct message from GPT-5
            conversation_context: Optional context about the conversation
            
        Returns:
            Humanized message with warmth and empathy added
        """
        try:
            logger.info(f"Humanizing message: {bare_bones_message[:100]}...")
            
            # Send just the bare bones message - system instructions contain all guidance
            user_prompt = bare_bones_message

            # Call GPT-4.1 for humanization
            response = await self.openai_client.chat.completions.create(
                model="gpt-4.1",  # Using GPT-4.1 model for better humanization
                messages=[
                    {
                        "role": "system",
                        "content": self.humanization_instructions
                    },
                    {
                        "role": "user", 
                        "content": user_prompt
                    }
                ],
                max_tokens=1000,
                temperature=0.5,  # More creative for natural humanization
                top_p=1
            )
            
            humanized_message = response.choices[0].message.content.strip()
            
            logger.info(f"Successfully humanized message: {humanized_message[:100]}...")
            return humanized_message
            
        except Exception as e:
            logger.error(f"Failed to humanize message: {e}")
            # Fallback: return original message if humanization fails
            logger.warning("Returning original message due to humanization failure")
            return bare_bones_message
    
    async def should_humanize(self, message: str) -> bool:
        """
        Determine if a message should be humanized.
        
        Args:
            message: The message to evaluate
            
        Returns:
            True if the message should be humanized, False otherwise
        """
        # Skip humanization for:
        # - Very short messages (likely acknowledgments)
        # - Messages that already have emojis (likely already humanized)
        # - System messages or error messages
        
        if len(message.strip()) < 20:
            return False
            
        # Check for existing emojis (indicates already humanized)
        emoji_pattern = r'[\U0001F300-\U0001F9FF]'
        if len(re.findall(emoji_pattern, message)) > 2:
            return False
            
        # Check for system-like messages
        system_indicators = ['error', 'sistema', 'technical', 'debug']
        if any(indicator in message.lower() for indicator in system_indicators):
            return False
            
        return True

# Global instance for use throughout the application
message_humanizer = MessageHumanizer()

async def humanize_response(
    bare_bones_response: str,
    conversation_context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Convenience function to humanize a response using the global humanizer instance.
    
    Args:
        bare_bones_response: The factual response from GPT-5
        conversation_context: Optional conversation context
        
    Returns:
        Humanized response with warmth and empathy
    """
    if not await message_humanizer.should_humanize(bare_bones_response):
        logger.info("Skipping humanization - message doesn't meet criteria")
        return bare_bones_response
        
    return await message_humanizer.humanize_message(
        bare_bones_response, 
        conversation_context
    )
