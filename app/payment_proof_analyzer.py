"""Payment Proof Analysis Tool

This module provides functionality to analyze payment receipts (images/PDFs)
using OpenAI's gpt-4o-mini or Vertex AI Gemini Vision based on USE_VERTEX_AI flag.
It can identify CompraClick payment receipts or bank transfers and extract key information.

Usage:
    result = await analyze_payment_proof(file_url)
"""
import os
import json
import logging
import base64
import aiohttp
import tempfile
import time
from typing import Optional, Dict, List, Any, Tuple
from openai import AsyncOpenAI
from PyPDF2 import PdfReader
from pdf2image import convert_from_path
import io
from PIL import Image
import vertexai
from vertexai.generative_models import GenerativeModel, Part
import vertexai.preview.generative_models as generative_models

# Configure logging
logger = logging.getLogger(__name__)

# Constants
ALLOWED_MIME_TYPES = ["application/pdf", "image/jpeg", "image/jpg", "image/png"]
MAX_IMAGE_SIZE = 20 * 1024 * 1024  # 20MB limit for images
MAX_PDF_PAGES = 5  # Reasonable limit for payment receipts

# Initialize OpenAI client
client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Import config for feature flag
from . import config

def _initialize_vertex_ai():
    """Initialize Vertex AI for payment proof analysis"""
    try:
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = config.GOOGLE_APPLICATION_CREDENTIALS
        
        vertexai.init(project=config.GOOGLE_CLOUD_PROJECT_ID, location=config.VERTEX_AI_LOCATION)
        logger.info(f"[PAYMENT_ANALYZER] Vertex AI initialized for payment analysis")
        return True
    except Exception as e:
        logger.error(f"[PAYMENT_ANALYZER] Failed to initialize Vertex AI: {e}")
        return False


