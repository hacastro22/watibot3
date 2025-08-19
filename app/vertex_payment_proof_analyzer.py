"""
Vertex AI Payment Proof Analysis Tool - Migration target for OpenAI vision models
Implements equivalent functionality using Google Vertex AI vision models
"""

import os
import json
import logging
import base64
import aiohttp
import tempfile
from typing import Optional, Dict, List, Any, Tuple
import vertexai
from vertexai.generative_models import GenerativeModel, Part
import vertexai.preview.generative_models as generative_models
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
import io
from PIL import Image

from . import config

logger = logging.getLogger(__name__)

# Constants
ALLOWED_MIME_TYPES = ["application/pdf", "image/jpeg", "image/jpg", "image/png"]
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB limit for images
MAX_PDF_PAGES = 5  # Reasonable limit for payment receipts

def _initialize_vertex_ai():
    """Initialize Vertex AI with project credentials"""
    try:
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = config.GOOGLE_APPLICATION_CREDENTIALS
        
        vertexai.init(project=config.GOOGLE_CLOUD_PROJECT_ID, location=config.VERTEX_AI_LOCATION)
        return True
    except Exception as e:
        logger.exception(f"[VERTEX_PAYMENT] Failed to initialize Vertex AI: {e}")
        return False

async def analyze_payment_proof(file_url: str) -> Dict[str, Any]:
    """
    Analyzes a payment proof image or PDF using Vertex AI Gemini Vision.
    
    Args:
        file_url: URL to the payment proof file (from WhatsApp/Wati)
    
    Returns:
        dict: {
            "success": bool,
            "is_valid_receipt": bool,
            "receipt_type": str,  # "compraclick", "bank_transfer", "unknown"
            "extracted_info": {
                "amount": float,
                "customer_name": str,
                "transaction_id": str,
                "timestamp": str
            },
            "error": str
        }
    """
    try:
        logger.info(f"[VERTEX_PAYMENT] Starting payment proof analysis for file: {file_url}")
        
        # Initialize Vertex AI
        if not _initialize_vertex_ai():
            raise Exception("Failed to initialize Vertex AI")
        
        # Download the file
        file_content, mime_type = await download_file(file_url)
        if not file_content:
            return {
                "success": False,
                "is_valid_receipt": False, 
                "receipt_type": "unknown",
                "extracted_info": {},
                "error": "Failed to download file"
            }
            
        # Convert to appropriate format for Gemini Vision
        image_parts = []
        
        if mime_type == "application/pdf":
            # Handle PDF conversion
            image_parts = await convert_pdf_to_image_parts(file_content)
        else:
            # Handle direct image
            try:
                image_parts = [Part.from_data(mime_type=mime_type, data=file_content)]
            except Exception as e:
                logger.exception(f"[VERTEX_PAYMENT] Error creating image part: {str(e)}")
                return {
                    "success": False,
                    "is_valid_receipt": False,
                    "receipt_type": "unknown",
                    "extracted_info": {},
                    "error": f"Error processing image: {str(e)}"
                }
        
        # Analyze with Vertex AI Gemini Vision
        result = await analyze_with_gemini_vision(image_parts)
        
        return result
        
    except Exception as e:
        logger.exception(f"[VERTEX_PAYMENT] Error analyzing payment proof: {str(e)}")
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "extracted_info": {},
            "error": f"Error analyzing payment proof: {str(e)}"
        }

async def download_file(file_url: str) -> Tuple[Optional[bytes], Optional[str]]:
    """
    Downloads a file from a URL or reads a local file and determines its MIME type.
    
    Args:
        file_url: URL to download or path to local file
        
    Returns:
        tuple: (file_content, mime_type) or (None, None) if download fails
    """
    try:
        # Check if it's a local file path
        if not file_url.startswith(('http://', 'https://', 'file://')):
            # It's a local file path
            logger.info(f"[VERTEX_PAYMENT] Reading local file: {file_url}")
            try:
                with open(file_url, 'rb') as f:
                    file_content = f.read()
                
                # Determine MIME type from extension
                ext = os.path.splitext(file_url)[1].lower()
                if ext in ('.jpg', '.jpeg'):
                    content_type = 'image/jpeg'
                elif ext == '.png':
                    content_type = 'image/png'
                elif ext == '.pdf':
                    content_type = 'application/pdf'
                else:
                    logger.error(f"[VERTEX_PAYMENT] Unsupported file extension: {ext}")
                    return None, None
                
                # Check file size
                if len(file_content) > MAX_IMAGE_SIZE:
                    logger.error(f"[VERTEX_PAYMENT] File too large: {len(file_content)} bytes")
                    return None, None
                
                return file_content, content_type
                
            except Exception as e:
                logger.exception(f"[VERTEX_PAYMENT] Error reading local file: {str(e)}")
                return None, None
        else:
            # It's a URL
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as response:
                    if response.status != 200:
                        logger.error(f"[VERTEX_PAYMENT] Failed to download file: HTTP {response.status}")
                        return None, None
                    
                    # Get content type from headers
                    content_type = response.headers.get('Content-Type', '')
                    
                    # Verify allowed MIME type
                    if not any(mime_type in content_type for mime_type in ALLOWED_MIME_TYPES):
                        logger.error(f"[VERTEX_PAYMENT] Unsupported file type: {content_type}")
                        return None, None
                    
                    # Get file content
                    file_content = await response.read()
                    
                    # Check file size
                    if len(file_content) > MAX_IMAGE_SIZE:
                        logger.error(f"[VERTEX_PAYMENT] File too large: {len(file_content)} bytes")
                        return None, None
                        
                    return file_content, content_type
    except Exception as e:
        logger.exception(f"[VERTEX_PAYMENT] Error downloading file: {str(e)}")
        return None, None

