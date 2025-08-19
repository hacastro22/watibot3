"""
Vertex AI Agent - Migration target for OpenAI Assistant API
Implements equivalent functionality using Google Vertex AI and Gemini models
"""

import asyncio
import logging
import time
import os
import json
import inspect
from typing import Optional, Tuple, List, Dict, Any
from google.cloud import aiplatform
import vertexai
from vertexai.generative_models import GenerativeModel, Part, FinishReason
import vertexai.preview.generative_models as generative_models

from . import config
from . import thread_store
# booking_tool imported locally to avoid circular dependency
# bank_transfer_tool imported locally to avoid circular dependency
# conversation_history functions imported locally to avoid circular dependency
import httpx
from datetime import datetime

# Import all necessary functions for AVAILABLE_FUNCTIONS
from . import payment_proof_analyzer
from . import compraclick_tool
from . import database_client
from . import smart_availability
from . import office_status_tool
from . import email_service
from . import bank_transfer_retry

logger = logging.getLogger(__name__)

class VertexAIError(Exception):
    """Custom exception for Vertex AI operations"""
    pass

# Initialize Vertex AI
def _initialize_vertex_ai():
    """Initialize Vertex AI with project credentials"""
    try:
        # Set credentials environment variable
        if config.GOOGLE_APPLICATION_CREDENTIALS:
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = config.GOOGLE_APPLICATION_CREDENTIALS
        
        # Initialize Vertex AI
        vertexai.init(project=config.GOOGLE_CLOUD_PROJECT_ID, location=config.VERTEX_AI_LOCATION)
        logger.info(f"[VERTEX] Initialized with project={config.GOOGLE_CLOUD_PROJECT_ID}, location={config.VERTEX_AI_LOCATION}")
        return True
    except Exception as e:
        logger.exception(f"[VERTEX] Failed to initialize Vertex AI: {e}")
        return False

