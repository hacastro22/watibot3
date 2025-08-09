"""
Contextual Image Classifier

This module provides functionality to classify uploaded images based on both
image content and conversation context to determine if they are payment proofs
or other types of images.

Uses the same gpt-4o-mini model as the payment proof analyzer for consistency.
"""
import os
import json
import logging
import base64
from typing import Dict, Any, List, Optional
from openai import AsyncOpenAI

# Configure logging
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

async def classify_image_with_context(
    image_path: str, 
    conversation_context: str,
    wa_id: str
) -> Dict[str, Any]:
    """
    Classifies an uploaded image using both image content and conversation context.
    
    Args:
        image_path: Path to the image file to classify
        conversation_context: Recent conversation messages for context
        wa_id: WhatsApp ID for logging purposes
    
    Returns:
        dict: {
            "success": bool,
            "classification": str,  # "payment_proof", "general_inquiry", "document", "personal_photo", "unknown"
            "confidence": float,  # 0.0 to 1.0
            "reasoning": str,  # Why this classification was chosen
            "should_analyze_as_payment": bool,  # Whether to proceed with payment analysis
            "suggested_response": str,  # How the system should handle this image type
            "error": str
        }
    """
    try:
        logger.info(f"[IMAGE_CLASSIFIER] Starting contextual image classification for waId: {wa_id}")
        
        # Step 1: Load and encode the image
        try:
            with open(image_path, 'rb') as img_file:
                img_content = img_file.read()
            img_base64 = base64.b64encode(img_content).decode('utf-8')
        except Exception as e:
            logger.exception(f"[IMAGE_CLASSIFIER] Error loading image: {str(e)}")
            return {
                "success": False,
                "classification": "unknown",
                "confidence": 0.0,
                "reasoning": f"Failed to load image: {str(e)}",
                "should_analyze_as_payment": False,
                "suggested_response": "Error processing image",
                "error": f"Image loading error: {str(e)}"
            }
        
        # Step 2: Prepare the classification prompt
        classification_prompt = f"""
        You are an intelligent image classifier for a hotel booking system. Your task is to classify uploaded images based on both the image content and the conversation context.

        CONVERSATION CONTEXT:
        {conversation_context}

        CLASSIFICATION CATEGORIES:
        1. "payment_proof" - Images that are clearly payment receipts, bank transfers, or CompraClick confirmations
        2. "general_inquiry" - Images related to questions about facilities, rooms, menus, policies, etc.
        3. "document" - Screenshots, PDFs, or other informational documents
        4. "personal_photo" - Personal photos, vacation pictures, selfies, etc.
        5. "unknown" - Unclear or unrecognizable images

        IMPORTANT CONTEXT CLUES:
        - Look for payment-related keywords in conversation: "pago", "transferencia", "compraclick", "deposito", "reserva"
        - Consider if customer recently requested a payment link or mentioned booking
        - Payment proofs typically contain: amounts, transaction IDs, bank names, authorization codes
        - General inquiries often follow questions about facilities or services

        Analyze the image and conversation context, then respond with a JSON object:
        {{
            "classification": "payment_proof|general_inquiry|document|personal_photo|unknown",
            "confidence": 0.0-1.0,
            "reasoning": "Detailed explanation of why this classification was chosen, considering both image content and conversation context",
            "visual_indicators": ["List of visual elements that support this classification"],
            "context_indicators": ["List of conversation elements that support this classification"],
            "should_analyze_as_payment": true/false,
            "suggested_response": "Brief suggestion on how the system should handle this image type"
        }}

        Be conservative with payment_proof classification - only classify as payment proof if you're highly confident (>0.8) based on both visual and contextual evidence.
        """

        # Step 3: Create the OpenAI API call
        messages = [
            {
                "role": "system",
                "content": classification_prompt
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Please classify this image based on the content and conversation context provided."
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{img_base64}"
                        }
                    }
                ]
            }
        ]

        # Step 4: Call OpenAI API
        logger.info(f"[IMAGE_CLASSIFIER] Calling OpenAI API for classification")
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=1024
        )

        # Step 5: Parse and validate response
        try:
            result_text = response.choices[0].message.content
            parsed_result = json.loads(result_text)
            
            # Validate and set defaults
            classification = parsed_result.get("classification", "unknown")
            confidence = float(parsed_result.get("confidence", 0.0))
            reasoning = parsed_result.get("reasoning", "No reasoning provided")
            should_analyze_as_payment = parsed_result.get("should_analyze_as_payment", False)
            suggested_response = parsed_result.get("suggested_response", "Process as general image")
            
            # Ensure confidence is within valid range
            confidence = max(0.0, min(1.0, confidence))
            
            # Conservative payment proof detection - require high confidence
            if classification == "payment_proof" and confidence < 0.8:
                logger.info(f"[IMAGE_CLASSIFIER] Downgrading payment_proof classification due to low confidence: {confidence}")
                classification = "document"
                should_analyze_as_payment = False
                reasoning += " (Downgraded due to insufficient confidence for payment proof)"
            
            logger.info(f"[IMAGE_CLASSIFIER] Classification result: {classification} (confidence: {confidence:.2f})")
            
            return {
                "success": True,
                "classification": classification,
                "confidence": confidence,
                "reasoning": reasoning,
                "should_analyze_as_payment": should_analyze_as_payment,
                "suggested_response": suggested_response,
                "visual_indicators": parsed_result.get("visual_indicators", []),
                "context_indicators": parsed_result.get("context_indicators", []),
                "error": ""
            }
            
        except json.JSONDecodeError as e:
            logger.exception(f"[IMAGE_CLASSIFIER] Error parsing OpenAI response: {str(e)}")
            return {
                "success": False,
                "classification": "unknown",
                "confidence": 0.0,
                "reasoning": f"Failed to parse AI response: {str(e)}",
                "should_analyze_as_payment": False,
                "suggested_response": "Process as general image due to classification error",
                "error": f"Response parsing error: {str(e)}"
            }
            
    except Exception as e:
        logger.exception(f"[IMAGE_CLASSIFIER] Unexpected error during classification: {str(e)}")
        return {
            "success": False,
            "classification": "unknown",
            "confidence": 0.0,
            "reasoning": f"Classification failed due to unexpected error: {str(e)}",
            "should_analyze_as_payment": False,
            "suggested_response": "Process as general image due to system error",
            "error": f"Classification error: {str(e)}"
        }