async def analyze_payment_proof(file_url: str) -> Dict[str, Any]:
    """
    Analyzes a payment proof image or PDF to determine if it's a valid payment receipt.
    
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
        logger.info(f"Starting payment proof analysis for file: {file_url}")
        
        # Step 1: Download the file
        file_content, mime_type = await download_file(file_url)
        if not file_content:
            return {
                "success": False,
                "is_valid_receipt": False, 
                "receipt_type": "unknown",
                "extracted_info": {},
                "error": "Failed to download file"
            }
            
        # Step 2: Convert to appropriate format for o4-mini
        image_data = []
        
        if mime_type == "application/pdf":
            # Handle PDF conversion
            image_data = await convert_pdf_to_images(file_content)
        else:
            # Handle direct image
            try:
                img_base64 = base64.b64encode(file_content).decode('utf-8')
                image_data = [{
                    "type": "image",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_base64}"
                    }
                }]
            except Exception as e:
                logger.exception(f"Error encoding image: {str(e)}")
                return {
                    "success": False,
                    "is_valid_receipt": False,
                    "receipt_type": "unknown",
                    "extracted_info": {},
                    "error": f"Error encoding image: {str(e)}"
                }
        
        # Step 3: Send to AI model for analysis (OpenAI or Vertex AI based on feature flag)
        if config.USE_VERTEX_AI:
            result = await analyze_with_vertex_ai(image_data)
        else:
            result = await analyze_with_o4_mini(image_data)
        
        return result
        
    except Exception as e:
        logger.exception(f"Error analyzing payment proof: {str(e)}")
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
            logger.info(f"Reading local file: {file_url}")
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
                    logger.error(f"Unsupported file extension: {ext}")
                    return None, None
                
                # Check file size
                if len(file_content) > MAX_IMAGE_SIZE:
                    logger.error(f"File too large: {len(file_content)} bytes")
                    return None, None
                
                return file_content, content_type
                
            except Exception as e:
                logger.exception(f"Error reading local file: {str(e)}")
                return None, None
        else:
            # It's a URL
            async with aiohttp.ClientSession() as session:
                async with session.get(file_url) as response:
                    if response.status != 200:
                        logger.error(f"Failed to download file: HTTP {response.status}")
                        return None, None
                    
                    # Get content type from headers
                    content_type = response.headers.get('Content-Type', '')
                    
                    # Verify allowed MIME type
                    if not any(mime_type in content_type for mime_type in ALLOWED_MIME_TYPES):
                        logger.error(f"Unsupported file type: {content_type}")
                        return None, None
                    
                    # Get file content
                    file_content = await response.read()
                    
                    # Check file size
                    if len(file_content) > MAX_IMAGE_SIZE:
                        logger.error(f"File too large: {len(file_content)} bytes")
                        return None, None
                        
                    return file_content, content_type
    except Exception as e:
        logger.exception(f"Error downloading file: {str(e)}")
        return None, None


async def convert_pdf_to_images(pdf_content: bytes) -> List[Dict[str, str]]:
    """
    Converts a PDF to a list of images encoded as base64.
    
    Args:
        pdf_content: PDF file content as bytes
        
    Returns:
        list: List of dicts with base64 encoded images
    """
    image_data = []
    
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
                logger.warning(f"PDF has {num_pages} pages, limiting to first {MAX_PDF_PAGES}")
                num_pages = MAX_PDF_PAGES
            
            # Convert PDF to images
            images = convert_from_path(pdf_path, first_page=1, last_page=num_pages)
            
            # Convert each image to base64
            for i, image in enumerate(images):
                img_byte_arr = io.BytesIO()
                image.save(img_byte_arr, format='JPEG', quality=70)  # Reduce quality to control size
                img_byte_arr = img_byte_arr.getvalue()
                
                # Skip if image is too large
                if len(img_byte_arr) > MAX_IMAGE_SIZE:
                    logger.warning(f"PDF page {i+1} image too large, skipping")
                    continue
                    
                img_base64 = base64.b64encode(img_byte_arr).decode('utf-8')
                image_data.append({
                    "type": "image",
                    "image_url": {
                        "url": f"data:image/jpeg;base64,{img_base64}"
                    }
                })
                
        finally:
            # Clean up temporary file
            os.unlink(pdf_path)
    
    except Exception as e:
        logger.exception(f"Error converting PDF: {str(e)}")
    
    return image_data


async def analyze_with_o4_mini(image_data: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Analyzes images with OpenAI o4-mini to extract payment information.
    
    Args:
        image_data: List of dicts with base64 encoded images
        
    Returns:
        dict: Analysis results with chain of thought for internal use.
    """
    if not image_data:
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "chain_of_thought": {},
            "extracted_info": {},
            "error": "No image data available for analysis"
        }

    try:
        # Create vision prompts with detailed instructions
        messages = [
            {
                "role": "system",
                "content": """You are a payment receipt analyzer. Your task is to analyze the provided image(s) to determine if they represent a valid payment receipt
                for either a CompraClick transaction or a bank transfer.

                **CRITICAL INSTRUCTIONS:**
                1.  **YOUR ANALYSIS MUST BE BASED *EXCLUSIVELY* ON THE VISUAL DATA IN THE IMAGE.** Do NOT use any external information, context, or text from the user's conversation history. If a piece of information is not visibly present in the image, you must not include it. The authorization number, in particular, must be found under the "Autorización" label within the image itself.
                2.  **Identify the receipt type:** "compraclick" or "bank_transfer".
                3.  **Extract key information:** Amount, customer name, transaction ID (authorization # for CompraClick, reference # for bank transfer), and timestamp.
                4.  **For CompraClick, the authorization number is explicitly labeled 'Autorización'.** Do not confuse it with the 'RECIBO' number or any other number.
                5.  **For bank transfers, the target account must be '200252070'.** If it's different, it's not a valid receipt for us.
                6.  **Provide a chain of thought** detailing your analysis steps.
                7.  **Output a JSON object** with the specified format.

                EXTRACTION REQUIREMENTS:

                For CompraClick receipts, extract:
                1. Amount paid (numeric value)
                2. Customer name (if visible)
                3. Authorization code (CRITICAL - MUST be labeled as "Autorización", "Authorization", "Auth Code", "Código de Autorización" or similar field name. DO NOT use "RECIBO" numbers, "ORDER" numbers, "TRANSACTION" numbers, or any other identifiers. If you cannot find a field specifically labeled as authorization/autorización, set is_valid_receipt to FALSE)
                4. Transaction date/timestamp
                5. Last 4 digits of credit card (if visible)
                
                VALIDATION RULES FOR COMPRACLICK:
                - Receipt MUST contain the word "compraclick" or "CompraClick"
                - Receipt MUST have a specific "Autorización" or "Authorization" field
                - If only "RECIBO" numbers are present, mark as INVALID
                - If no authorization field is found, mark as INVALID
                
                For bank transfers, extract:
                1. Amount transferred (numeric value)
                2. Sender/customer name
                3. Transaction date (CRITICAL - required for validation, format as MM/DD/YYYY if possible)
                4. Reference number (if available)
                5. Confirmation that transfer was to account 200252070
                
                Return only a JSON response with the following structure. The 'chain_of_thought' is for internal analysis; DO NOT expose it to the end user:
                {
                    "is_valid_receipt": boolean,
                    "receipt_type": "compraclick" or "bank_transfer" or "unknown",
                    "chain_of_thought": {
                        "steps": [
                            {"step": 1, "action": "Image quality check", "reasoning": "Ensure image is readable", "result": "Clear/Blurry"},
                            {"step": 2, "action": "Detect payment type", "reasoning": "Look for CompraClick or bank indicators", "result": "Found: [indicators]"},
                            {"step": 3, "action": "Extract key information", "reasoning": "Identify required fields for validation", "result": "Extracted: [fields]"},
                            {"step": 4, "action": "Authorization field validation", "reasoning": "For CompraClick: verify 'Autorización' field exists (not RECIBO)", "result": "Authorization field: Found/Missing"},
                            {"step": 5, "action": "Validate completeness", "reasoning": "Check if all critical info is present", "result": "Complete/Missing: [details]"}
                        ],
                        "conclusion": "Receipt type identified with X% confidence"
                    },
                    "extracted_info": {
                        "amount": float,
                        "customer_name": string,
                        "transaction_id": string (authorization code for CompraClick, reference for bank),
                        "timestamp": string (date/time of transaction),
                        "additional_info": {
                            "card_last_four": string (for CompraClick only),
                            "target_account": string (for bank transfer only, should be "200252070")
                        }
                    },
                    "detection_confidence": float (0.0 to 1.0),
                    "detected_indicators": [list of indicators found that led to the classification],
                    "comments": string (any additional observations or potential issues)
                }
                
                If the image quality is poor or critical information is missing, set is_valid_receipt to false
                and explain in comments what information is missing or unclear.
                """
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Analyze this payment receipt and extract the key information based on the detection criteria."
                    }
                ]
            }
        ]

        # Add each image to the user's message content
        for img in image_data:
            messages[1]["content"].append({
                "type": "image_url",
                "image_url": img["image_url"]
            })

        # Call OpenAI API
        response = await client.chat.completions.create(
            model="gpt-4o-mini",
            messages=messages,
            response_format={"type": "json_object"},
            max_tokens=2048  # Increased tokens for the larger response format
        )

        # Parse response
        try:
            result = response.choices[0].message.content
            parsed_result = json.loads(result)

            # Add success field and ensure structure
            parsed_result["success"] = True
            parsed_result.setdefault("is_valid_receipt", False)
            parsed_result.setdefault("receipt_type", "unknown")
            parsed_result.setdefault("chain_of_thought", {})
            parsed_result.setdefault("extracted_info", {})
            parsed_result.setdefault("error", "")

            return parsed_result

        except Exception as e:
            logger.exception(f"Error parsing OpenAI response: {str(e)}")
            return {
                "success": False,
                "is_valid_receipt": False,
                "receipt_type": "unknown",
                "chain_of_thought": {},
                "extracted_info": {},
                "error": f"Error parsing analysis results: {str(e)}"
            }

    except Exception as e:
        logger.exception(f"Error calling OpenAI API: {str(e)}")
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "chain_of_thought": {},
            "extracted_info": {},
            "error": f"Error analyzing with AI model: {str(e)}"
        }