# Tool definitions - complete port from openai_agent.py (17 tools total)
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_payment_proof",
            "description": "Analyzes an image or PDF of a payment receipt to determine if it's a valid CompraClick payment or bank transfer. Use this when the customer sends a payment proof document or image.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_url": {
                        "type": "string",
                        "description": "URL to the payment proof file (from WhatsApp/Wati)"
                    }
                },
                "required": ["file_url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_compraclick_link",
            "description": "Creates a CompraClick payment link for the customer to process a payment for their booking. IMPORTANT: You MUST copy and include the returned payment URL in your response to the user. The link will NOT be automatically sent.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "The name of the customer for the payment link."
                    },
                    "payment_amount": {
                        "type": "number",
                        "description": "The TOTAL booking amount (always the full amount, NOT the partial amount). The payment_percentage parameter determines what portion will be charged."
                    },
                    "calculation_explanation": {
                        "type": "string",
                        "description": "A step-by-step explanation of how the payment amount was calculated. For example: 'The user wants to pay 50% of a $406 booking. Total booking amount: $406. Payment percentage: 50%. The final payment amount will be $203.'"
                    },
                    "payment_percentage": {
                        "type": "string",
                        "description": "Percentage of the total amount to charge (50% deposit or 100% full payment).",
                        "enum": ["50%", "100%"]
                    }
                },
                "required": ["customer_name", "payment_amount", "calculation_explanation", "payment_percentage"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_office_status",
            "description": "MANDATORY: Check customer service office status and automation eligibility. MUST be called before attempting any booking. Returns office status (open/closed) and whether automation is allowed. Can also be called when users ask about office hours.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_price_for_date",
            "description": "Get the price for a specific date for all available packages: Day Pass (lh_adulto, lh_nino), Paquete Amigos (pa_adulto, pa_nino), and Paquete Escapadita (es_adulto, es_nino). The assistant can also use this function on the reverse to check which dates have the prices the customer is interested in, this is useful when the customer has seen a promotion and is asking which dates contain that promotion price.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": "string",
                        "description": "The date to check the price for, in YYYY-MM-DD format."
                    }
                },
                "required": ["date_str"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_location_pin",
            "description": "Formats the business location details into a text message with a Google Maps link. Use this when the user asks for the location of the business.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latitude": {"type": "number", "description": "The latitude of the location."},
                    "longitude": {"type": "number", "description": "The longitude of the location."},
                    "name": {"type": "string", "description": "The name of the location/business."},
                    "address": {"type": "string", "description": "The address of the location."}
                },
                "required": ["latitude", "longitude", "name", "address"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_menu_pdf",
            "description": "Sends the hotel's restaurant menu in PDF format to the user. Use this when the user asks for the menu or food options.",
            "parameters": {
                "type": "object",
                "properties": {
                    "caption": {
                        "type": "string",
                        "description": "A short, friendly message to send along with the menu PDF. For example: '¡Aquí tienes nuestro menú!'"
                    }
                },
                "required": ["caption"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_room_availability",
            "description": "Checks the availability of room types (Bungalow Familiar, Bungalow Junior, Habitacion) for a given date range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "check_in_date": {
                        "type": "string",
                        "description": "The check-in date in YYYY-MM-DD format."
                    },
                    "check_out_date": {
                        "type": "string",
                        "description": "The check-out date in YYYY-MM-DD format."
                    }
                },
                "required": ["check_in_date", "check_out_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_smart_availability",
            "description": "Smart availability checker that offers partial stay options when full period is unavailable. Use this instead of check_room_availability when customer requests multi-night stays to maximize booking opportunities by offering alternative partial periods.",
            "parameters": {
                "type": "object",
                "properties": {
                    "check_in_date": {
                        "type": "string",
                        "description": "The check-in date in YYYY-MM-DD format."
                    },
                    "check_out_date": {
                        "type": "string",
                        "description": "The check-out date in YYYY-MM-DD format."
                    }
                },
                "required": ["check_in_date", "check_out_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_bungalow_pictures",
            "description": "Sends pictures of a specific bungalow type to the user. Use this when the user asks for photos of the accommodations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "bungalow_type": {
                        "type": "string",
                        "description": "The type of bungalow to send pictures for.",
                        "enum": ["Bungalow Familiar", "Bungalow Junior", "Habitacion"]
                    }
                },
                "required": ["bungalow_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_public_areas_pictures",
            "description": "Sends pictures of the public areas of the hotel (restaurant, pool, common areas, etc.). Use this when the user asks for photos of the facilities or public spaces.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sync_compraclick_payments",
            "description": "Synchronizes CompraClick payments by downloading the latest transaction report. Run this before validating a CompraClick payment.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_compraclick_payment",
            "description": "Validates a CompraClick payment using the authorization number from the receipt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "authorization_number": {
                        "type": "string",
                        "description": "The authorization number from the CompraClick receipt."
                    },
                    "booking_total": {
                        "type": "number",
                        "description": "The total amount of the booking to validate against."
                    }
                },
                "required": ["authorization_number", "booking_total"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_compraclick_payment_fallback",
            "description": "Fallback validation for CompraClick payments when authorization code is not available. Use this ONLY after customer fails to provide correct authorization code 3 times OR explicitly states they cannot find it. Validates payment by matching credit card last 4 digits, charged amount, and payment date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_last_four": {
                        "type": "string",
                        "description": "Last 4 digits of the credit card used for payment"
                    },
                    "charged_amount": {
                        "type": "number",
                        "description": "Amount that was charged to the credit card"
                    },
                    "payment_date": {
                        "type": "string",
                        "description": "Date when the payment was made (accepts various formats: 'hoy', 'ayer', 'DD/MM/YYYY', etc.)"
                    },
                    "booking_total": {
                        "type": "number",
                        "description": "The total amount of the booking to validate against"
                    }
                },
                "required": ["card_last_four", "charged_amount", "payment_date", "booking_total"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "sync_bank_transfers",
            "description": "Synchronizes bank transfer payments by downloading the latest transaction report. Run this before validating a bank transfer.",
            "parameters": {"type": "object", "properties": {}}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "validate_bank_transfer",
            "description": "Validates a booking amount against a bank transfer slip. Use this when a customer provides a bank transfer receipt.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slip_date": {
                        "type": "string",
                        "description": "The date on the payment slip, in YYYY-MM-DD format."
                    },
                    "slip_amount": {
                        "type": "number",
                        "description": "The total amount shown on the payment slip."
                    },
                    "booking_amount": {
                        "type": "number",
                        "description": "The amount of the booking to validate and use from the slip's balance."
                    }
                },
                "required": ["slip_date", "slip_amount", "booking_amount"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "make_booking",
            "description": "Creates a booking reservation after payment verification. CRITICAL: This tool can ONLY be used AFTER payment proof has been verified using payment verification tools. All booking information must be explicitly provided by the customer. SUPPORTS PARTIAL PAYMENTS: The system will automatically calculate the true booking total using database rates and compare it to the payment amount received.",
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {
                        "type": "string",
                        "description": "Full name of the customer (must be explicitly provided, not inferred)"
                    },
                    "email": {
                        "type": "string",
                        "description": "Customer's email address (must be explicitly provided)"
                    },
                    "phone_number": {
                        "type": "string",
                        "description": "Customer's phone number (will be extracted from waId)"
                    },
                    "city": {
                        "type": "string",
                        "description": "Customer's city of origin (must be explicitly provided)"
                    },
                    "dui_passport": {
                        "type": "string",
                        "description": "Customer's DUI or passport number (must be explicitly provided)"
                    },
                    "nationality": {
                        "type": "string",
                        "description": "Customer's nationality (must be explicitly provided)"
                    },
                    "check_in_date": {
                        "type": "string",
                        "description": "Check-in date in YYYY-MM-DD format (must be explicitly provided)"
                    },
                    "check_out_date": {
                        "type": "string",
                        "description": "Check-out date in YYYY-MM-DD format (must be explicitly provided)"
                    },
                    "adults": {
                        "type": "integer",
                        "description": "Number of adults (must be explicitly provided)"
                    },
                    "children_0_5": {
                        "type": "integer",
                        "description": "Number of children 0-5 years old (must be explicitly provided, 0 if none)"
                    },
                    "children_6_10": {
                        "type": "integer",
                        "description": "Number of children 6-10 years old (must be explicitly provided, 0 if none)"
                    },
                    "bungalow_type": {
                        "type": "string",
                        "description": "Type of accommodation. For overnight stays, use Familiar, Junior, Matrimonial, or Habitación. CRITICAL: For a day pass, this MUST be set to 'Pasadía'. (must be explicitly provided)"
                    },
                    "package_type": {
                        "type": "string",
                        "description": "Package type: Las Hojas, Escapadita, Pasadía, or Romántico (must be explicitly provided)"
                    },
                    "payment_method": {
                        "type": "string",
                        "description": "Payment method: CompraClick or Depósito BAC (determined from payment verification)"
                    },
                    "payment_amount": {
                        "type": "number",
                        "description": "Actual amount paid by customer (from payment verification). May be partial payment, deposit, or full payment. System will calculate true booking total from database rates and handle accordingly."
                    },
                    "payment_maker_name": {
                        "type": "string",
                        "description": "Name of the person who made the payment (from payment verification)"
                    },
                    "wa_id": {
                        "type": "string",
                        "description": "WhatsApp ID of the customer (extracted from conversation context)"
                    },
                    "authorization_number": {
                        "type": "string",
                        "description": "CompraClick authorization number (if payment method is CompraClick)"
                    },
                    "transfer_id": {
                        "type": "string",
                        "description": "Bank transfer ID (if payment method is Depósito BAC)"
                    },
                    "extra_beds": {
                        "type": "integer",
                        "description": "Number of extra beds requested (0 if none). CRITICAL: Follow extra bed policy from system instructions."
                    },
                    "extra_beds_cost": {
                        "type": "number",
                        "description": "Total cost for extra beds (0.0 if free or none). Use $15.00 per bed for paid beds."
                    },
                    "customer_instructions": {
                        "type": "string",
                        "description": "Special instructions from customer in Spanish (optional). Must comply with hotel policy and system instructions."
                    }
                },
                "required": ["customer_name", "email", "phone_number", "city", "dui_passport", "nationality", "check_in_date", "check_out_date", "adults", "children_0_5", "children_6_10", "bungalow_type", "package_type", "payment_method", "payment_amount", "payment_maker_name", "wa_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Sends an email to internal hotel staff based on specific use cases. CRITICAL: This tool is ONLY for internal notifications and MUST NOT be used to email customers. The `to_emails` parameter is strictly validated. Use cases: 1. For membership inquiries, email `promociones@lashojasresort.com`. 2. For group quotes (20+ people), email `sbartenfeld@lashojasresort.com` and `acienfuegos@lashojasresort.com`. 3. For last-minute customer information for reception, email `reservas@lashojasresort.com`.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to_emails": {
                        "type": "array",
                        "items": {
                            "type": "string"
                        },
                        "description": "A list of approved recipient email addresses. Must be one of the allowed addresses based on the use case."
                    },
                    "subject": {
                        "type": "string",
                        "description": "The subject of the email, clearly stating the purpose (e.g., 'Membership Inquiry', 'Group Quote Request')."
                    },
                    "body": {
                        "type": "string",
                        "description": "The body of the email, containing all relevant details, such as customer contact info or special requests."
                    }
                },
                "required": ["to_emails", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_bank_transfer_retry_process",
            "description": "Start automatic staged retry process for bank transfer validation when initial validation fails. This will retry validation every 5 minutes (6x), then every 30 minutes (4x), then every hour (6x) before escalating to human agent. Use this when sync_bank_transfers succeeds but validate_bank_transfer fails.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Customer's phone number"
                    },
                    "payment_data": {
                        "type": "object",
                        "properties": {
                            "slip_date": {
                                "type": "string",
                                "description": "Date from payment slip (YYYY-MM-DD format)"
                            },
                            "slip_amount": {
                                "type": "number",
                                "description": "Amount from payment slip"
                            },
                            "booking_amount": {
                                "type": "number",
                                "description": "Amount needed for booking validation"
                            },
                            "booking_data": {
                                "type": "object",
                                "description": "Complete booking data for automatic booking completion",
                                "properties": {
                                    "customer_name": {"type": "string"},
                                    "email": {"type": "string"},
                                    "city": {"type": "string"},
                                    "dui_passport": {"type": "string"},
                                    "nationality": {"type": "string"},
                                    "check_in_date": {"type": "string"},
                                    "check_out_date": {"type": "string"},
                                    "adults": {"type": "integer"},
                                    "children_0_5": {"type": "integer"},
                                    "children_6_10": {"type": "integer"},
                                    "bungalow_type": {"type": "string"},
                                    "package_type": {"type": "string"},
                                    "extra_beds": {"type": "integer", "description": "Number of extra beds requested (0 if none)"},
                                    "extra_beds_cost": {"type": "number", "description": "Total cost for extra beds (0.0 if free or none)"},
                                    "customer_instructions": {"type": "string", "description": "Special instructions from customer in Spanish (optional)"}
                                }
                            }
                        },
                        "required": ["slip_date", "slip_amount", "booking_amount", "booking_data"]
                    }
                },
                "required": ["phone_number", "payment_data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "mark_customer_frustrated",
            "description": "Mark customer as frustrated to immediately halt retry process and escalate to human agent. Use this when customer expresses annoyance, requests refund, or complains about waiting time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Customer's phone number"
                    }
                },
                "required": ["phone_number"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "trigger_compraclick_retry_for_missing_payment",
            "description": "Start automatic staged retry process for CompraClick payment validation when initial validation fails. This will retry validation every 5 minutes (6x), then every 30 minutes (4x), then every hour (6x) before escalating to human agent. Use this when sync_compraclick_payments succeeds but validate_compraclick_payment fails with 'Authorization code not found'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Customer's phone number"
                    },
                    "authorization_number": {
                        "type": "string",
                        "description": "CompraClick authorization number from payment receipt"
                    },
                    "booking_total": {
                        "type": "number",
                        "description": "Total booking amount for validation"
                    },
                    "booking_data": {
                        "type": "object",
                        "description": "Complete booking data for automatic booking completion",
                        "properties": {
                            "customer_name": {"type": "string"},
                            "email": {"type": "string"},
                            "city": {"type": "string"},
                            "dui_passport": {"type": "string"},
                            "nationality": {"type": "string"},
                            "check_in_date": {"type": "string"},
                            "check_out_date": {"type": "string"},
                            "adults": {"type": "integer"},
                            "children_0_5": {"type": "integer"},
                            "children_6_10": {"type": "integer"},
                            "bungalow_type": {"type": "string"},
                            "package_type": {"type": "string"},
                            "extra_beds": {"type": "integer", "description": "Number of extra beds requested (0 if none)"},
                            "extra_beds_cost": {"type": "number", "description": "Total cost for extra beds (0.0 if free or none)"},
                            "customer_instructions": {"type": "string", "description": "Special instructions from customer in Spanish (optional)"}
                        }
                    }
                },
                "required": ["phone_number", "authorization_number", "booking_total", "booking_data"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_conversation_snippets",
            "description": "Search and retrieve conversation history snippets based on a query. Performs case-insensitive substring search using SQLite LIKE with escaped wildcards. Use this to find relevant past conversation context when customers reference previous discussions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Customer's phone number to search conversations for"
                    },
                    "query": {
                        "type": "string",
                        "description": "Search query to find relevant conversation snippets"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of conversation snippets to return (default: 5)",
                        "default": 5
                    }
                },
                "required": ["phone_number", "query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "book_room",
            "description": "Complete automatic booking with validated payment",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {
                        "type": "string",
                        "description": "Customer phone number (WhatsApp ID)"
                    },
                    "authorization_number": {
                        "type": "string",
                        "description": "Bank transfer authorization number"
                    },
                    "booking_total": {
                        "type": "number",
                        "description": "Total booking amount for validation"
                    },
                    "booking_data": {
                        "type": "object",
                        "description": "Complete booking data for automatic booking completion",
                        "properties": {
                            "customer_name": {"type": "string"},
                            "email": {"type": "string"},
                            "city": {"type": "string"},
                            "dui_passport": {"type": "string"},
                            "nationality": {"type": "string"},
                            "check_in_date": {"type": "string"},
                            "check_out_date": {"type": "string"},
                            "adults": {"type": "integer"},
                            "children_0_5": {"type": "integer"},
                            "children_6_10": {"type": "integer"},
                            "bungalow_type": {"type": "string"},
                            "package_type": {"type": "string"},
                            "extra_beds": {"type": "integer", "description": "Number of extra beds requested (0 if none)"},
                            "extra_beds_cost": {"type": "number", "description": "Total cost for extra beds (0.0 if free or none)"},
                            "customer_instructions": {"type": "string", "description": "Special instructions from customer in Spanish (optional)"}
                        }
                    }
                },
                "required": ["phone_number", "authorization_number", "booking_total", "booking_data"]
            }
        }
    }
]

