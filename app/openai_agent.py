import asyncio
import inspect
import json
import logging
import os
import sqlite3
import time
from typing import Dict, List, Any, Optional, Tuple, Union
from datetime import datetime, timedelta
import re
import httpx

from openai import AsyncOpenAI
from . import config, database_client
from . import compraclick_tool
from . import payment_proof_analyzer
from app import bank_transfer_tool
from app import bank_transfer_retry
from app import booking_tool, email_service
from app.booking_tool import process_pending_booking_if_needed
from app import smart_availability
from app import office_status_tool
from pytz import timezone
from app import wati_client
from app.clients import manychat_client
from app import menu_reader
from app import operations_tool
from app.message_humanizer import humanize_response

logger = logging.getLogger(__name__)

# Initialize OpenAI client for Responses API
openai_client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)


def _extract_text_from_output(output_items: List[Any]) -> str:
    """Extract text content from Responses API output items.
    
    Handles different output item types and content block structures.
    """
    if not output_items:
        return ""
    
    parts = []
    for item in output_items:
        if getattr(item, "type", None) == "message":
            for b in getattr(item, "content", []) or []:
                bt = getattr(b, "type", None)
                if bt in ("output_text", "text"):
                    txt = getattr(b, "text", None)
                    if txt:
                        parts.append(str(txt))
        elif getattr(item, "type", None) in ("output_text", "text"):
            txt = getattr(item, "text", None)
            if txt:
                parts.append(str(txt))
    return "\n".join(parts).strip()


def _iter_tool_calls(resp) -> List[Any]:
    """Yield all tool calls from a Responses API response, whether top-level or nested in a message's content."""
    calls = []
    for item in (getattr(resp, "output", None) or []):
        t = getattr(item, "type", None)

        # Case A: top-level tool call
        if t in ("tool_call", "function_call", "tool_use"):
            calls.append(item)
            continue

        # Case B: inside a message's content blocks
        if t == "message":
            for block in (getattr(item, "content", None) or []):
                bt = getattr(block, "type", None)
                if bt in ("tool_call", "function_call", "tool_use"):
                    calls.append(block)

    return calls


def _tool_args(raw_args: Any) -> dict:
    """Normalize tool arguments to a dict."""
    if isinstance(raw_args, dict):
        return raw_args
    if raw_args is None or raw_args == "":
        return {}
    try:
        return json.loads(raw_args)
    except Exception:
        # If the model accidentally emitted non-JSON, don't crash
        return {}


def _coerce_output_str(result: Any) -> str:
    """Coerce any Python result to a string accepted by submit_tool_outputs."""
    if result is None:
        return ""
    if isinstance(result, (str, bytes)):
        return result.decode() if isinstance(result, bytes) else result
    try:
        return json.dumps(result, ensure_ascii=False)
    except Exception:
        return str(result)




# Add system instruction loading function
def load_system_instructions():
    """Load system instructions from file."""
    with open('app/resources/system_instructions.txt', 'r', encoding='utf-8') as f:
        return f.read()

tools = [
    {
        "type": "function",
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
    },
    {
        "type": "function",
        "name": "create_compraclick_link",
        "description": "Creates a CompraClick payment link for a customer. Use this when the customer wants to pay via credit card online.",
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
    },
    {
        "type": "function",
        "name": "check_office_status",
        "description": "MANDATORY: Check customer service office status and automation eligibility. MUST be called before attempting any booking. Returns office status (open/closed) and whether automation is allowed. Can also be called when users ask about office hours.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
        "name": "get_price_for_date",
        "description": "Get the price for a specific date for all available packages: Day Pass/Pasadía (pa_adulto, pa_nino), Accommodation/Las Hojas (lh_adulto, lh_nino), and Paquete Escapadita (es_adulto, es_nino). IMPORTANT: For daypass/pasadía questions use pa_ prices, for accommodation/overnight stays use lh_ prices. The assistant can also use this function on the reverse to check which dates have the prices the customer is interested in, this is useful when the customer has seen a promotion and is asking which dates contain that promotion price.",
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
    },
    {
        "type": "function",
        "name": "send_location_pin",
        "description": "Formats the business location details into a text message with a Google Maps link. CRITICAL: You MUST include the exact output returned by this function in your response to the user - do not create your own location text.",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
        "name": "read_menu_content",
        "description": "Converts the current menu PDF to high-resolution PNG images for visual analysis. Use this to answer specific questions about food items, prices, or menu sections by examining the actual menu layout and visual content. This provides accurate information about dish names, prices, and descriptions exactly as they appear in the menu without text extraction errors.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
        "name": "send_public_areas_pictures",
        "description": "Sends pictures of the public areas of the hotel (restaurant, pool, common areas, etc.). Use this when the user asks for photos of the facilities or public spaces.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
        "name": "sync_compraclick_payments",
        "description": "Synchronizes CompraClick payments by downloading the latest transaction report. Run this before validating a CompraClick payment.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
        "name": "sync_bank_transfers",
        "description": "Synchronizes bank transfer payments by downloading the latest transaction report. Run this before validating a bank transfer.",
        "parameters": {"type": "object", "properties": {}, "required": []}
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
        "name": "send_email",
        "description": "Sends an email to internal hotel staff based on specific use cases. CRITICAL: This tool is ONLY for internal notifications and MUST NOT be used to email customers. The to_emails parameter is strictly validated. Use cases: 1. For membership inquiries, email promociones@lashojasresort.com. 2. For group quotes (20+ people), email sbartenfeld@lashojasresort.com and acienfuegos@lashojasresort.com. 3. For last-minute customer information for reception, email reservas@lashojasresort.com.",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
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
    },
    {
        "type": "function",
        "name": "notify_operations_department",
        "description": "Send urgent notification to Operations Department (50377976000) when guests at the hotel report issues with rooms, service, or facilities that need immediate attention. Use this ONLY when guests are physically present at the hotel and need immediate assistance that cannot wait for email resolution. IMPORTANT: Extract ALL information from conversation context - do NOT re-ask for details already provided by the guest.",
        "parameters": {
            "type": "object",
            "properties": {
                "guest_name": {
                    "type": "string",
                    "description": "Name of the guest reporting the issue (extract from conversation context if mentioned, use 'Huésped' if not provided)"
                },
                "guest_phone": {
                    "type": "string", 
                    "description": "Guest's phone number for contact (for WATI users: extract from wa_id, for ManyChat: use channel identifier or ask only if critical)"
                },
                "issue_type": {
                    "type": "string",
                    "description": "Type of issue being reported (categorize automatically from guest's description)",
                    "enum": ["Habitación/Room", "Servicio/Service", "Instalaciones/Facilities", "Mantenimiento/Maintenance", "Limpieza/Cleaning", "Restaurante/Restaurant", "Otro/Other"]
                },
                "issue_description": {
                    "type": "string",
                    "description": "Detailed description of the issue in Spanish (use EXACTLY what the guest already described - do NOT ask them to repeat it)"
                },
                "guest_location": {
                    "type": "string",
                    "description": "Guest's current location (use specific location if mentioned by guest, otherwise default to 'Instalaciones del hotel')"
                }
            },
            "required": ["issue_type", "issue_description"]
        }
    }
]