async def convert_pdf_to_image_parts(pdf_content: bytes) -> List[Part]:
    """
    Converts a PDF to a list of Vertex AI image parts.
    
    Args:
        pdf_content: PDF file content as bytes
        
    Returns:
        list: List of Vertex AI Part objects with images
    """
    image_parts = []
    
    try:
        # Create a temporary file to save the PDF content
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as pdf_file:
            pdf_file.write(pdf_content)
            pdf_path = pdf_file.name
            
        try:
            # Check how many pages the PDF has
            pdf_reader = PdfReader(io.BytesIO(pdf_content))
            num_pages = len(pdf_reader.pages)
            
            # Apply page limit
            if num_pages > MAX_PDF_PAGES:
                logger.warning(f"[VERTEX_PAYMENT] PDF has {num_pages} pages, limiting to first {MAX_PDF_PAGES}")
                num_pages = MAX_PDF_PAGES
            
            # Convert PDF to images
            images = convert_from_path(pdf_path, first_page=1, last_page=num_pages)
            
            # Convert each image to Vertex AI Part
            for i, image in enumerate(images):
                img_byte_arr = io.BytesIO()
                image.save(img_byte_arr, format='JPEG', quality=70)  # Reduce quality to control size
                img_byte_arr = img_byte_arr.getvalue()
                
                # Skip if image is too large
                if len(img_byte_arr) > MAX_IMAGE_SIZE:
                    logger.warning(f"[VERTEX_PAYMENT] PDF page {i+1} image too large, skipping")
                    continue
                    
                image_parts.append(Part.from_data(
                    mime_type="image/jpeg",
                    data=img_byte_arr
                ))
                
        finally:
            # Clean up temporary file
            os.unlink(pdf_path)
    
    except Exception as e:
        logger.exception(f"[VERTEX_PAYMENT] Error converting PDF: {str(e)}")
    
    return image_parts