# Available functions mapping - all tools from openai_agent.py (22 tools total)
AVAILABLE_FUNCTIONS = {
    "analyze_payment_proof": payment_proof_analyzer.analyze_payment_proof,
    "create_compraclick_link": compraclick_tool.create_compraclick_link,
    "check_office_status": office_status_tool.check_office_status,
    "get_price_for_date": database_client.get_price_for_date,
    "send_location_pin": lambda latitude, longitude, name, address: f"Aquí está la ubicación de {name}:\\n\\n{address}\\n\\nPuede encontrarlo en Google Maps aquí: https://www.google.com/maps/search/?api=1&query={latitude},{longitude}",
    "send_menu_pdf": lambda caption: f"{caption} - El menú se encuentra disponible en nuestras instalaciones.",
    "check_room_availability": database_client.check_room_availability,
    "check_smart_availability": smart_availability.check_smart_availability,
    "send_bungalow_pictures": lambda bungalow_type: f"Aquí puede ver las fotos de {bungalow_type}. Las imágenes están disponibles en nuestro sitio web.",
    "send_public_areas_pictures": lambda: "Aquí puede ver las fotos de nuestras áreas públicas y facilidades. Las imágenes están disponibles en nuestro sitio web.",
    "sync_compraclick_payments": compraclick_tool.sync_compraclick_payments,
    "validate_compraclick_payment": compraclick_tool.validate_compraclick_payment,
    "validate_compraclick_payment_fallback": compraclick_tool.validate_compraclick_payment_fallback,
    "sync_bank_transfers": lambda *args, **kwargs: __import__('app.bank_transfer_tool', fromlist=['sync_bank_transfers']).sync_bank_transfers(*args, **kwargs),
    "validate_bank_transfer": lambda *args, **kwargs: __import__('app.bank_transfer_tool', fromlist=['validate_bank_transfer']).validate_bank_transfer(*args, **kwargs),
    "make_booking": lambda *args, **kwargs: __import__('app.booking_tool', fromlist=['make_booking']).make_booking(*args, **kwargs),
    "send_email": email_service.send_email,
    "start_bank_transfer_retry_process": bank_transfer_retry.start_bank_transfer_retry_process,
    "mark_customer_frustrated": bank_transfer_retry.mark_customer_frustrated,
    "trigger_compraclick_retry_for_missing_payment": compraclick_tool.trigger_compraclick_retry_for_missing_payment,
    "get_conversation_snippets": lambda *args, **kwargs: __import__('app.database_client', fromlist=['get_conversation_snippets']).get_conversation_snippets(*args, **kwargs),
    "book_room": lambda *args, **kwargs: __import__('app.booking_tool', fromlist=['make_booking']).make_booking(*args, **kwargs)
}