async def get_conversation_context(wa_id: str, max_messages: int = 5) -> str:
    """
    Extracts recent conversation context for the given WhatsApp ID.
    
    Args:
        wa_id: WhatsApp ID to get context for
        max_messages: Maximum number of recent messages to include
    
    Returns:
        str: Formatted conversation context
    """
    try:
        from . import thread_store
        from openai import AsyncOpenAI
        
        # Get the thread data for this customer
        thread_data = thread_store.get_thread_id(wa_id)
        if not thread_data or 'thread_id' not in thread_data:
            logger.info(f"[IMAGE_CLASSIFIER] No thread found for waId: {wa_id}")
            return "No conversation history available."
        
        thread_id = thread_data['thread_id']

        # Get recent messages from the OpenAI thread
        openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        
        try:
            messages = await openai_client.beta.threads.messages.list(
                thread_id=thread_id,
                limit=max_messages,
                order="desc"
            )
            
            # Format the messages for context
            context_parts = []
            for message in reversed(messages.data):  # Reverse to get chronological order
                role = message.role
                content = ""
                
                # Extract content from message
                if hasattr(message, 'content') and message.content:
                    for content_block in message.content:
                        if hasattr(content_block, 'text') and content_block.text:
                            content += content_block.text.value
                        elif hasattr(content_block, 'value'):
                            content += str(content_block.value)
                
                if content.strip():
                    context_parts.append(f"{role.upper()}: {content.strip()}")
            
            context = "\n".join(context_parts[-max_messages:])  # Keep only last N messages
            
            if not context.strip():
                context = "No recent conversation content available."
            
            logger.info(f"[IMAGE_CLASSIFIER] Retrieved {len(context_parts)} messages for context")
            return context
            
        except Exception as e:
            logger.exception(f"[IMAGE_CLASSIFIER] Error retrieving messages from OpenAI thread: {str(e)}")
            return f"Error retrieving conversation history: {str(e)}"
            
    except Exception as e:
        logger.exception(f"[IMAGE_CLASSIFIER] Error getting conversation context: {str(e)}")
        return f"Error accessing conversation context: {str(e)}"