async def add_message_to_thread(thread_id: str, content: str):
    """Adds a message to an existing OpenAI conversation for history import using Responses API."""
    try:
        logger.info(f"[HISTORY_IMPORT] Adding historical context to conversation {thread_id}...")
        
        # Use the Responses API to add context to conversation object
        # Get last response ID from local storage - need user_identifier to look up
        # This function is called during history import, so we need to extract wa_id from context
        from .thread_store import get_last_response_id
        
        # Extract wa_id from thread_id lookup in database
        import sqlite3
        try:
            conn = sqlite3.connect("thread_store.db")
            cursor = conn.cursor()
            cursor.execute("SELECT wa_id FROM threads WHERE thread_id = ? OR conversation_id = ?", (thread_id, thread_id))
            result = cursor.fetchone()
            conn.close()
            
            if result:
                user_identifier = result[0]
                previous_response_id = get_last_response_id(user_identifier)
                logger.info(f"[HISTORY_IMPORT] Retrieved previous_response_id from local storage for {user_identifier}: {previous_response_id}")
            else:
                previous_response_id = None
                logger.warning(f"[HISTORY_IMPORT] Could not find wa_id for thread {thread_id}")
        except Exception as e:
            logger.warning(f"[HISTORY_IMPORT] Could not get previous response ID from local storage: {e}")
            previous_response_id = None
        
        if previous_response_id:
            response = await openai_client.responses.create(
                model="gpt-5",
                previous_response_id=previous_response_id,
                input=[
                    {
                        "type": "message",
                        "role": "system", 
                        "content": [{"type": "input_text", "text": content}]
                    }
                ],
                max_output_tokens=16
            )
            save_response_id(user_identifier, response.id)
        else:
            response = await openai_client.responses.create(
                model="gpt-5",
                conversation=thread_id,
                input=[
                    {
                        "type": "message",
                        "role": "system", 
                        "content": [{"type": "input_text", "text": content}]
                    }
                ],
                max_output_tokens=16
            )
            save_response_id(user_identifier, response.id)
        
        logger.info(f"[HISTORY_IMPORT] Successfully added historical context to conversation {thread_id}.")
        return True
    except Exception as e:
        logger.error(f"[HISTORY_IMPORT] An unexpected error occurred while adding message to conversation {thread_id}: {e}")
        return False

async def get_thread_messages(thread_id: str, limit: int = 10) -> list:
    """Retrieves the most recent messages from a given OpenAI conversation."""
    if not thread_id:
        return []
    try:
        logger.info(f"[CONTEXT] Note: get_thread_messages deprecated with Responses API - conversation history is automatic")
        return []
    except Exception as e:
        logger.error(f"[CONTEXT] Error retrieving messages from thread {thread_id}: {e}")
        return []

def format_location_as_text(latitude: float, longitude: float, name: str, address: str) -> str:
    """Formats location data into a user-friendly text message with a Google Maps link."""
    maps_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
    return f"Aquí está la ubicación de {name}:\n\n{address}\n\nPuede encontrarlo en Google Maps aquí: {maps_url}"

def generate_caption_from_filename(filename: str, bungalow_type: str) -> str:
    """Generates a user-friendly Spanish caption from a filename."""
    name_part = os.path.splitext(filename)[0]

    bungalow_map = {
        "Bungalow Familiar": "bungalow_familiar",
        "Bungalow Junior": "bungalow_junior",
        "Habitacion": "habitacion"
    }
    prefix_to_remove = bungalow_map.get(bungalow_type, "").lower().replace(" ", "_") + "_"
    if name_part.startswith(prefix_to_remove):
        name_part = name_part[len(prefix_to_remove):]

    name_part = name_part.replace('_', ' ').replace('-', ' ')

    translations = {
        "livingroom": "Sala de Estar",
        "masterbedroom": "Habitación Principal",
        "outside": "Vista Exterior",
        "terrace": "Terraza",
        "cuarto": "Habitación",
        "room": "Habitación",
        "bathroom": "Baño"
    }

    parts = re.findall(r'[a-zA-Z]+|\d+', name_part)
    
    description_parts = []
    for part in parts:
        if part.lower() in translations:
            description_parts.append(translations[part.lower()])
        else:
            description_parts.append(part.capitalize())
    
    description = " ".join(description_parts)

    return f"{description} - {bungalow_type}"