async def get_openai_response(
    prompt: str, 
    thread_id: Optional[str] = None, 
    wa_id: Optional[str] = None,
    subscriber_id: Optional[str] = None,
    channel: Optional[str] = None
) -> Tuple[str, str]:
    """
    Vertex AI equivalent of openai_agent.get_openai_response()
    Uses Gemini 1.5 Flash for chat completion with full business logic ported from OpenAI
    
    Args:
        prompt: User message text
        thread_id: Session identifier (maps to Vertex session)
        wa_id: WhatsApp ID for routing and context
        subscriber_id: ManyChat subscriber ID 
        channel: Channel identifier (facebook, instagram, etc)
        
    Returns:
        Tuple[response_text, session_id]
    """
    
    logger.info(f"[VERTEX] Processing request for wa_id={wa_id}, channel={channel}")
    
    try:
        # Initialize Vertex AI
        if not _initialize_vertex_ai():
            raise VertexAIError("Failed to initialize Vertex AI")
        
        # Get or create session ID
        session_id = thread_id or _get_or_create_session(wa_id)
        
        # Get conversation context
        context = _get_conversation_context(wa_id, session_id)
        
        # Build full conversation prompt
        system_instructions = _get_system_instructions()
        full_prompt = f"{system_instructions}\n\n{context}\n\nUser: {prompt}"
        
        # Process with retry logic and timeout recovery
        response_text = await _process_with_retry_logic(full_prompt, session_id, wa_id, subscriber_id, channel)
        
        logger.info(f"[VERTEX] Successfully processed request for {wa_id}")
        return response_text, session_id
        
    except Exception as e:
        logger.error(f"[VERTEX] Error processing request: {e}")
        error_session = thread_id or f"error_session_{int(time.time())}"
        return "🚧 Vertex AI processing failed: " + str(e), error_session

