"""
Vertex AI Image Classifier - Migration target for OpenAI vision models
Implements equivalent functionality using Google Vertex AI vision models
"""

import os
import json
import logging
import base64
from typing import Dict, Any, List, Optional
import vertexai
from vertexai.generative_models import GenerativeModel, Part
import vertexai.preview.generative_models as generative_models

from . import config

logger = logging.getLogger(__name__)

def _initialize_vertex_ai():
    """Initialize Vertex AI with project credentials"""
    try:
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = config.GOOGLE_APPLICATION_CREDENTIALS
        
        vertexai.init(project=config.GOOGLE_CLOUD_PROJECT_ID, location=config.VERTEX_AI_LOCATION)
        return True
    except Exception as e:
        logger.exception(f"[VERTEX_IMAGE] Failed to initialize Vertex AI: {e}")
        return False

async def classify_image_with_context(
    image_path: str, 
    conversation_context: str,
    wa_id: str
) -> Dict[str, Any]:
    """
    Classifies an uploaded image using Vertex AI Gemini Vision model.
    
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
        logger.info(f"[VERTEX_IMAGE] Starting contextual image classification for waId: {wa_id}")
        
        # Initialize Vertex AI
        if not _initialize_vertex_ai():
            raise Exception("Failed to initialize Vertex AI")
        
        # Load and encode the image
        try:
            with open(image_path, 'rb') as img_file:
                img_content = img_file.read()
        except Exception as e:
            logger.exception(f"[VERTEX_IMAGE] Error loading image: {str(e)}")
            return {
                "success": False,
                "classification": "unknown",
                "confidence": 0.0,
                "reasoning": f"Failed to load image: {str(e)}",
                "should_analyze_as_payment": False,
                "suggested_response": "Error processing image",
                "error": f"Image loading error: {str(e)}"
            }
        
        # Initialize Gemini Vision model
        model = GenerativeModel("gemini-2.5-pro")
        
        # Create image part for Gemini
        image_part = Part.from_data(
            mime_type="image/jpeg",
            data=img_content
        )
        
        # Prepare the classification prompt
        classification_prompt = f"""
        Eres un clasificador inteligente de imágenes para un sistema de reservas hoteleras. Tu tarea es clasificar las imágenes subidas basándote tanto en el contenido de la imagen como en el contexto de la conversación.

        CONTEXTO DE CONVERSACIÓN:
        {conversation_context}

        CATEGORÍAS DE CLASIFICACIÓN:
        1. "payment_proof" - Imágenes que son claramente recibos de pago, transferencias bancarias, o confirmaciones de CompraClick
        2. "general_inquiry" - Imágenes relacionadas con preguntas sobre instalaciones, habitaciones, menús, políticas, etc.
        3. "document" - Capturas de pantalla, PDFs, u otros documentos informativos
        4. "personal_photo" - Fotos personales, fotos de vacaciones, selfies, etc.
        5. "unknown" - Imágenes poco claras o no reconocibles

        PISTAS CONTEXTUALES IMPORTANTES:
        - Busca palabras relacionadas con pagos en la conversación: "pago", "transferencia", "compraclick", "deposito", "reserva"
        - Considera si el cliente solicitó recientemente un enlace de pago o mencionó hacer una reserva
        - Los comprobantes de pago típicamente contienen: montos, IDs de transacción, nombres de bancos, códigos de autorización
        - Las consultas generales suelen seguir a preguntas sobre instalaciones o servicios

        Analiza la imagen y el contexto de la conversación, luego responde con un objeto JSON:
        {{
            "classification": "payment_proof|general_inquiry|document|personal_photo|unknown",
            "confidence": 0.0-1.0,
            "reasoning": "Explicación detallada de por qué se eligió esta clasificación, considerando tanto el contenido de la imagen como el contexto de la conversación",
            "visual_indicators": ["Lista de elementos visuales que apoyan esta clasificación"],
            "context_indicators": ["Lista de elementos de conversación que apoyan esta clasificación"],
            "should_analyze_as_payment": true/false,
            "suggested_response": "Breve sugerencia sobre cómo el sistema debería manejar este tipo de imagen"
        }}

        Sé conservador con la clasificación payment_proof - solo clasifica como comprobante de pago si tienes alta confianza (>0.8) basada en evidencia visual y contextual.
        """
        
        # Generate response with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = model.generate_content(
                    [classification_prompt, image_part],
                    generation_config=generative_models.GenerationConfig(
                        max_output_tokens=1024,
                        temperature=0.3,
                        response_mime_type="application/json"
                    ),
                    safety_settings={
                        generative_models.HarmCategory.HARM_CATEGORY_HATE_SPEECH: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                        generative_models.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                        generative_models.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                        generative_models.HarmCategory.HARM_CATEGORY_HARASSMENT: generative_models.HarmBlockThreshold.BLOCK_MEDIUM_AND_ABOVE,
                    }
                )
                
                if response.text:
                    try:
                        parsed_result = json.loads(response.text)
                        
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
                            logger.info(f"[VERTEX_IMAGE] Downgrading payment_proof classification due to low confidence: {confidence}")
                            classification = "document"
                            should_analyze_as_payment = False
                            reasoning += " (Downgraded due to insufficient confidence for payment proof)"
                        
                        logger.info(f"[VERTEX_IMAGE] Classification result: {classification} (confidence: {confidence:.2f})")
                        
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
                        logger.warning(f"[VERTEX_IMAGE] Failed to parse JSON response on attempt {attempt + 1}: {e}")
                        if attempt == max_retries - 1:
                            raise
                else:
                    logger.warning(f"[VERTEX_IMAGE] Empty response from Gemini on attempt {attempt + 1}")
                    
            except Exception as e:
                logger.warning(f"[VERTEX_IMAGE] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise
        
        raise Exception("Failed to classify image after all retries")
        
    except Exception as e:
        logger.exception(f"[VERTEX_IMAGE] Unexpected error during classification: {str(e)}")
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
    Extracts recent conversation context using Vertex AI session data.
    
    Args:
        wa_id: WhatsApp ID to get context for
        max_messages: Maximum number of recent messages to include
    
    Returns:
        str: Formatted conversation context
    """
    try:
        from . import thread_store
        
        # Get recent messages from thread_store
        recent_messages = thread_store.get_recent_messages(conversation_id=wa_id)
        if not recent_messages:
            logger.info(f"[VERTEX_IMAGE] No conversation history for waId: {wa_id}")
            return "No hay historial de conversación disponible."
        
        # Format context (limit to last N messages to avoid token limits)
        context_messages = recent_messages[-max_messages:] if len(recent_messages) > max_messages else recent_messages
        context_parts = []
        
        for msg in context_messages:
            if msg.get('role') == 'user':
                context_parts.append(f"USUARIO: {msg.get('content', '')}")
            elif msg.get('role') == 'assistant':
                context_parts.append(f"ASISTENTE: {msg.get('content', '')}")
        
        context = "\n".join(context_parts)
        
        if not context.strip():
            context = "No hay contenido de conversación reciente disponible."
        
        logger.info(f"[VERTEX_IMAGE] Retrieved {len(context_parts)} messages for context")
        return context
        
    except Exception as e:
        logger.exception(f"[VERTEX_IMAGE] Error getting conversation context: {str(e)}")
        return f"Error al acceder al contexto de conversación: {str(e)}"
