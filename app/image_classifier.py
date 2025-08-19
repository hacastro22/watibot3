"""Contextual Image Classifier

This module provides functionality to classify uploaded images based on both
image content and conversation context to determine if they are payment proofs
or other types of images.

Supports both OpenAI gpt-4o-mini and Vertex AI Gemini Vision based on USE_VERTEX_AI flag.
"""
import os
import json
import logging
import base64
import time
from typing import Dict, Any, List, Optional
from openai import AsyncOpenAI
import vertexai
from vertexai.generative_models import GenerativeModel, Part
import vertexai.preview.generative_models as generative_models

# Configure logging
logger = logging.getLogger(__name__)

# Initialize clients
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Import config for feature flag
from . import config

def _initialize_vertex_ai():
    """Initialize Vertex AI for vision processing"""
    try:
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = config.GOOGLE_APPLICATION_CREDENTIALS
        
        vertexai.init(project=config.GOOGLE_CLOUD_PROJECT_ID, location=config.VERTEX_AI_LOCATION)
        logger.info(f"[IMAGE_CLASSIFIER] Vertex AI initialized for vision processing")
        return True
    except Exception as e:
        logger.error(f"[IMAGE_CLASSIFIER] Failed to initialize Vertex AI: {e}")
        return False

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

        # Step 4: Call AI API (OpenAI or Vertex AI based on feature flag)
        if config.USE_VERTEX_AI:
            result_text = await _classify_with_vertex_ai(img_base64, classification_prompt)
        else:
            logger.info(f"[IMAGE_CLASSIFIER] Calling OpenAI API for classification")
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=messages,
                response_format={"type": "json_object"},
                max_tokens=1024
            )
            result_text = response.choices[0].message.content

        # Step 5: Parse and validate response
        try:
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

async def _classify_with_vertex_ai(img_base64: str, classification_prompt: str) -> str:
    """
    Classify image using Vertex AI Gemini Vision model
    
    Args:
        img_base64: Base64 encoded image data
        classification_prompt: Classification prompt text
        
    Returns:
        JSON string with classification results
    """
    try:
        logger.info(f"[IMAGE_CLASSIFIER] Using Vertex AI Gemini Vision for classification")
        
        # Initialize Vertex AI
        if not _initialize_vertex_ai():
            raise Exception("Failed to initialize Vertex AI")
        
        # Initialize Gemini Vision model
        model = GenerativeModel("gemini-2.5-pro")
        
        # Create image part from base64 data
        image_part = Part.from_data(
            data=base64.b64decode(img_base64),
            mime_type="image/jpeg"
        )
        
        # Create the full prompt
        full_prompt = f"{classification_prompt}\n\nPlease classify this image based on the content and conversation context provided."
        
        # Generate response with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = model.generate_content(
                    [full_prompt, image_part],
                    generation_config=generative_models.GenerationConfig(
                        max_output_tokens=1024,
                        temperature=0.1,  # Low temperature for consistent classification
                        top_p=0.8,
                        top_k=40
                    ),
                    safety_settings={
                        generative_models.HarmCategory.HARM_CATEGORY_HATE_SPEECH: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                        generative_models.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                        generative_models.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                        generative_models.HarmCategory.HARM_CATEGORY_HARASSMENT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    }
                )
                
                if response.candidates and response.candidates[0].content.parts:
                    result_text = response.candidates[0].content.parts[0].text
                    
                    # Clean up response to extract JSON
                    result_text = result_text.strip()
                    if result_text.startswith('```json'):
                        result_text = result_text[7:-3].strip()
                    elif result_text.startswith('```'):
                        lines = result_text.split('\n')
                        result_text = '\n'.join(lines[1:-1])
                    
                    # Validate it's proper JSON
                    json.loads(result_text)  # This will raise an exception if not valid JSON
                    
                    logger.info(f"[IMAGE_CLASSIFIER] Vertex AI classification successful")
                    return result_text
                else:
                    raise Exception("No response candidates generated")
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"[IMAGE_CLASSIFIER] Vertex AI attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise
        
        raise Exception("Max retries exceeded")
        
    except Exception as e:
        logger.error(f"[IMAGE_CLASSIFIER] Vertex AI classification failed: {e}")
        # Return fallback classification
        return json.dumps({
            "classification": "unknown",
            "confidence": 0.0,
            "reasoning": f"Vertex AI classification failed: {str(e)}",
            "visual_indicators": [],
            "context_indicators": [],
            "should_analyze_as_payment": False,
            "suggested_response": "Process as general image due to classification error"
        })