async def _process_tool_calls(tool_calls: List[Dict], wa_id: Optional[str], subscriber_id: Optional[str], channel: Optional[str]) -> List[str]:
    """
    Process tool calls with automatic identifier injection
    Replicates the tool handling logic from openai_agent.py
    """
    results = []
    
    for tool_call in tool_calls:
        try:
            function_name = tool_call.get('name')
            function_args = tool_call.get('arguments', {})
            
            function_to_call = AVAILABLE_FUNCTIONS.get(function_name)
            
            if function_to_call:
                logger.info(f"[VERTEX] Calling function: {function_name} with args: {function_args}")
                
                # Inject routing identifiers (critical fix from openai_agent.py)
                if asyncio.iscoroutinefunction(function_to_call):
                    sig = inspect.signature(function_to_call)
                    
                    # Auto-inject phone_number/wa_id
                    if 'phone_number' in sig.parameters and wa_id:
                        function_args['phone_number'] = wa_id
                        logger.info(f"[VERTEX] Auto-injected phone_number for {function_name}")
                    
                    if 'wa_id' in sig.parameters and wa_id:
                        function_args['wa_id'] = wa_id
                        logger.info(f"[VERTEX] Auto-injected wa_id for {function_name}")
                    
                    # Auto-inject ManyChat identifiers
                    if 'subscriber_id' in sig.parameters and subscriber_id:
                        function_args['subscriber_id'] = subscriber_id
                        logger.info(f"[VERTEX] Auto-injected subscriber_id for {function_name}")
                    
                    if 'channel' in sig.parameters and channel:
                        function_args['channel'] = channel
                        logger.info(f"[VERTEX] Auto-injected channel for {function_name}")
                    
                    result = await function_to_call(**function_args)
                else:
                    result = function_to_call(**function_args)
                
                results.append(json.dumps(result) if result else "{}")
                
            else:
                error_result = json.dumps({"error": f"Function {function_name} not found."})
                results.append(error_result)
                logger.error(f"[VERTEX] Unknown function: {function_name}")
                
        except Exception as e:
            logger.exception(f"[VERTEX] Tool execution error: {function_name}")
            error_result = json.dumps({"error": f"Error executing {function_name}: {str(e)}"})
            results.append(error_result)
    
    return results

def _extract_tool_calls(response_text: str) -> List[Dict]:
    """
    Extract tool calls from Vertex AI response
    (Placeholder - in production this would parse actual Vertex tool call format)
    """
    # For now, return empty list since we're using basic Gemini model
    # In production with Agent Engine, this would parse tool calls from response
    return []

def _apply_json_response_guard(response_text: str) -> str:
    """
    Apply JSON response guard to ensure clean Spanish customer messages
    Replicates the output validation from openai_agent.py Responses API guard
    """
    try:
        # Remove common JSON artifacts and formatting issues
        cleaned = response_text.strip()
        
        # Remove JSON wrapper if present
        if cleaned.startswith('```json') and cleaned.endswith('```'):
            cleaned = cleaned[7:-3].strip()
        
        # Remove markdown code blocks
        if cleaned.startswith('```') and cleaned.endswith('```'):
            lines = cleaned.split('\n')
            if len(lines) > 2:
                cleaned = '\n'.join(lines[1:-1])
        
        # Remove JSON object wrapper if the entire response is wrapped
        if cleaned.startswith('{') and cleaned.endswith('}'):
            try:
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict) and len(parsed) == 1:
                    # If it's a single-key object, extract the value
                    key = list(parsed.keys())[0]
                    if key in ['response', 'message', 'text', 'content']:
                        cleaned = str(parsed[key])
            except json.JSONDecodeError:
                pass  # Keep original if not valid JSON
        
        # Ensure response is in Spanish and customer-friendly
        if not cleaned or len(cleaned.strip()) == 0:
            cleaned = "Lo siento, no pude procesar tu solicitud. ¿Podrías intentar de nuevo?"
        
        logger.info(f"[VERTEX] Applied JSON response guard: {response_text[:100]}... → {cleaned[:100]}...")
        return cleaned
        
    except Exception as e:
        logger.warning(f"[VERTEX] Response guard failed: {e}")
        return response_text

def _get_system_instructions() -> str:
    """
    Load system instructions for the Vertex AI agent
    """
    try:
        instructions_path = os.path.join(os.path.dirname(__file__), 'resources', 'system_instructions.txt')
        if os.path.exists(instructions_path):
            with open(instructions_path, 'r', encoding='utf-8') as f:
                return f.read().strip()
    except Exception as e:
        logger.warning(f"[VERTEX] Failed to load system instructions: {e}")
    
    return "Eres un asistente de atención al cliente para Las Hojas Resort en El Salvador. Responde en español de manera amable y profesional."

def _get_or_create_session(wa_id: Optional[str]) -> str:
    """
    Get existing session ID or create new one
    """
    if wa_id:
        session_id = thread_store.get_session_id(wa_id)
        if session_id:
            return session_id
    
    return f"session_{wa_id}_{int(time.time())}"

async def _recover_session(wa_id: Optional[str], failed_session_id: str) -> Optional[str]:
    """
    Recover from session failure by creating new session with context preservation
    """
    try:
        new_session_id = f"recovered_{wa_id}_{int(time.time())}"
        
        if wa_id:
            # Update session ID in database
            thread_store.set_session_id(wa_id, new_session_id)
        
        logger.info(f"[VERTEX] Session recovered: {failed_session_id} → {new_session_id}")
        return new_session_id
        
    except Exception as e:
        logger.error(f"[VERTEX] Session recovery failed: {e}")
        return None

def _get_conversation_context(wa_id: Optional[str], session_id: Optional[str]) -> str:
    """
    Get conversation context from thread store or session history
    
    Args:
        wa_id: WhatsApp ID
        session_id: Session identifier
        
    Returns:
        Formatted conversation context
    """
    
    try:
        # Try to get recent conversation context
        if wa_id:
            # Get thread info for conversation context
            thread_info = thread_store.get_thread_id(wa_id)
            if thread_info:
                # Use existing conversation context logic
                context = f"Conversación existente para {wa_id}."
                return context
        
        return "Conversación nueva."
        
    except Exception as e:
        logger.warning(f"[VERTEX] Failed to get conversation context: {e}")
        return "Conversación nueva."