async def analyze_with_gemini_vision(image_parts: List[Part]) -> Dict[str, Any]:
    """
    Analyzes images with Vertex AI Gemini Vision to extract payment information.
    
    Args:
        image_parts: List of Vertex AI Part objects with images
        
    Returns:
        dict: Analysis results with chain of thought for internal use.
    """
    if not image_parts:
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "chain_of_thought": {},
            "extracted_info": {},
            "error": "No image data available for analysis"
        }

    try:
        # Initialize Gemini Vision model
        model = GenerativeModel("gemini-2.5-pro")
        
        # Create vision prompt with detailed instructions
        analysis_prompt = """Eres un analizador de recibos de pago. Tu tarea es analizar las imágenes proporcionadas para determinar si representan un recibo de pago válido
        para una transacción de CompraClick o una transferencia bancaria.

        **INSTRUCCIONES CRÍTICAS:**
        1. **TU ANÁLISIS DEBE BASARSE *EXCLUSIVAMENTE* EN LOS DATOS VISUALES DE LA IMAGEN.** NO uses información externa, contexto, o texto del historial de conversación del usuario. Si una información no está visiblemente presente en la imagen, no debes incluirla. El número de autorización, en particular, debe encontrarse bajo la etiqueta "Autorización" dentro de la propia imagen.
        2. **Identifica el tipo de recibo:** "compraclick" o "bank_transfer".
        3. **Extrae información clave:** Monto, nombre del cliente, ID de transacción (# de autorización para CompraClick, # de referencia para transferencia bancaria), y timestamp.
        4. **Para CompraClick, el número de autorización está explícitamente etiquetado como 'Autorización'.** No lo confundas con el número de 'RECIBO' o cualquier otro número.
        5. **Para transferencias bancarias, la cuenta destino debe ser '200252070'.** Si es diferente, no es un recibo válido para nosotros.
        6. **Proporciona una cadena de pensamiento** detallando tus pasos de análisis.
        7. **Produce un objeto JSON** con el formato especificado.

        REQUISITOS DE EXTRACCIÓN:

        Para recibos de CompraClick, extrae:
        1. Monto pagado (valor numérico)
        2. Nombre del cliente (si es visible)
        3. Código de autorización (CRÍTICO - DEBE estar etiquetado como "Autorización", "Authorization", "Auth Code", "Código de Autorización" o campo similar. NO uses números "RECIBO", números "ORDER", números "TRANSACTION", u otros identificadores. Si no puedes encontrar un campo específicamente etiquetado como autorización/autorización, establece is_valid_receipt en FALSE)
        4. Fecha/timestamp de transacción
        5. Últimos 4 dígitos de la tarjeta de crédito (si es visible)
        
        REGLAS DE VALIDACIÓN PARA COMPRACLICK:
        - El recibo DEBE contener la palabra "compraclick" o "CompraClick"
        - El recibo DEBE tener un campo específico de "Autorización" o "Authorization"
        - Si solo están presentes números "RECIBO", marca como INVÁLIDO
        - Si no se encuentra el campo de autorización, marca como INVÁLIDO
        
        Para transferencias bancarias, extrae:
        1. Monto transferido (valor numérico)
        2. Nombre del remitente/cliente
        3. Fecha de transacción (CRÍTICO - requerido para validación, formato como MM/DD/YYYY si es posible)
        4. Número de referencia (si está disponible)
        5. Confirmación de que la transferencia fue a la cuenta 200252070
        
        Devuelve solo una respuesta JSON con la siguiente estructura. El 'chain_of_thought' es para análisis interno; NO lo expongas al usuario final:
        {
            "is_valid_receipt": boolean,
            "receipt_type": "compraclick" or "bank_transfer" or "unknown",
            "chain_of_thought": {
                "steps": [
                    {"step": 1, "action": "Verificación de calidad de imagen", "reasoning": "Asegurar que la imagen sea legible", "result": "Clara/Borrosa"},
                    {"step": 2, "action": "Detectar tipo de pago", "reasoning": "Buscar indicadores de CompraClick o banco", "result": "Encontrado: [indicadores]"},
                    {"step": 3, "action": "Extraer información clave", "reasoning": "Identificar campos requeridos para validación", "result": "Extraído: [campos]"},
                    {"step": 4, "action": "Validación de campo de autorización", "reasoning": "Para CompraClick: verificar que existe el campo 'Autorización' (no RECIBO)", "result": "Campo de autorización: Encontrado/Faltante"},
                    {"step": 5, "action": "Validar completitud", "reasoning": "Verificar si toda la información crítica está presente", "result": "Completo/Faltante: [detalles]"}
                ],
                "conclusion": "Tipo de recibo identificado con X% de confianza"
            },
            "extracted_info": {
                "amount": float,
                "customer_name": string,
                "transaction_id": string (código de autorización para CompraClick, referencia para banco),
                "timestamp": string (fecha/hora de la transacción),
                "additional_info": {
                    "card_last_four": string (solo para CompraClick),
                    "target_account": string (solo para transferencia bancaria, debería ser "200252070")
                }
            },
            "detection_confidence": float (0.0 a 1.0),
            "detected_indicators": [lista de indicadores encontrados que llevaron a la clasificación],
            "comments": string (cualquier observación adicional o problemas potenciales)
        }
        
        Si la calidad de la imagen es pobre o falta información crítica, establece is_valid_receipt en false
        y explica en los comentarios qué información falta o no está clara.

        Analiza esta imagen de recibo de pago y extrae la información clave basándote en los criterios de detección.
        """
        
        # Generate response with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                # Create content list with prompt and images
                content = [analysis_prompt] + image_parts
                
                response = model.generate_content(
                    content,
                    generation_config=generative_models.GenerationConfig(
                        max_output_tokens=2048,
                        temperature=0.1,  # Lower temperature for more consistent extraction
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
                        
                        # Add success field and ensure structure
                        parsed_result["success"] = True
                        parsed_result.setdefault("is_valid_receipt", False)
                        parsed_result.setdefault("receipt_type", "unknown")
                        parsed_result.setdefault("chain_of_thought", {})
                        parsed_result.setdefault("extracted_info", {})
                        parsed_result.setdefault("error", "")
                        
                        logger.info(f"[VERTEX_PAYMENT] Analysis result: {parsed_result.get('receipt_type')} (valid: {parsed_result.get('is_valid_receipt')})")
                        return parsed_result
                        
                    except json.JSONDecodeError as e:
                        logger.warning(f"[VERTEX_PAYMENT] Failed to parse JSON response on attempt {attempt + 1}: {e}")
                        if attempt == max_retries - 1:
                            raise
                else:
                    logger.warning(f"[VERTEX_PAYMENT] Empty response from Gemini on attempt {attempt + 1}")
                    
            except Exception as e:
                logger.warning(f"[VERTEX_PAYMENT] Attempt {attempt + 1} failed: {e}")
                if attempt == max_retries - 1:
                    raise

        raise Exception("Failed to analyze payment proof after all retries")

    except Exception as e:
        logger.exception(f"[VERTEX_PAYMENT] Error calling Gemini Vision API: {str(e)}")
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "chain_of_thought": {},
            "extracted_info": {},
            "error": f"Error analyzing with AI model: {str(e)}"
        }