async def analyze_with_vertex_ai(image_data: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Analyzes images with Vertex AI Gemini Vision to extract payment information.
    
    Args:
        image_data: List of dicts with base64 encoded images
        
    Returns:
        dict: Analysis results with chain of thought for internal use.
    """
    if not image_data:
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "chain_of_thought": {},
            "extracted_info": {},
            "error": "No image data available for analysis"
        }

    try:
        logger.info(f"[PAYMENT_ANALYZER] Using Vertex AI Gemini Vision for payment analysis")
        
        # Initialize Vertex AI
        if not _initialize_vertex_ai():
            raise Exception("Failed to initialize Vertex AI")
        
        # Initialize Gemini Vision model
        model = GenerativeModel("gemini-2.5-pro")
        
        # Create system prompt with detailed instructions (same as OpenAI)
        analysis_prompt = """You are a payment receipt analyzer. Your task is to analyze the provided image(s) to determine if they represent a valid payment receipt
        for either a CompraClick transaction or a bank transfer.

        **CRITICAL INSTRUCTIONS:**
        1.  **YOUR ANALYSIS MUST BE BASED *EXCLUSIVELY* ON THE VISUAL DATA IN THE IMAGE.** Do NOT use any external information, context, or text from the user's conversation history. If a piece of information is not visibly present in the image, you must not include it. The authorization number, in particular, must be found under the "Autorización" label within the image itself.
        2.  **Identify the receipt type:** "compraclick" or "bank_transfer".
        3.  **Extract key information:** Amount, customer name, transaction ID (authorization # for CompraClick, reference # for bank transfer), and timestamp.
        4.  **For CompraClick, the authorization number is explicitly labeled 'Autorización'.** Do not confuse it with the 'RECIBO' number or any other number.
        5.  **For bank transfers, the target account must be '200252070'.** If it's different, it's not a valid receipt for us.
        6.  **Provide a chain of thought** detailing your analysis steps.
        7.  **Output a JSON object** with the specified format.

        EXTRACTION REQUIREMENTS:

        For CompraClick receipts, extract:
        1. Amount paid (numeric value)
        2. Customer name (if visible)
        3. Authorization code (CRITICAL - MUST be labeled as "Autorización", "Authorization", "Auth Code", "Código de Autorización" or similar field name. DO NOT use "RECIBO" numbers, "ORDER" numbers, "TRANSACTION" numbers, or any other identifiers. If you cannot find a field specifically labeled as authorization/autorización, set is_valid_receipt to FALSE)
        4. Transaction date/timestamp
        5. Last 4 digits of credit card (if visible)
        
        VALIDATION RULES FOR COMPRACLICK:
        - Receipt MUST contain the word "compraclick" or "CompraClick"
        - Receipt MUST have a specific "Autorización" or "Authorization" field
        - If only "RECIBO" numbers are present, mark as INVALID
        - If no authorization field is found, mark as INVALID
        
        For bank transfers, extract:
        1. Amount transferred (numeric value)
        2. Sender/customer name
        3. Transaction date (CRITICAL - required for validation, format as MM/DD/YYYY if possible)
        4. Reference number (if available)
        5. Confirmation that transfer was to account 200252070
        
        Return only a JSON response with the following structure. The 'chain_of_thought' is for internal analysis; DO NOT expose it to the end user:
        {
            "is_valid_receipt": boolean,
            "receipt_type": "compraclick" or "bank_transfer" or "unknown",
            "chain_of_thought": {
                "steps": [
                    {"step": 1, "action": "Image quality check", "reasoning": "Ensure image is readable", "result": "Clear/Blurry"},
                    {"step": 2, "action": "Detect payment type", "reasoning": "Look for CompraClick or bank indicators", "result": "Found: [indicators]"},
                    {"step": 3, "action": "Extract key information", "reasoning": "Identify required fields for validation", "result": "Extracted: [fields]"},
                    {"step": 4, "action": "Authorization field validation", "reasoning": "For CompraClick: verify 'Autorización' field exists (not RECIBO)", "result": "Authorization field: Found/Missing"},
                    {"step": 5, "action": "Validate completeness", "reasoning": "Check if all critical info is present", "result": "Complete/Missing: [details]"}
                ],
                "conclusion": "Receipt type identified with X% confidence"
            },
            "extracted_info": {
                "amount": float,
                "customer_name": string,
                "transaction_id": string (authorization code for CompraClick, reference for bank),
                "timestamp": string (date/time of transaction),
                "additional_info": {
                    "card_last_four": string (for CompraClick only),
                    "target_account": string (for bank transfer only, should be "200252070")
                }
            },
            "detection_confidence": float (0.0 to 1.0),
            "detected_indicators": [list of indicators found that led to the classification],
            "comments": string (any additional observations or potential issues)
        }
        
        If the image quality is poor or critical information is missing, set is_valid_receipt to false
        and explain in comments what information is missing or unclear.

        Analyze this payment receipt and extract the key information based on the detection criteria."""
        
        # Convert image data to Vertex AI format
        content_parts = [analysis_prompt]
        
        for img_dict in image_data:
            # Extract base64 data from the data URL
            image_url = img_dict.get("image_url", {}).get("url", "")
            if "base64," in image_url:
                base64_data = image_url.split("base64,")[1]
                image_part = Part.from_data(
                    data=base64.b64decode(base64_data),
                    mime_type="image/jpeg"
                )
                content_parts.append(image_part)
        
        # Generate response with retry logic
        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = model.generate_content(
                    content_parts,
                    generation_config=generative_models.GenerationConfig(
                        max_output_tokens=2048,
                        temperature=0.1,  # Low temperature for consistent analysis
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
                    
                    # Parse and validate JSON
                    parsed_result = json.loads(result_text)
                    
                    # Add success field and ensure structure
                    parsed_result["success"] = True
                    parsed_result.setdefault("is_valid_receipt", False)
                    parsed_result.setdefault("receipt_type", "unknown")
                    parsed_result.setdefault("chain_of_thought", {})
                    parsed_result.setdefault("extracted_info", {})
                    parsed_result.setdefault("error", "")
                    
                    logger.info(f"[PAYMENT_ANALYZER] Vertex AI analysis successful")
                    return parsed_result
                else:
                    raise Exception("No response candidates generated")
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"[PAYMENT_ANALYZER] Vertex AI attempt {attempt + 1} failed: {e}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise
        
        raise Exception("Max retries exceeded")
        
    except Exception as e:
        logger.error(f"[PAYMENT_ANALYZER] Vertex AI analysis failed: {e}")
        # Return fallback result
        return {
            "success": False,
            "is_valid_receipt": False,
            "receipt_type": "unknown",
            "chain_of_thought": {},
            "extracted_info": {},
            "error": f"Vertex AI analysis failed: {str(e)}"
        }