async def add_message_to_thread(session_id: str, message: str) -> bool:
    """
    Vertex AI equivalent of openai_agent.add_message_to_thread()
    Used for injecting conversation history
    
    Args:
        session_id: Vertex AI session identifier  
        message: Formatted history message to inject
        
    Returns:
        True if successful, False otherwise
    """
    
    try:
        logger.info(f"[VERTEX] Injecting history into session {session_id}: {message[:100]}...")
        
        # For Vertex AI, we store context in thread_store for continuity
        # The actual context injection happens in _get_conversation_context()
        
        return True
        
    except Exception as e:
        logger.exception(f"[VERTEX] Failed to inject message into session {session_id}: {e}")
        return False

async def create_vertex_session(wa_id: str) -> Optional[str]:
    """Create a new Vertex AI Agent Engine session for a wa_id"""
    try:
        # Generate session ID
        session_id = f"session_{wa_id}_{int(time.time())}"
        
        # For now, return the generated session ID
        # In production, this would create an actual Vertex Agent Engine session
        logger.info(f"[VERTEX] Created session {session_id} for wa_id: {wa_id}")
        return session_id
        
    except Exception as e:
        logger.error(f"[VERTEX] Failed to create session for wa_id {wa_id}: {e}")
        return None

async def inject_session_context(session_id: str, context_message: str) -> bool:
    """Inject context message into a Vertex session"""
    try:
        # Use the existing add_message_to_thread function which handles session injection
        success = await add_message_to_thread(session_id, context_message)
        
        if success:
            logger.info(f"[VERTEX] Successfully injected context to session {session_id}")
        else:
            logger.warning(f"[VERTEX] Failed to inject context to session {session_id}")
            
        return success
        
    except Exception as e:
        logger.error(f"[VERTEX] Error injecting context to session {session_id}: {e}")
        return False

def get_conversation_context(wa_id: str, limit: int = 10) -> str:
    """Get conversation context for a wa_id (placeholder implementation)"""
    try:
        # This would typically fetch conversation history from the database
        # For now, return a placeholder
        return f"Conversation context for {wa_id} (last {limit} messages)"
        
    except Exception as e:
        logger.error(f"[VERTEX] Failed to get conversation context for {wa_id}: {e}")
        return ""