async def send_bungalow_pictures(
    bungalow_type: str,
    phone_number: Optional[str] = None,
    subscriber_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> str:
    """Send bungalow pictures to the user via ManyChat (FB/IG) or WhatsApp.

    Preference order:
    - If `subscriber_id` and `channel` ("facebook"|"instagram") are provided, send via ManyChat.
    - Else if `phone_number` is provided, send via WATI (WhatsApp).
    - Else return an error indicating missing identifiers.
    """
    base_path = "/home/robin/watibot3/app/resources/pictures"
    type_map = {
        "Bungalow Familiar": "bungalow_familiar",
        "Bungalow Junior": "bungalow_junior",
        "Habitacion": "habitacion"
    }
    
    dir_name = type_map.get(bungalow_type)
    if not dir_name:
        logger.error(f"Invalid bungalow type received: {bungalow_type}")
        return f"Error: El tipo de bungalow '{bungalow_type}' no es válido."

    picture_dir = os.path.join(base_path, dir_name)

    if not os.path.isdir(picture_dir):
        logger.error(f"Picture directory not found: {picture_dir}")
        return f"Lo siento, no pude encontrar el directorio de fotos para {bungalow_type}."

    pictures = sorted([f for f in os.listdir(picture_dir) if os.path.isfile(os.path.join(picture_dir, f))])

    if not pictures:
        logger.warning(f"No pictures found in {picture_dir}")
        return f"Lo siento, no hay fotos disponibles para {bungalow_type} en este momento."

    logger.info(
        f"Found {len(pictures)} pictures for {bungalow_type}. "
        f"Target -> channel={channel}, subscriber_id={subscriber_id}, phone_number={phone_number}"
    )
    for pic in pictures:
        file_path = os.path.join(picture_dir, pic)
        caption = generate_caption_from_filename(pic, bungalow_type)
        try:
            if subscriber_id and channel in ("facebook", "instagram"):
                await manychat_client.send_media_message(
                    subscriber_id=subscriber_id,
                    file_path=file_path,
                    media_type="image",
                    channel=channel,
                    caption=caption,
                )
            elif phone_number:
                await wati_client.send_wati_file(
                    phone_number=phone_number,
                    caption=caption,
                    file_path=file_path,
                )
            else:
                logger.error("No subscriber_id/channel or phone_number provided to send media.")
                return (
                    "Lo siento, no pude identificar tu canal para enviar las fotos. "
                    "Por favor intenta de nuevo."
                )
            await asyncio.sleep(1)  # Small delay to prevent flooding
        except Exception as e:
            logger.exception(f"Failed to send picture {file_path} to {phone_number}")
            return f"Tuve un problema al enviar una de las fotos. Por favor, inténtalo de nuevo."

    return f"He enviado {len(pictures)} foto(s) de {bungalow_type}. ¡Espero que te gusten!"

async def send_public_areas_pictures(
    phone_number: Optional[str] = None,
    subscriber_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> str:
    """Send public area pictures to the user via ManyChat (FB/IG) or WhatsApp.

    See `send_bungalow_pictures` for routing logic.
    """
    base_path = "/home/robin/watibot3/app/resources/pictures"
    picture_dir = os.path.join(base_path, "public_areas")

    if not os.path.isdir(picture_dir):
        logger.error(f"Public areas picture directory not found: {picture_dir}")
        return f"Lo siento, no pude encontrar el directorio de fotos de las áreas públicas."

    pictures = sorted([f for f in os.listdir(picture_dir) if os.path.isfile(os.path.join(picture_dir, f))])

    if not pictures:
        logger.warning(f"No pictures found in {picture_dir}")
        return f"Lo siento, no hay fotos disponibles de las áreas públicas en este momento."

    logger.info(
        f"Found {len(pictures)} public area pictures. "
        f"Target -> channel={channel}, subscriber_id={subscriber_id}, phone_number={phone_number}"
    )
    for pic in pictures:
        file_path = os.path.join(picture_dir, pic)
        caption = generate_caption_from_filename(pic, "Áreas Públicas")
        try:
            if subscriber_id and channel in ("facebook", "instagram"):
                await manychat_client.send_media_message(
                    subscriber_id=subscriber_id,
                    file_path=file_path,
                    media_type="image",
                    channel=channel,
                    caption=caption,
                )
            elif phone_number:
                await wati_client.send_wati_file(
                    phone_number=phone_number,
                    caption=caption,
                    file_path=file_path,
                )
            else:
                logger.error("No subscriber_id/channel or phone_number provided to send media.")
                return (
                    "Lo siento, no pude identificar tu canal para enviar las fotos. "
                    "Por favor intenta de nuevo."
                )
            await asyncio.sleep(1)  # Small delay to prevent flooding
        except Exception as e:
            logger.exception(f"Failed to send public area picture {file_path} to {phone_number}")
            return f"Tuve un problema al enviar una de las fotos de las áreas públicas. Por favor, inténtalo de nuevo."

    return f"He enviado {len(pictures)} foto(s) de las áreas públicas del hotel. ¡Espero que te gusten!"

async def send_menu_pdf_wrapper(
    caption: str,
    phone_number: Optional[str] = None,
    subscriber_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> dict:
    """Send the restaurant menu PDF via ManyChat (FB/IG) or WhatsApp.

    - Uses ManyChat when `subscriber_id` and `channel` provided.
    - Falls back to WATI (WhatsApp) when `phone_number` provided.
    """
    menu_pdf_path = "/home/robin/watibot3/app/resources/menu.pdf"
    if subscriber_id and channel in ("facebook", "instagram"):
        # Instagram: convert PDF to images and send as images (IG doesn't support PDF attachments)
        if channel == "instagram":
            try:
                import fitz  # PyMuPDF
                from pathlib import Path
                import time as _time
                out_dir = Path(__file__).resolve().parent / "resources" / "pictures" / "menu_converted"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = int(_time.time())
                doc = fitz.open(menu_pdf_path)
                image_paths = []
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(dpi=144)
                    img_path = out_dir / f"menu_{ts}_p{i+1}.png"
                    pix.save(str(img_path))
                    image_paths.append(str(img_path))
                doc.close()

                # Send images sequentially; include caption on first image only
                for idx, img_path in enumerate(image_paths):
                    cap = caption if idx == 0 else ""
                    await manychat_client.send_media_message(
                        subscriber_id=subscriber_id,
                        file_path=img_path,
                        media_type="image",
                        channel=channel,
                        caption=cap,
                    )
                return {"status": "sent", "channel": channel, "pages": len(image_paths)}
            except Exception as e:
                logger.warning(
                    f"[IG] PDF->image conversion unavailable or failed ({e}). Sending link as text."
                )
                # Fallback: send caption then link as text message
                await manychat_client.send_media_message(
                    subscriber_id=subscriber_id,
                    file_path=menu_pdf_path,
                    media_type="file",
                    channel=channel,
                    caption=caption,
                )
                return {"status": "sent_as_link", "channel": channel}

        # Facebook: send PDF as file attachment
        await manychat_client.send_media_message(
            subscriber_id=subscriber_id,
            file_path=menu_pdf_path,
            media_type="file",
            channel=channel,
            caption=caption,
        )
        return {"status": "sent", "channel": channel}
    elif phone_number:
        return await wati_client.send_wati_file(
            phone_number=phone_number, caption=caption, file_path=menu_pdf_path
        )
    else:
        logger.error("No subscriber_id/channel or phone_number provided to send menu PDF.")
        return {"error": "Missing recipient identifiers"}

available_functions = {
    "get_price_for_date": database_client.get_price_for_date,
    "send_location_pin": format_location_as_text,
    "send_menu_pdf": send_menu_pdf_wrapper,
    "read_menu_content": menu_reader.read_menu_content_wrapper,
    "analyze_payment_proof": payment_proof_analyzer.analyze_payment_proof,
    "check_office_status": office_status_tool.check_office_status,
    "check_room_availability": database_client.check_room_availability,
    "check_smart_availability": smart_availability.check_smart_availability,
    "send_bungalow_pictures": send_bungalow_pictures,
    "send_public_areas_pictures": send_public_areas_pictures,
    "create_compraclick_link": compraclick_tool.create_compraclick_link,
    "sync_compraclick_payments": compraclick_tool.sync_compraclick_payments,
    "validate_compraclick_payment": compraclick_tool.validate_compraclick_payment,
    "validate_compraclick_payment_fallback": compraclick_tool.validate_compraclick_payment_fallback,
    "sync_bank_transfers": bank_transfer_tool.sync_bank_transfers,
    "validate_bank_transfer": bank_transfer_tool.validate_bank_transfer,
    "start_bank_transfer_retry_process": bank_transfer_retry.start_bank_transfer_retry_process,
    "mark_customer_frustrated": bank_transfer_retry.mark_customer_frustrated,
    "trigger_compraclick_retry_for_missing_payment": compraclick_tool.trigger_compraclick_retry_for_missing_payment,
    "make_booking": booking_tool.make_booking,
    "send_email": email_service.send_email,
    "notify_operations_department": operations_tool.notify_operations_department,
}

async def rotate_conversation_thread(old_conversation_id: str, wa_id: str, current_message: str, system_instructions: str) -> str:
    """
    Creates a new conversation thread when the current one exceeds context window.
    Seeds the new thread with essential context from the old one.
    """
    try:
        logger.info(f"[THREAD_ROTATION] Starting rotation for wa_id: {wa_id}")
        
        # Create new conversation using Conversations API
        import httpx
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.openai.com/v1/conversations",
                headers={
                    "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={}
            )
            response.raise_for_status()
            conv_data = response.json()
            new_conversation_id = conv_data.get("id")
            if not new_conversation_id:
                raise RuntimeError("Conversations API returned no id")
        logger.info(f"[THREAD_ROTATION] Created new conversation: {new_conversation_id}")
        
        # NOTE: Context seeding now handled in get_openai_response via enhanced_developer_message
        # No separate seeding call needed - prevents context overflow issues
        
        # Update database with new conversation ID
        db_path = "app/thread_store.db"
        
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            
            # Create archived_threads table if it doesn't exist
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS archived_threads (
                    wa_id TEXT,
                    thread_id TEXT,
                    original_thread_id TEXT,
                    created_at TEXT,
                    archived_at TEXT,
                    agent_context_injected INTEGER
                )
            """)
            
            # Get the old thread data before deleting (wa_id is PRIMARY KEY)
            cursor.execute(
                "SELECT wa_id, thread_id, created_at, agent_context_injected FROM threads WHERE wa_id = ?", 
                (wa_id,)
            )
            old_thread_data = cursor.fetchone()
            
            if old_thread_data:
                # Archive the old conversation
                archived_name = f"{old_thread_data[1]}_archived_{int(time.time())}"  # Use actual thread_id from DB
                cursor.execute(
                    "INSERT INTO archived_threads (wa_id, thread_id, original_thread_id, created_at, archived_at, agent_context_injected) VALUES (?, ?, ?, ?, ?, ?)",
                    (old_thread_data[0], archived_name, old_thread_data[1], old_thread_data[2], datetime.now().isoformat(), old_thread_data[3] if len(old_thread_data) > 3 else 0)
                )
                
                # Delete the old thread record by wa_id only (wa_id is PRIMARY KEY)
                cursor.execute(
                    "DELETE FROM threads WHERE wa_id = ?", 
                    (wa_id,)
                )
                logger.info(f"[THREAD_ROTATION] Archived old conversation {old_thread_data[1]} for {wa_id}")
            
            # Insert new conversation record (without agent_context_injected flag)
            cursor.execute(
                "INSERT INTO threads (wa_id, thread_id, created_at) VALUES (?, ?, ?)",
                (wa_id, new_conversation_id, datetime.now().isoformat())
            )
            
            conn.commit()
            logger.info(f"[THREAD_ROTATION] Database updated successfully for {wa_id}")
        
        logger.info(f"[THREAD_ROTATION] Successfully rotated thread for {wa_id}: {old_conversation_id} -> {new_conversation_id}")
        return new_conversation_id
        
    except Exception as e:
        logger.error(f"[THREAD_ROTATION] Failed to rotate conversation thread: {e}")
        return None

async def get_recent_conversation_context(conversation_id: str, limit: int = 50) -> str:
    """Gets the most recent conversation context for thread rotation.
    
    Returns a summarized conversation flow.
    """
    try:
        logger.info(f"[THREAD_ROTATION] Note: Context retrieval simplified with Responses API")
        # With Responses API, conversation context is handled automatically
        # Return a simple context message
        return "Conversación previa disponible en el historial del sistema."
        
    except Exception as e:
        logger.warning(f"[THREAD_ROTATION] Error getting context from {conversation_id}: {e}")
        return ""

async def get_openai_response(
    message: str,
    thread_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    subscriber_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> Tuple[str, str]:
    """Send message to OpenAI using new Responses API and return response, handling function calls.

    Migrated from Assistant API to Responses API. Now supports multi-channel messaging by 
    auto-injecting `subscriber_id` and `channel` into tool calls that accept them (ManyChat), 
    while preserving WhatsApp compatibility via `phone_number`/`wa_id`.
    
    Uses conversation IDs instead of thread IDs for conversation context management.
    """
    from .thread_store import get_conversation_id, save_conversation_id, get_last_response_id, save_response_id
    
    el_salvador_tz = timezone("America/El_Salvador")
    now_in_sv = datetime.now(el_salvador_tz)
    datetime_str = now_in_sv.strftime("%A, %Y-%m-%d, %H:%M")
    
    # Load system instructions from file
    system_instructions = load_system_instructions()
    
    # Build the contextualized user message (same as before)
    contextualized_message = (
        f"The current date, day, and time in El Salvador (GMT-6) is {datetime_str}. "
        f"CRITICAL Booking Workflow: "
        f"1. **Collect Information**: Gather all necessary booking details from the customer (dates, number of guests, package, etc.). "
        f"2. **Payment**: "
        f"- For CompraClick: Create a payment link using `create_compraclick_link`. "
        f"- For Bank Transfer: Provide the bank details and instruct the user to send a proof of payment. "
        f"3. **Payment Verification (Sync -> Validate -> Book)**: "
        f"- When a customer sends proof of payment, first use `analyze_payment_proof` to extract details. "
        f"**CRITICAL CompraClick Proof Distinction**: "
        f"- INVALID CompraClick proof: Screenshot showing only 'Número de operación' or 'Recibo' (this is just the confirmation screen after payment, NOT a valid proof). "
        f"- VALID CompraClick proof: CompraClick PDF receipt containing the word 'Autorización' with a 6-character alphanumeric code. "
        f"- If customer sends INVALID proof (only Número de operación/Recibo), you MUST: (1) Remember that number, (2) Explain this is not the correct proof, (3) Instruct them to check their email inbox for the CompraClick PDF receipt, (4) Ask them to open the PDF and send a screenshot showing the date, credit card number, AND the 'Autorización' code, (5) If they can't find the email, suggest checking Junk/Spam folder. "
        f"- If customer repeats the same Número de operación/Recibo number, insist that this is NOT the authorization code needed and that they must find the 'Autorización' code from the CompraClick email PDF. "
        f"- If the analysis is inconclusive or key details like an authorization number are missing, you MUST ask the user to provide this information directly. DO NOT get distracted by other topics or documents; resolving the payment is the top priority. "
        f"**CRITICAL Payment Method Consistency**: "
        f"- Once a customer selects a payment method (CompraClick or Bank Transfer), you MUST stay focused on that method throughout the conversation. "
        f"- DO NOT assume the customer changed payment methods unless they EXPLICITLY state so (e.g., 'decidí hacer transferencia bancaria' or 'mejor hice un depósito'). "
        f"- If customer sends a bank transfer proof when CompraClick was selected, first confirm: '¿Decidiste cambiar el método de pago a transferencia bancaria en lugar de CompraClick?' "
        f"**CompraClick Fallback Validation Process**: "
        f"- Track failed CompraClick authorization code attempts (wrong codes or customer can't find the code). "
        f"- After 3 failed attempts OR if customer explicitly states they cannot find the authorization code, activate fallback validation: "
        f"  1. Inform customer: 'Entiendo que no puede encontrar el código de autorización. Puedo verificar su pago con información alternativa.' "
        f"  2. Request: (a) Last 4 digits of the credit card used, (b) Exact amount charged, (c) Date of payment "
        f"  3. Call `validate_compraclick_payment_fallback` with the provided information "
        f"  4. If fallback validation succeeds, proceed with booking as normal "
        f"  5. If fallback validation fails, provide specific guidance based on the error (wrong card digits, amount mismatch, etc.) "
        f"**IMMEDIATE SYNC TRIGGER**: If the user mentions they have made a bank transfer (e.g., 'ya transferí', 'pago enviado'), you MUST immediately call `sync_bank_transfers()` BEFORE asking for proof or any other action. This ensures the system has the latest data. "
        f"- **CRITICAL SYNC STEP**: Before validating, you MUST sync the latest payments. "
        f"- For CompraClick, call `sync_compraclick_payments()`. "
        f"- For Bank Transfers, call `sync_bank_transfers()`. "
        f"- **VALIDATION STEP**: "
        f"- **CompraClick**: After syncing, use `validate_compraclick_payment` with the correct `authorization_number` and `booking_total`. "
        f"  * If validation fails with 'Authorization code not found' after 3 attempts or customer can't find code, use `validate_compraclick_payment_fallback` instead. "
        f"- **Bank Transfer**: CRITICAL - Before calling `validate_bank_transfer`, verify that ALL required data was extracted from the payment proof: "
        f"  * If the `timestamp` field is missing or empty from `analyze_payment_proof` result, you MUST ask the customer to provide the exact date of the bank transfer (e.g., 'Por favor, indícame la fecha exacta de la transferencia bancaria (formato DD/MM/AAAA)'). "
        f"  * If the `amount` field is missing, ask the customer to confirm the transfer amount. "
        f"  * DO NOT attempt validation with incomplete data as it will cause system errors. "
        f"  * Only call `validate_bank_transfer` once you have complete data: `slip_date`, `slip_amount`, and `booking_amount`. "
        f"- **AUTOMATIC BOOKING TRIGGER**: Once payment validation succeeds (either CompraClick or Bank Transfer), you MUST IMMEDIATELY proceed to steps 5 and 6 below WITHOUT waiting for additional customer input or confirmation. The customer has already provided payment - proceed directly to complete their booking. "
        f"4. **Handling Validation Failures (Retry Logic)**: "
        f"- If validation fails (payment not found), you MUST call the appropriate retry tool: "
        f"- `trigger_compraclick_retry_for_missing_payment` for CompraClick. "
        f"- `start_bank_transfer_retry_process` for Bank Transfers. "
        f"- Inform the user that you are verifying the payment and will notify them shortly. DO NOT ask them to send the proof again unless the retry process also fails. "
        f"5. **MANDATORY Office Status Check**: "
        f"- **BEFORE ANY BOOKING ATTEMPT**: You MUST call `check_office_status()` to determine if automation is allowed. This is MANDATORY - no exceptions. "
        f"- **If office_status = 'closed' OR can_automate = true**: Proceed with automated booking using `make_booking`. "
        f"- **If office_status = 'open' AND can_automate = false**: "
        f"  1. Send a complete customer message that: "
        f"     - Confirms payment validation success "
        f"     - Explains that since offices are open, they'll be transferred to a human agent to complete booking "
        f"     - Provides reassurance about the process "
        f"  2. After sending the customer message, send a separate follow-up message containing ONLY the word 'handover' (no other text) to trigger the system handover process. "
        f"  3. DO NOT attempt booking. "
        f"6. **Booking Confirmation**: "
        f"- **CRITICAL**: After confirming automation is allowed via `check_office_status`, immediately call the `make_booking` function to reserve the room. DO NOT ask for the information again; use the data you have already collected. "
        f"- After calling `make_booking`, inform the user that their booking is confirmed and they will receive an email confirmation. "
        f"When asked for location, use `send_location_pin` and include the EXACT output from the function in your response - do not create your own location text. "
        f"When asked for the menu or 'what food do you have', use `send_menu_pdf`. When asked specific questions about menu items, prices, or food options (like 'do you have fish?', 'what desserts do you have?', 'how much does the chicken cost?'), first use `read_menu_content` to get current menu information, then answer ONLY with information that is EXPLICITLY written in the PDF content. CRITICAL: DO NOT add descriptions, details, or side dishes that are not specifically mentioned in the PDF text. DO NOT make assumptions about ingredients, preparations, or accompaniments beyond what is explicitly stated. "
        f"When asked for availability for multi-night stays (2+ nights), use `check_smart_availability` to offer partial stay options if full period unavailable. For single night stays, use `check_room_availability`. "
        f"When asked for pictures of accommodations, use `send_bungalow_pictures`. When asked for pictures of public areas, facilities, or common spaces, use `send_public_areas_pictures`. "
        f"To create a payment link, use `create_compraclick_link`. "
        f"Do not answer from memory."
    )

    # Check for PENDING bookings that need processing
    if phone_number:
        try:
            pending_result = await process_pending_booking_if_needed(phone_number, message)
            if pending_result:
                logger.info(f"Processed PENDING booking for {phone_number}: {pending_result}")
                # Return the booking result directly without going through OpenAI
                if pending_result.get('success'):
                    return pending_result.get('customer_message', 'Su reserva ha sido procesada exitosamente.'), thread_id or 'no_thread'
                else:
                    return pending_result.get('customer_message', 'Ha ocurrido un error al procesar su reserva pendiente. Un agente se pondrá en contacto con usted.'), thread_id or 'no_thread'
        except Exception as e:
            logger.error(f"Error checking PENDING booking for {phone_number}: {e}")
            # Continue with normal processing if PENDING check fails

    # Get or create conversation ID using Responses API
    user_identifier = phone_number or subscriber_id or "unknown"
    conversation_id = thread_id or get_conversation_id(user_identifier)
    if thread_id:
        # ensure persistence if you want this to become canonical for the user
        save_conversation_id(user_identifier, thread_id)
    
    # Function to get conversation context for recovery
    async def get_conversation_context(conv_id):
        """Retrieve recent conversation history for context seeding"""
        try:
            # With Responses API, conversation context is handled automatically
            logger.info(f"[CONTEXT] Responses API handles conversation history automatically")
            context_items = []
        except Exception as e:
            logger.warning(f"[OpenAI] Could not retrieve context from {conv_id}: {e}")
        return []
    
    # Function to create fresh conversation with context
    async def create_fresh_conversation_with_context(old_conv_id=None):
        """Create new conversation and seed with context from old one"""
        context_messages = []
        if old_conv_id:
            logger.info(f"[OpenAI] Retrieving context from {old_conv_id} for recovery")
            context_items = await get_conversation_context(old_conv_id)
            # Convert context items to input format (skip complex items that cause API errors)
            for item in context_items[-5:]:  # Last 5 items only
                if hasattr(item, 'content') and item.content:
                    # Only add simple text messages, skip complex items (function calls, reasoning, etc.)
                    if item.type == "message" and isinstance(item.content, str):
                        role = "assistant" if getattr(item, 'role', None) == "assistant" else "user"
                        context_messages.append({
                            "type": "message",
                            "role": role,
                            "content": [{"type": "input_text", "text": item.content}]
                        })
                    elif item.type == "response" and isinstance(item.content, str):
                        context_messages.append({
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "input_text", "text": item.content}]
                        })
        
        # Create fresh conversation using Responses API
        # Create fresh conversation using Conversations API
        async with httpx.AsyncClient() as client:
            response = await client.post(
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
        
        save_conversation_id(user_identifier, conversation_id)
        logger.info(f"[OpenAI] Created fresh conversation {conversation_id} with {len(context_messages)} context messages")
        return conversation_id, context_messages
    
    try:
        # Try to reuse existing conversation first
        if not conversation_id:
            logger.info(f"[OpenAI] Creating new conversation for {user_identifier}")
            # Create initial conversation using Conversations API
            async with httpx.AsyncClient() as client:
                response = await client.post(
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
            save_conversation_id(user_identifier, conversation_id)
            logger.info(f"[OpenAI] Created conversation {conversation_id}")
        else:
            logger.info(f"[OpenAI] Reusing existing conversation {conversation_id}")

        # Create response with contextualized message using Responses API
        # Find the most recent response to continue from to avoid stale tool call states
        previous_response_id = None
        
        # For existing conversations, get the last response ID from local storage to continue properly  
        if conversation_id:
            previous_response_id = get_last_response_id(user_identifier)
            if previous_response_id:
                logger.info(f"[OpenAI] Found previous response {previous_response_id} to continue from")
            else:
                logger.info(f"[OpenAI] No previous response ID found in local storage")
        else:
            logger.info(f"[OpenAI] Fresh conversation - no previous response to continue from")
        
        try:
            # Check if agent context needs to be injected (ONE TIME ONLY per conversation)
            from agent_context_injector import (
                check_if_agent_context_injected, 
                mark_agent_context_injected, 
                get_agent_context_for_system_injection
            )
            
            needs_agent_context = not check_if_agent_context_injected(conversation_id)
            agent_context_system_msg = ""
            
            if needs_agent_context:
                agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                if agent_context_system_msg:
                    logger.info(f"[AGENT_CONTEXT] Injecting agent context for conversation {conversation_id}")
                    
                    # FIRST API CALL: Send ONLY agent context in developer input
                    # For fresh conversations, use conversation parameter for first call only
                    agent_response = await openai_client.responses.create(
                        model="gpt-5",
                        conversation=conversation_id,
                        input=[
                            {
                                "type": "message",
                                "role": "developer",
                                "content": [{"type": "input_text", "text": agent_context_system_msg}],
                            }
                        ],
                        # keep it tiny so the model doesn't produce a long reply
                        max_output_tokens=16,
                    )
                    # Save agent context response ID
                    save_response_id(user_identifier, agent_response.id)
                    
                    logger.info(f"[AGENT_CONTEXT] Agent context successfully injected for conversation {conversation_id}")
                    mark_agent_context_injected(conversation_id)
                else:
                    logger.info(f"[AGENT_CONTEXT] No agent context available for {phone_number}")
            else:
                logger.info(f"[AGENT_CONTEXT] Agent context already injected for conversation {conversation_id}")
            
            # Check for missed customer-agent messages (only if NOT first message)
            missed_messages = ""
            if not needs_agent_context:  # Only if agent context already injected (not first message)
                from agent_context_injector import (
                    check_if_5_minutes_since_last_webhook_message,
                    get_missed_customer_agent_messages_for_developer_input
                )
                
                if check_if_5_minutes_since_last_webhook_message(phone_number):
                    missed_messages = get_missed_customer_agent_messages_for_developer_input(phone_number)
                    if missed_messages:
                        logger.info(f"[MISSED_MESSAGES] Found missed customer-agent messages for {phone_number}")
                    else:
                        logger.info(f"[MISSED_MESSAGES] No missed messages found for {phone_number}")
                else:
                    logger.info(f"[MISSED_MESSAGES] Less than 5 minutes since last webhook message for {phone_number}")
            
            # Use regular developer message + missed messages if any
            enhanced_developer_message = contextualized_message + missed_messages
            
            # SECOND API CALL: Send normal message (system + developer + user)
            # Always use previous_response_id to avoid stale tool call conflicts
            if previous_response_id:
                # Continue from existing response
                response = await openai_client.responses.create(
                    model="gpt-5",
                    previous_response_id=previous_response_id,
                    input=[
                        {
                            "type": "message",
                            "role": "system",
                            "content": [{"type": "input_text", "text": system_instructions}]
                        },
                        {
                            "type": "message", 
                            "role": "developer",
                            "content": [{"type": "input_text", "text": enhanced_developer_message}]
                        },
                        {
                            "type": "message",
                            "role": "user", 
                            "content": [{"type": "input_text", "text": message}]
                        }
                    ],
                    tools=tools,
                    max_output_tokens=4000
                )
                logger.info(f"[OpenAI] Continued from response {previous_response_id}")
            else:
                # New conversation - use conversation parameter only for the very first call
                response = await openai_client.responses.create(
                    model="gpt-5",
                    conversation=conversation_id,
                    input=[
                        {
                            "type": "message",
                            "role": "system",
                            "content": [{"type": "input_text", "text": system_instructions}]
                        },
                        {
                            "type": "message", 
                            "role": "developer",
                            "content": [{"type": "input_text", "text": enhanced_developer_message}]
                        },
                        {
                            "type": "message",
                            "role": "user", 
                            "content": [{"type": "input_text", "text": message}]
                        }
                    ],
                    tools=tools,
                    max_output_tokens=4000
                )
                logger.info(f"[OpenAI] Started new conversation {conversation_id}")
            
            # Save this response ID for future continuation
            save_response_id(user_identifier, response.id)
        except Exception as e:
            # Check if this is a tool call or conversation structure related error
            error_str = str(e).lower()
            if "tool output" in error_str or "function call" in error_str or "call_id" in error_str or "reasoning" in error_str:
                logger.warning(f"[OpenAI] Tool call error detected: {e}")
                logger.info(f"[OpenAI] Creating fresh conversation and completely restarting")
                
                # Create completely fresh conversation without trying to recover context
                async with httpx.AsyncClient() as client:
                    response_data = await client.post(
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
                
                save_conversation_id(user_identifier, conversation_id)
                logger.info(f"[OpenAI] Created fresh conversation {conversation_id}")
                
                # INJECT AGENT CONTEXT first for fresh conversation
                agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                if agent_context_system_msg:
                    logger.info(f"[AGENT_CONTEXT] Injecting agent context for fresh conversation {conversation_id}")
                    agent_response = await openai_client.responses.create(
                        model="gpt-5",
                        conversation=conversation_id,
                        input=[{
                            "type": "message",
                            "role": "developer",
                            "content": [{"type": "input_text", "text": agent_context_system_msg}]
                        }],
                        max_output_tokens=16
                    )
                    logger.info(f"[AGENT_CONTEXT] Agent context injected for fresh recovery conversation {conversation_id}")
                    
                    # Save agent context response ID
                    save_response_id(user_identifier, agent_response.id)
                    
                    # RESTART THE ENTIRE FLOW with fresh conversation
                    response = await openai_client.responses.create(
                        model="gpt-5",
                        previous_response_id=agent_response.id,
                        input=[
                            {
                                "type": "message",
                                "role": "system",
                                "content": [{"type": "input_text", "text": system_instructions}]
                            },
                            {
                                "type": "message",
                                "role": "developer", 
                                "content": [{"type": "input_text", "text": contextualized_message}]
                            },
                            {
                                "type": "message",
                                "role": "user", 
                                "content": [{"type": "input_text", "text": message}]
                            }
                        ],
                        tools=tools,
                        max_output_tokens=4000
                    )
                    
                    # Save main response ID
                    save_response_id(user_identifier, response.id)
                    logger.info(f"[OpenAI] Successfully restarted with fresh conversation")
            else:
                # Re-raise if not a tool call error
                raise
        
        # Handle tool calls (chaining supported)
        max_tool_rounds = 8  # safety to avoid infinite loops
        round_count = 0
        recovery_attempts = 0  # Circuit breaker for recovery loops
        max_recovery_attempts = 2
        all_tool_outputs = []  # Keep track for potential synthesis

        while round_count < max_tool_rounds:
            round_count += 1

            tool_calls = _iter_tool_calls(response)
            if not tool_calls:
                # No more tool calls requested by model
                break

            tool_output_input = []
            
            for tc in tool_calls:
                fn_name = getattr(tc, "name", None)
                raw_args = getattr(tc, "arguments", None)
                call_id = getattr(tc, "call_id", None) or getattr(tc, "id", None)
                
                logger.info(f"[Tool] Round {round_count} - Requested: {fn_name} with call_id: {call_id} args={raw_args}")

                # Execute function and get result
                fn_args = _tool_args(raw_args)
                fn = available_functions.get(fn_name)

                try:
                    if fn is None:
                        output = {"error": f"Function {fn_name} not found"}
                    else:
                        if asyncio.iscoroutinefunction(fn):
                            # Auto-inject channel identifiers if the function accepts them
                            sig = inspect.signature(fn)
                            if 'phone_number' in sig.parameters and phone_number:
                                fn_args.setdefault('phone_number', phone_number)
                            if 'wa_id' in sig.parameters and phone_number:
                                fn_args.setdefault('wa_id', phone_number)
                            if 'subscriber_id' in sig.parameters and subscriber_id:
                                fn_args.setdefault('subscriber_id', subscriber_id)
                            if 'channel' in sig.parameters and channel:
                                fn_args.setdefault('channel', channel)
                            result = await fn(**fn_args)
                        else:
                            result = fn(**fn_args)
                        output = _coerce_output_str(result)
                except Exception as e:
                    logger.exception(f"Error executing tool {fn_name}")
                    output = _coerce_output_str({"error": f"Error executing {fn_name}: {str(e)}"})

                tool_output_input.append({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output
                })
                all_tool_outputs.append((fn_name, output))  # Track for logging

                
            # Submit tool outputs - model may request MORE tools or provide final answer
            try:
                # Capture the ID of the response that requested the tool call
                previous_resp_id = response.id
                
                response = await openai_client.responses.create(
                    model="gpt-5",
                    # Use previous_response_id to continue the *same* response turn
                    previous_response_id=previous_resp_id,
                    input=tool_output_input,
                    tools=tools,  # Keep tools available for chaining
                    max_output_tokens=4000
                    # DO NOT include the 'conversation' parameter here
                )
                # Save tool output response ID
                save_response_id(user_identifier, response.id)
            except Exception as e:
                # Handle stale tool call IDs in existing conversations
                error_str = str(e).lower()
                if "tool output" in error_str or "function call" in error_str:
                    recovery_attempts += 1
                    if recovery_attempts > max_recovery_attempts:
                        logger.error(f"[Tool] Too many recovery attempts ({recovery_attempts}), giving up on tool execution")
                        # Try to get a natural response without tools
                        try:
                            # Fallback without tools - use conversation for simplicity
                            response = await openai_client.responses.create(
                                model="gpt-5",
                                conversation=conversation_id,
                                input=[
                                    {
                                        "type": "message",
                                        "role": "developer",
                                        "content": [{"type": "input_text", 
                                                   "text": f"Please provide a response to the user's question: '{message}' without using any tools. Respond in Spanish."}]
                                    }
                                ],
                                max_output_tokens=4000
                            )
                            # Save fallback response ID
                            save_response_id(user_identifier, response.id)
                            break  # Exit tool loop
                        except:
                            # If even this fails, return error message
                            return "Lo siento, hay un problema técnico. Por favor intenta nuevamente.", conversation_id
                    
                    logger.warning(f"[Tool] Tool call error during submission (attempt {recovery_attempts}): {e}")
                    logger.info(f"[Tool] Creating fresh conversation and restarting entire flow")
                    
                    # Create completely fresh conversation
                    async with httpx.AsyncClient() as client:
                        response_data = await client.post(
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
                    
                    save_conversation_id(user_identifier, conversation_id)
                    logger.info(f"[Tool] Created fresh conversation {conversation_id} for recovery")
                    
                    # INJECT AGENT CONTEXT first for fresh conversation
                    agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                    if agent_context_system_msg:
                        logger.info(f"[AGENT_CONTEXT] Injecting agent context for fresh recovery conversation {conversation_id}")
                        # Fresh conversation recovery - use conversation parameter for first call
                        agent_response = await openai_client.responses.create(
                            model="gpt-5",
                            conversation=conversation_id,
                            input=[{
                                "type": "message",
                                "role": "developer",
                                "content": [{"type": "input_text", "text": agent_context_system_msg}]
                            }],
                            max_output_tokens=16
                        )
                        save_response_id(user_identifier, agent_response.id)
                        logger.info(f"[AGENT_CONTEXT] Agent context injected for fresh recovery conversation {conversation_id}")
                    
                    # RESTART THE ENTIRE FLOW with fresh conversation
                    # Fresh conversation recovery - use conversation parameter for first call
                    response = await openai_client.responses.create(
                        model="gpt-5",
                        conversation=conversation_id,
                        input=[
                            {
                                "type": "message",
                                "role": "system",
                                "content": [{"type": "input_text", "text": system_instructions}]
                            },
                            {
                                "type": "message",
                                "role": "developer", 
                                "content": [{"type": "input_text", "text": contextualized_message}]
                            },
                            {
                                "type": "message",
                                "role": "user", 
                                "content": [{"type": "input_text", "text": message}]
                            }
                        ],
                        tools=tools,
                        max_output_tokens=4000
                    )
                    # Save recovery response ID
                    save_response_id(user_identifier, response.id)
                    logger.info(f"[Tool] Successfully restarted with fresh conversation - resetting tool rounds")
                    # Reset tool round tracking since we're starting fresh
                    round_count = 0
                    all_tool_outputs = []
                    continue  # Go back to the tool detection loop
                else:
                    # Re-raise if not a tool call error
                    raise
            
            logger.info(f"[Tool] Round {round_count} complete. Checking for more tool calls...")

        # After all tool rounds complete, check response quality

        # After all tool rounds complete, check response quality
        final_response = response.output_text or _extract_text_from_output(getattr(response, "output", [])) or ""
        
        # ONLY do synthesis call if response seems incomplete or error-like
        # Skip synthesis for friendly_goodbye responses
        if (not final_response or 
            len(final_response) < 30 or 
            final_response.lower().startswith("i've") or
            final_response.lower().startswith("the function") or
            "successfully" in final_response.lower() and len(final_response) < 100) and \
           "friendly_goodbye" not in final_response.lower():
            
            logger.info(f"[OpenAI] Response seems incomplete ({len(final_response)} chars). Making synthesis call.")
            
            # Include context reminder without breaking tool chain flow
            # Use previous_response_id to continue the same turn and avoid reasoning item conflicts
            previous_resp_id = response.id
            
            response = await openai_client.responses.create(
                model="gpt-5",
                previous_response_id=previous_resp_id,
                input=[
                    {
                        "type": "message",
                        "role": "developer",
                        "content": [{"type": "input_text", 
                                   "text": f"Based on the tool results obtained, please provide a complete and helpful response to the user's original question: '{message}'. Respond in Spanish or English as per system instructions depending on the context."}]
                    }
                ],
                max_output_tokens=4000
            )
            # Save synthesis response ID
            save_response_id(user_identifier, response.id)
            
            final_response = response.output_text or _extract_text_from_output(getattr(response, "output", [])) or "No response generated."
        
        # Apply JSON guard (preserve from memory) - only if still seems problematic
        if final_response and len(final_response) < 200:
            try:
                parsed = json.loads(final_response)
                if any(k in str(parsed).lower() for k in ['function', 'arguments', 'name']):
                    logger.info("[JSON_GUARD] Detected tool-like JSON, repairing")
                    # JSON repair - use conversation for simplicity since this is error recovery
                    repair_response = await openai_client.responses.create(
                        model="gpt-5",
                        conversation=conversation_id,
                        input=[
                            {
                                "type": "message", 
                                "role": "system",
                                "content": [{"type": "input_text", "text": "Convierte esto en un mensaje natural en español."}]
                            },
                            {
                                "type": "message", 
                                "role": "user",
                                "content": [{"type": "input_text", "text": final_response}]
                            }
                        ]
                    )
                    # Save repair response ID
                    save_response_id(user_identifier, repair_response.id)
                    final_response = repair_response.output_text or final_response
            except:
                pass  # Not JSON, use as-is
        
        logger.info(f"Final response from OpenAI: {final_response[:100]}...")
        
        # HUMANIZATION STEP: Add warmth, empathy, and personality to the response
        try:
            humanized_response = await humanize_response(final_response)
            logger.info(f"Response humanized successfully: {humanized_response[:100]}...")
            return humanized_response, conversation_id
        except Exception as humanization_error:
            logger.error(f"Humanization failed, returning original response: {humanization_error}")
            return final_response, conversation_id
        
    except Exception as e:
        logger.error(f"[OpenAI] Error: {str(e)}")
        
        # Handle specific error types
        if "rate_limit" in str(e).lower():
            await asyncio.sleep(5)
            # Retry once for rate limit
            try:
                # Rate limit retry - use conversation for simplicity
                response = await openai_client.responses.create(
                    model="gpt-5",
                    conversation=conversation_id,
                    input=[
                        {
                            "type": "message",
                            "role": "system",
                            "content": [{"type": "input_text", "text": system_instructions}]
                        },
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": message}]
                        }
                    ],
                    tools=tools
                )
                # Save rate limit retry response ID
                save_response_id(user_identifier, response.id)
                final_response = response.output_text or "No response after retry."
                
                # HUMANIZATION STEP for retry response
                try:
                    humanized_response = await humanize_response(final_response)
                    return humanized_response, conversation_id
                except Exception as humanization_error:
                    logger.error(f"Retry humanization failed: {humanization_error}")
                    return final_response, conversation_id
            except Exception as retry_error:
                logger.error(f"Rate limit retry failed: {retry_error}")
                # Let this bubble up to main.py retry logic instead of returning error message
                raise retry_error
        elif "context_length_exceeded" in str(e).lower() or "context window" in str(e).lower():
            # Handle context window overflow by rotating to new conversation
            logger.warning(f"[THREAD_ROTATION] Context window exceeded, rotating to new conversation thread")
            try:
                new_conversation_id = await rotate_conversation_thread(conversation_id, phone_number, contextualized_message, system_instructions)
                if new_conversation_id:
                    # Force agent context injection for new conversation
                    logger.info(f"[THREAD_ROTATION] Injecting agent context for new conversation {new_conversation_id}")
                    
                    from agent_context_injector import (
                        get_agent_context_for_system_injection,
                        mark_agent_context_injected
                    )
                    
                    agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                    if agent_context_system_msg:
                        # FIRST API CALL: Send agent context only  
                        # Fresh conversation - use conversation parameter for first call
                        agent_response = await openai_client.responses.create(
                            model="gpt-5",
                            conversation=new_conversation_id,
                            input=[
                                {
                                    "type": "message",
                                    "role": "developer",
                                    "content": [{"type": "input_text", "text": agent_context_system_msg}],
                                }
                            ],
                            # keep it tiny so the model doesn't produce a long reply
                            max_output_tokens=16,
                        )
                        # Save agent context response ID for context window rotation
                        save_response_id(user_identifier, agent_response.id)
                        
                        # Mark as injected
                        mark_agent_context_injected(new_conversation_id)
                        logger.info(f"[THREAD_ROTATION] Agent context injected successfully")
                    
                    # Recursively call get_openai_response with new conversation ID
                    # This ensures proper tool execution flow after thread rotation
                    logger.info(f"[THREAD_ROTATION] Retrying with new conversation {new_conversation_id} through complete flow")
                    return await get_openai_response(
                        message,
                        thread_id=new_conversation_id,
                        phone_number=phone_number,
                        subscriber_id=subscriber_id,
                        channel=channel,
                    )
                else:
                    raise RuntimeError(f"Thread rotation failed: unable to create new conversation for {phone_number}")
            except Exception as rotation_error:
                # Filter out system_instructions content from error message
                error_msg = str(rotation_error)
                if "system_language" in error_msg or len(error_msg) > 1000:
                    # Extract just the error type and first line if it contains system instructions
                    error_lines = error_msg.split('\n')
                    filtered_msg = error_lines[0] if error_lines else "Context window error"
                    logger.error(f"[THREAD_ROTATION] Failed to rotate conversation: {filtered_msg}")
                else:
                    logger.error(f"[THREAD_ROTATION] Failed to rotate conversation: {error_msg}")
                raise rotation_error
        else:
            # Let all other errors bubble up to main.py retry logic
            raise e
