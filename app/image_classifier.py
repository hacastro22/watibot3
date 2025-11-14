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
import asyncio
import httpx
from typing import Dict, Any, List, Optional
from openai import AsyncOpenAI

# Configure logging
logger = logging.getLogger(__name__)

# Initialize OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Import tools from openai_agent to enable dynamic module loading and other capabilities
from . import openai_agent

async def classify_image_with_context(
    image_path: str, 
    conversation_context: str,
    wa_id: str,
    caption: str = None,
    reply_context_id: str = None
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
        2. "general_inquiry" - Any other image that the customer is sharing (facilities, rooms, menus, documents, personal photos, etc.)

        IMPORTANT CONTEXT CLUES:
        - Look for payment-related keywords in conversation: "pago", "transferencia", "compraclick", "deposito", "reserva"
        - Consider if customer recently requested a payment link or mentioned booking
        - Payment proofs typically contain: amounts, transaction IDs, bank names, authorization codes
        - General inquiries often follow questions about facilities or services

        Analyze the image and conversation context, then respond with a JSON object:
        {{
            "classification": "payment_proof|general_inquiry",
            "confidence": 0.0-1.0,
            "reasoning": "Detailed explanation of why this classification was chosen, considering both image content and conversation context",
            "visual_indicators": ["List of visual elements that support this classification"],
            "context_indicators": ["List of conversation elements that support this classification"],
            "should_analyze_as_payment": true/false
        }}

        Be conservative with payment_proof classification - only classify as payment proof if you're highly confident (>0.8) based on both visual and contextual evidence.
        If not a payment proof, classify as general_inquiry - the system will handle the image in conversation context.
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

        # Step 4: Call OpenAI API using Chat Completions API (like watibot3)
        logger.info(f"[IMAGE_CLASSIFIER] Calling OpenAI API for classification")
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=1024
        )

        # Step 5: Parse and validate response
        try:
            # Extract text from Chat Completions API format (like watibot3)
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
                classification = "general_inquiry"
                should_analyze_as_payment = False
                reasoning += " (Downgraded due to insufficient confidence for payment proof)"
            
            logger.info(f"[IMAGE_CLASSIFIER] Classification result: {classification} (confidence: {confidence:.2f})")
            
            # If it's a general inquiry, handle it directly with Responses API
            if classification == "general_inquiry":
                direct_response = await handle_general_inquiry_image(
                    image_path, wa_id, reasoning, parsed_result.get("visual_indicators", []), caption, reply_context_id
                )
                return {
                    "success": True,
                    "classification": classification,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "should_analyze_as_payment": False,
                    "direct_response": direct_response,
                    "visual_indicators": parsed_result.get("visual_indicators", []),
                    "context_indicators": parsed_result.get("context_indicators", []),
                    "error": ""
                }
            
            return {
                "success": True,
                "classification": classification,
                "confidence": confidence,
                "reasoning": reasoning,
                "should_analyze_as_payment": should_analyze_as_payment,
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

async def handle_general_inquiry_image(image_path: str, wa_id: str, classification_reasoning: str, visual_indicators: List[str], caption: str = None, reply_context_id: str = None) -> Dict[str, Any]:
    """
    Handles general inquiry images by sending them directly to GPT-5 via Responses API
    with full conversation context and system instructions.
    
    Args:
        image_path: Path to the image file
        wa_id: WhatsApp ID for the user
        classification_reasoning: Why this was classified as general inquiry
        visual_indicators: Visual elements detected in the image
        caption: Optional text caption that came with the image
        reply_context_id: Optional reply context ID from universal webhook
    
    Returns:
        dict: Response from the AI system or error information
    """
    try:
        from . import thread_store
        from . import openai_agent
        
        # Get conversation context
        conversation_id = thread_store.get_conversation_id(wa_id)
        previous_response_id = thread_store.get_last_response_id(wa_id)
        
        # Load system instructions (same as main agent)
        system_instructions = openai_agent.build_classification_system_prompt()
        logger.info(f"[IMAGE_CLASSIFIER] Loaded system instructions: {len(system_instructions)} chars")
        
        # Create new conversation if one doesn't exist (same logic as openai_agent.py)
        if not conversation_id:
            logger.info(f"[IMAGE_CLASSIFIER] No conversation ID found for wa_id: {wa_id}, creating new conversation")
            from . import config
            async with httpx.AsyncClient() as http_client:
                response = await http_client.post(
                    "https://api.openai.com/v1/conversations",
                    headers={
                        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={}
                )
                response.raise_for_status()
                conv_data = response.json()
                conversation_id = conv_data.get("id")
                if not conversation_id:
                    raise RuntimeError("Conversations API returned no id")
            
            thread_store.save_conversation_id(wa_id, conversation_id)
            logger.info(f"[IMAGE_CLASSIFIER] Created new conversation {conversation_id} for wa_id: {wa_id}")
            previous_response_id = None  # Fresh conversation has no previous response
        
        # Load and encode the image
        try:
            with open(image_path, 'rb') as img_file:
                img_content = img_file.read()
            img_base64 = base64.b64encode(img_content).decode('utf-8')
        except Exception as e:
            logger.exception(f"[IMAGE_CLASSIFIER] Error loading image for general inquiry: {str(e)}")
            return {
                "success": False,
                "response_text": "Error processing image",
                "error": f"Image loading error: {str(e)}"
            }
        
        # Create the input for Responses API with correct image format
        # Per OpenAI docs: image should come before text for better results
        # Include caption and reply context if provided
        if caption:
            text_content = caption
            logger.info(f"[IMAGE_CLASSIFIER] Image has caption: {caption!r}")
        else:
            text_content = "[Usuario envió una imagen]"
        
        # If this is a reply to a previous message, add context
        if reply_context_id:
            try:
                # Try to retrieve the original message context
                from .wati_client import get_original_message_context
                original_context = await get_original_message_context(wa_id, reply_context_id)
                if original_context:
                    text_content = f"(Customer is replying to: \"{original_context}\") {text_content}"
                    logger.info(f"[IMAGE_CLASSIFIER] Added reply context to image: {reply_context_id}")
            except Exception as e:
                logger.warning(f"[IMAGE_CLASSIFIER] Could not retrieve reply context: {e}")
        
        image_input = {
            "role": "user",
            "content": [
                {
                    "type": "input_image",
                    "image_url": f"data:image/jpeg;base64,{img_base64}",
                    "detail": "auto"
                },
                {
                    "type": "input_text",
                    "text": text_content
                }
            ]
        }
        
        # Call Responses API - ALWAYS include system instructions with images
        logger.info(f"[IMAGE_CLASSIFIER] Sending general inquiry image to Responses API for wa_id: {wa_id} (with_caption: {bool(caption)}, with_reply_context: {bool(reply_context_id)})")
        
        # Instructions must be a STRING for Responses API, not an array
        instructions_text = system_instructions
        
        # Try to continue conversation with previous_response_id if available
        # If that fails due to stale tool calls, we'll recover by creating a fresh conversation
        recovery_attempts = 0
        max_recovery_attempts = 2
        
        while recovery_attempts <= max_recovery_attempts:
            try:
                if previous_response_id and recovery_attempts == 0:
                    # Continue existing conversation with instructions and tools
                    logger.info(f"[IMAGE_CLASSIFIER] Continuing conversation with system instructions and tools")
                    response = await client.responses.create(
                        model="gpt-5",
                        previous_response_id=previous_response_id,
                        instructions=instructions_text,
                        input=[image_input],
                        tools=openai_agent.tools,
                        max_output_tokens=4000
                    )
                else:
                    # New conversation or recovery from error
                    logger.info(f"[IMAGE_CLASSIFIER] Starting {'new' if recovery_attempts == 0 else 'fresh recovery'} conversation with system instructions and tools")
                    response = await client.responses.create(
                        model="gpt-5",
                        conversation=conversation_id,
                        instructions=instructions_text,
                        input=[image_input],
                        tools=openai_agent.tools,
                        max_output_tokens=4000
                    )
                break  # Success, exit retry loop
                
            except Exception as e:
                error_str = str(e).lower()
                # Check if it's a tool call related error (stale tool call IDs)
                if ("tool output" in error_str or "function call" in error_str or "no tool output found" in error_str):
                    recovery_attempts += 1
                    if recovery_attempts > max_recovery_attempts:
                        logger.error(f"[IMAGE_CLASSIFIER] Too many recovery attempts ({recovery_attempts}), giving up")
                        raise  # Re-raise to be caught by outer exception handler
                    
                    logger.warning(f"[IMAGE_CLASSIFIER] Tool call error (attempt {recovery_attempts}): {e}")
                    logger.info(f"[IMAGE_CLASSIFIER] Creating fresh conversation for recovery")
                    
                    # Create fresh conversation (httpx already imported at module level)
                    from . import config
                    async with httpx.AsyncClient() as http_client:
                        response_data = await http_client.post(
                            "https://api.openai.com/v1/conversations",
                            headers={
                                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                                "Content-Type": "application/json",
                            },
                            json={}
                        )
                        response_data.raise_for_status()
                        conv_data = response_data.json()
                        conversation_id = conv_data.get("id")
                        if not conversation_id:
                            raise RuntimeError("Conversations API returned no id")
                    
                    thread_store.save_conversation_id(wa_id, conversation_id)
                    logger.info(f"[IMAGE_CLASSIFIER] Created fresh conversation {conversation_id} for recovery")
                    
                    # INJECT AGENT CONTEXT first for fresh conversation (same as openai_agent.py)
                    from agent_context_injector import get_agent_context_for_system_injection
                    agent_context_system_msg = get_agent_context_for_system_injection(wa_id)
                    if agent_context_system_msg:
                        logger.info(f"[IMAGE_CLASSIFIER] Injecting agent context for fresh recovery conversation {conversation_id}")
                        agent_response = await client.responses.create(
                            model="gpt-5",
                            conversation=conversation_id,
                            input=[{
                                "type": "message",
                                "role": "developer",
                                "content": [{"type": "input_text", "text": agent_context_system_msg}]
                            }],
                            max_output_tokens=16
                        )
                        thread_store.save_response_id(wa_id, agent_response.id)
                        logger.info(f"[IMAGE_CLASSIFIER] Agent context injected for fresh recovery conversation {conversation_id}")
                    
                    previous_response_id = None  # Force new conversation on retry
                else:
                    # Different error, don't retry
                    raise
        
        # Save the response ID for future continuation
        thread_store.save_response_id(wa_id, response.id)
        
        # Handle tool calls (if any) - delegate to openai_agent's tool execution
        # This allows images to trigger dynamic module loading and other tool capabilities
        max_tool_rounds = 5
        round_count = 0
        
        while round_count < max_tool_rounds:
            # Check if there are tool calls in the response
            tool_calls = openai_agent._iter_tool_calls(response)
            if not tool_calls:
                # No tool calls, we're done
                break
            
            round_count += 1
            logger.info(f"[IMAGE_CLASSIFIER] Tool call round {round_count}: {len(tool_calls)} tool(s) requested")
            
            # Execute tool calls
            tool_results = []
            for tc in tool_calls:
                fn_name = getattr(tc, "name", None)
                raw_args = getattr(tc, "arguments", None)
                call_id = getattr(tc, "call_id", None) or getattr(tc, "id", None)
                
                logger.info(f"[IMAGE_CLASSIFIER] Executing tool: {fn_name}")
                
                # Parse arguments
                args = openai_agent._tool_args(raw_args)
                
                # Execute the tool using openai_agent's available_functions map
                try:
                    import inspect
                    func = openai_agent.available_functions.get(fn_name)
                    if func:
                        # Check if function is async
                        if inspect.iscoroutinefunction(func):
                            result = await func(**args)
                        else:
                            result = func(**args)
                        output_str = openai_agent._coerce_output_str(result)
                    else:
                        output_str = f"Error: Unknown tool {fn_name}"
                        logger.warning(f"[IMAGE_CLASSIFIER] Unknown tool: {fn_name}")
                except Exception as e:
                    output_str = f"Error executing {fn_name}: {str(e)}"
                    logger.exception(f"[IMAGE_CLASSIFIER] Tool execution error: {fn_name}")
                
                tool_results.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output_str
                })
            
            # Send tool results back to the API to continue the conversation
            response = await client.responses.create(
                model="gpt-5",
                previous_response_id=response.id,
                input=tool_results,
                tools=openai_agent.tools,
                max_output_tokens=4000
            )
            thread_store.save_response_id(wa_id, response.id)
        
        # Extract final response text
        response_text = ""
        if hasattr(response, 'output') and response.output:
            for item in response.output:
                if hasattr(item, 'content') and item.content:
                    if isinstance(item.content, str):
                        response_text += item.content
                    elif isinstance(item.content, list):
                        for content_block in item.content:
                            if hasattr(content_block, 'text'):
                                response_text += content_block.text
        
        if not response_text.strip():
            response_text = "He recibido tu imagen. ¿Podrías darme más detalles sobre lo que necesitas?"
        
        logger.info(f"[IMAGE_CLASSIFIER] Generated response for general inquiry: {len(response_text)} chars")
        
        return {
            "success": True,
            "response_text": response_text.strip(),
            "response_id": response.id,
            "error": ""
        }
        
    except Exception as e:
        logger.exception(f"[IMAGE_CLASSIFIER] Error handling general inquiry image: {str(e)}")
        return {
            "success": False,
            "response_text": "Disculpa, hubo un error procesando tu imagen. ¿Podrías intentar nuevamente?",
            "error": f"General inquiry handling error: {str(e)}"
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
        
        # Check if we have a conversation for this user
        conversation_id = thread_store.get_conversation_id(wa_id)
        if not conversation_id:
            logger.info(f"[IMAGE_CLASSIFIER] No conversation found for waId: {wa_id}")
            return "No conversation history available."
        
        # With Responses API, conversation context is handled automatically by OpenAI
        # We don't need to manually retrieve or pass conversation history
        logger.info(f"[IMAGE_CLASSIFIER] Conversation context handled automatically by Responses API for waId: {wa_id}")
        return "Conversation context is maintained automatically by the AI system. Consider the ongoing conversation when classifying this image."
            
    except Exception as e:
        logger.exception(f"[IMAGE_CLASSIFIER] Error accessing conversation context: {str(e)}")
        return "Conversation context handled automatically by AI system."