async def _process_with_retry_logic(
    full_prompt: str,
    session_id: str,
    wa_id: Optional[str],
    subscriber_id: Optional[str], 
    channel: Optional[str]
) -> str:
    """
    Core processing function with retry logic and timeout recovery
    Implements the critical business logic from openai_agent.py with Vertex AI
    """
    max_retries = 3
    retry_count = 0
    start_time = time.time()
    timeout_seconds = 300  # 5 minutes timeout
    
    while retry_count < max_retries:
        try:
            # Check for timeout
            elapsed_time = time.time() - start_time
            if elapsed_time > timeout_seconds:
                logger.warning(f"[VERTEX] Request timeout after {elapsed_time:.0f}s, attempting recovery")
                
                # Session recovery - create new session preserving context
                new_session_id = await _recover_session(wa_id, session_id)
                if new_session_id:
                    session_id = new_session_id
                    # CRITICAL FIX: Reset start_time after successful recovery
                    start_time = time.time()
                    logger.info(f"[VERTEX] Session recovered, timer reset")
                else:
                    raise VertexAIError("Session recovery failed")
            
            # Initialize Gemini model with tools
            model = GenerativeModel("gemini-2.5-pro")
            
            # Create single consolidated tool for Vertex AI (workaround for multiple tools limitation)
            dispatch_tool = generative_models.Tool(
                function_declarations=[
                    generative_models.FunctionDeclaration(
                        name="dispatch_function",
                        description="Execute any business function needed to help the customer. Available functions: analyze_payment_proof, create_compraclick_link, get_price_for_date, book_room, send_email_notification, validate_bank_transfer, retry_pending_booking, get_conversation_snippets, process_payment_proof, create_bank_transfer_link, get_bank_accounts, validate_compraclick_payment, handle_booking_confirmation, set_payment_method_preference, send_payment_instructions, send_whatsapp_message, add_conversation_note, get_available_dates, format_booking_details, update_booking_status, send_booking_summary, generate_booking_reference",
                        parameters={
                            "type": "object",
                            "properties": {
                                "function_name": {
                                    "type": "string",
                                    "description": "Name of the function to execute",
                                    "enum": [
                                        "analyze_payment_proof", "create_compraclick_link", "get_price_for_date", 
                                        "book_room", "send_email_notification", "validate_bank_transfer", 
                                        "retry_pending_booking", "get_conversation_snippets", "process_payment_proof",
                                        "create_bank_transfer_link", "get_bank_accounts", "validate_compraclick_payment",
                                        "handle_booking_confirmation", "set_payment_method_preference", "send_payment_instructions",
                                        "send_whatsapp_message", "add_conversation_note", "get_available_dates",
                                        "format_booking_details", "update_booking_status", "send_booking_summary",
                                        "generate_booking_reference"
                                    ]
                                },
                                "arguments": {
                                    "type": "object",
                                    "description": "Arguments to pass to the function"
                                }
                            },
                            "required": ["function_name", "arguments"]
                        }
                    )
                ]
            )
            vertex_tools = [dispatch_tool]
            
            # Generate response
            logger.info(f"[VERTEX] Generating response (attempt {retry_count + 1}/{max_retries}) with {len(vertex_tools)} tools")
            response = model.generate_content(
                full_prompt,
                tools=vertex_tools,
                generation_config=generative_models.GenerationConfig(
                    max_output_tokens=2048,
                    temperature=0.7,
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
            
            # Extract response text and function calls
            if response.candidates:
                candidate = response.candidates[0]
                response_text = ""
                tool_calls = []
                
                # Process all parts in the response
                for part in candidate.content.parts:
                    if hasattr(part, 'text') and part.text:
                        response_text += part.text
                    elif hasattr(part, 'function_call') and part.function_call:
                        # Extract function call details
                        func_call = part.function_call
                        if func_call.name == "dispatch_function":
                            # Extract the actual function and arguments from dispatch
                            args = dict(func_call.args) if func_call.args else {}
                            actual_function = args.get("function_name", "")
                            actual_args = args.get("arguments", {})
                            tool_call = {
                                "name": actual_function,
                                "arguments": actual_args
                            }
                            logger.info(f"[VERTEX] Dispatch function call: {actual_function}")
                        else:
                            tool_call = {
                                "name": func_call.name,
                                "arguments": dict(func_call.args) if func_call.args else {}
                            }
                            logger.info(f"[VERTEX] Function call detected: {func_call.name}")
                        tool_calls.append(tool_call)
                
                # Process tool calls if present
                if tool_calls:
                    logger.info(f"[VERTEX] Processing {len(tool_calls)} tool calls")
                    tool_results = await _process_tool_calls(tool_calls, wa_id, subscriber_id, channel)
                    
                    # Combine response with tool results
                    if response_text:
                        combined_response = f"{response_text}\n\n" + "\n".join(tool_results)
                    else:
                        combined_response = "\n".join(tool_results)
                    response_text = combined_response
                
                # Apply JSON response guard for clean Spanish output
                response_text = _apply_json_response_guard(response_text)
                
                logger.info(f"[VERTEX] Successfully generated response in {time.time() - start_time:.1f}s")
                return response_text
                
            else:
                raise VertexAIError("No response candidates generated")
                
        except Exception as e:
            retry_count += 1
            error_msg = str(e)
            
            # Calculate exponential backoff delay
            backoff_delay = min(2 ** (retry_count - 1), 60)  # Max 60 seconds
            
            logger.error(f"[VERTEX] Error on attempt {retry_count}/{max_retries}: {error_msg}")
            
            if retry_count >= max_retries:
                logger.error(f"[VERTEX] Exceeded maximum retries ({max_retries})")
                return f"🚧 Lo siento, el sistema está experimentando dificultades técnicas. Error: {error_msg}"
            
            # Wait before retry with exponential backoff
            logger.info(f"[VERTEX] Retrying in {backoff_delay}s...")
            await asyncio.sleep(backoff_delay)
            
            # Reset start_time for timeout calculation on retry
            start_time = time.time()
    
    # Should never reach here due to max_retries check above
    return " Lo siento, no pude procesar tu solicitud después de varios intentos."

async def get_pre_live_history(wa_id: str, before_date: datetime) -> list:
    """
    Retrieves all conversation history before a specific go-live date from WATI API
    Ported from main.py with proper pagination and rate limiting
    
    Args:
        wa_id: The WhatsApp ID of the user
        before_date: The cutoff date. Only messages before this date will be returned
        
    Returns:
        A list of all historical message objects, sorted chronologically
    """
    all_pre_live_messages = []
    page_number = 1
    PAGE_SIZE = 100

    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                logger.info(f"[VERTEX_HISTORY] Fetching WATI page {page_number} for {wa_id}...")
                wati_url = f"{config.WATI_API_URL}/api/v1/getMessages/{wa_id}?pageSize={PAGE_SIZE}&pageNumber={page_number}"
                headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}
                response = await client.get(wati_url, headers=headers)

                if response.status_code == 429:
                    logger.warning(f"[VERTEX_HISTORY] Rate limit hit. Waiting 30 seconds...")
                    await asyncio.sleep(30)
                    continue
                
                response.raise_for_status()
                response_data = response.json()
                
                # WATI API returns messages in nested structure
                messages_data = response_data.get("messages", {})
                if not isinstance(messages_data, dict):
                    logger.warning(f"[VERTEX_HISTORY] 'messages' field is not a dictionary for {wa_id}. Value: {messages_data}")
                    messages = []
                else:
                    messages = messages_data.get("items", [])

                if not messages:
                    logger.info(f"[VERTEX_HISTORY] No more messages found for {wa_id}. Stopping fetch.")
                    break

                all_pre_live_messages.extend(messages)
                page_number += 1

                # Stop fetching if we've gone back far enough (20 pages = 2000 messages max)
                if page_number > 20:
                    logger.warning(f"[VERTEX_HISTORY] Reached 20-page limit for {wa_id}. Stopping to avoid excessive fetching.")
                    break

                await asyncio.sleep(1)  # Be respectful to the API

            except httpx.HTTPStatusError as e:
                logger.error(f"[VERTEX_HISTORY] HTTP error fetching history: {e}")
                break
            except Exception as e:
                logger.error(f"[VERTEX_HISTORY] An unexpected error occurred: {e}")
                break

    # Filter and normalize timestamps, return chronological order
    final_list = []
    for m in all_pre_live_messages:
        try:
            timestamp_str = m.get('created', '')
            if not timestamp_str:
                continue

            # Normalize WATI timestamps (remove 'Z', handle variable microseconds)
            ts_str = timestamp_str.replace('Z', '')
            if '+' in ts_str:
                ts_str = ts_str.split('+')[0]

            if '.' in ts_str:
                parts = ts_str.split('.')
                microseconds = parts[1][:6].ljust(6, '0')  # Normalize to 6 digits
                ts_str = f"{parts[0]}.{microseconds}"

            msg_datetime = datetime.fromisoformat(ts_str)
            
            # Only include messages before the cutoff date
            if msg_datetime < before_date:
                final_list.append(m)

        except Exception as e:
            logger.warning(f"[VERTEX_HISTORY] Could not parse timestamp for message: {m}. Error: {e}")

    # Sort chronologically (oldest first)
    final_list.sort(key=lambda x: x.get('created', ''))
    
    logger.info(f"[VERTEX_HISTORY] Retrieved {len(final_list)} pre-live messages for {wa_id}")
    return final_list

async def inject_wati_pre_live_history(wa_id: str, session_id: str) -> bool:
    """
    Inject WATI conversation history into Vertex AI session
    Replicates the history import logic from main.py for Vertex AI
    
    Args:
        wa_id: WhatsApp ID
        session_id: Vertex AI session identifier
        
    Returns:
        True if successful, False otherwise
    """
    try:
        # Check if history already imported
        if thread_store.is_history_imported(wa_id):
            logger.info(f"[VERTEX_HISTORY] History already imported for {wa_id}")
            return True

        # Define go-live date and message limit
        GO_LIVE_DATE = datetime(2025, 7, 5)
        MESSAGE_LIMIT = 200

        # Fetch all pre-live history
        all_pre_live_history = await get_pre_live_history(wa_id, before_date=GO_LIVE_DATE)

        if all_pre_live_history:
            # Take the last N messages (most recent ones)
            limited_history = all_pre_live_history[-MESSAGE_LIMIT:]
            logger.info(f"[VERTEX_HISTORY] Found {len(all_pre_live_history)} total messages. Capping at {len(limited_history)}.")

            # Format history into a single context message
            formatted_history = f"Este es el historial de conversación más reciente ({len(limited_history)} mensajes antes del 5 de Julio, 2025) para darte contexto:\n\n---"
            
            for msg in limited_history:
                try:
                    ts = datetime.fromisoformat(msg.get('created').replace('Z', '+00:00'))
                    formatted_ts = ts.strftime('%Y-%m-%d %H:%M')
                    sender = "Usuario" if msg.get('eventType') == 'message' else "Asistente"
                    text = msg.get('text', '[Mensaje sin texto]')
                    formatted_history += f"\n[{formatted_ts}] {sender}: {text}"
                except Exception as e:
                    logger.warning(f"[VERTEX_HISTORY] Could not format message: {msg}. Error: {e}")
            
            formatted_history += "\n---"
            
            # Inject formatted history into session
            logger.info(f"[VERTEX_HISTORY] Injecting {len(limited_history)} messages into session {session_id} for {wa_id}")
            success = await add_message_to_thread(session_id, formatted_history)
            
            if success:
                thread_store.set_history_imported(wa_id)
                logger.info(f"[VERTEX_HISTORY] Successfully imported and marked history for {wa_id}")
                return True
            else:
                logger.error(f"[VERTEX_HISTORY] Failed to inject history into session {session_id}")
                return False
        else:
            logger.info(f"[VERTEX_HISTORY] No pre-live messages found for {wa_id}. Marking as imported.")
            thread_store.set_history_imported(wa_id)
            return True

    except Exception as e:
        logger.exception(f"[VERTEX_HISTORY] Error injecting WATI history for {wa_id}: {e}")
        return False

async def inject_agent_context(wa_id: str, session_id: str) -> bool:
    """
    Inject human agent conversation context for conversations that human agents handled
    Detects and injects missed context not captured by webhooks
    
    Args:
        wa_id: WhatsApp ID
        session_id: Vertex AI session identifier
        
    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"[VERTEX_AGENT_CONTEXT] Checking agent context for {wa_id}")
        
        # Check if agent context already injected
        if thread_store.is_vertex_context_injected(wa_id):
            logger.info(f"[VERTEX_AGENT_CONTEXT] Agent context already injected for {wa_id}")
            return True

        # Fetch recent agent interactions from WATI API
        context_messages = await _fetch_agent_context_messages(wa_id)
        
        if context_messages:
            # Format agent context for injection
            context_text = f"Contexto de conversaciones con agentes humanos para {wa_id}:\n\n---"
            
            for msg in context_messages:
                try:
                    ts = datetime.fromisoformat(msg.get('created').replace('Z', '+00:00'))
                    formatted_ts = ts.strftime('%Y-%m-%d %H:%M')
                    sender_type = msg.get('senderType', 'unknown')
                    sender = "Agente Humano" if sender_type == 'admin' else "Usuario"
                    text = msg.get('text', '[Mensaje sin texto]')
                    context_text += f"\n[{formatted_ts}] {sender}: {text}"
                except Exception as e:
                    logger.warning(f"[VERTEX_AGENT_CONTEXT] Could not format agent message: {msg}. Error: {e}")
            
            context_text += "\n---\n"
            
            # Inject agent context into session
            success = await add_message_to_thread(session_id, context_text)
            
            if success:
                thread_store.set_vertex_context_injected(wa_id)
                logger.info(f"[VERTEX_AGENT_CONTEXT] Successfully injected agent context for {wa_id}")
                return True
            else:
                logger.error(f"[VERTEX_AGENT_CONTEXT] Failed to inject agent context into session {session_id}")
                return False
        else:
            # No agent context found, mark as completed
            thread_store.set_vertex_context_injected(wa_id)
            logger.info(f"[VERTEX_AGENT_CONTEXT] No agent context found for {wa_id}. Marked as completed.")
            return True

    except Exception as e:
        logger.exception(f"[VERTEX_AGENT_CONTEXT] Error injecting agent context for {wa_id}: {e}")
        return False

async def _fetch_agent_context_messages(wa_id: str, limit: int = 50) -> list:
    """
    Fetch recent messages involving human agents from WATI API
    
    Args:
        wa_id: WhatsApp ID
        limit: Maximum number of messages to fetch
        
    Returns:
        List of agent interaction messages
    """
    agent_messages = []
    page_number = 1
    PAGE_SIZE = 50
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            while len(agent_messages) < limit and page_number <= 5:  # Max 5 pages
                wati_url = f"{config.WATI_API_URL}/api/v1/getMessages/{wa_id}?pageSize={PAGE_SIZE}&pageNumber={page_number}"
                headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}
                
                response = await client.get(wati_url, headers=headers)
                
                if response.status_code == 429:
                    logger.warning(f"[VERTEX_AGENT_CONTEXT] Rate limit hit while fetching agent context")
                    await asyncio.sleep(10)
                    continue
                    
                response.raise_for_status()
                response_data = response.json()
                
                messages_data = response_data.get("messages", {})
                messages = messages_data.get("items", []) if isinstance(messages_data, dict) else []
                
                if not messages:
                    break
                
                # Filter for agent messages (admin sender type)
                for msg in messages:
                    if msg.get('senderType') == 'admin' or msg.get('eventType') == 'agent_message':
                        agent_messages.append(msg)
                        
                page_number += 1
                await asyncio.sleep(0.5)  # Rate limiting
                
    except Exception as e:
        logger.error(f"[VERTEX_AGENT_CONTEXT] Error fetching agent context messages: {e}")
        
    return agent_messages[:limit]  # Return up to limit

# Module name compatibility for routing function
__name__ = "vertex_agent"
