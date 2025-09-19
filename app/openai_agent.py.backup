import httpx
import asyncio
import re
import inspect
import json
import logging
import os
from typing import Optional, Tuple
from . import config, database_client
from . import compraclick_tool
from . import payment_proof_analyzer
from app import bank_transfer_tool
from app import bank_transfer_retry
from app import booking_tool, email_service
from app.booking_tool import process_pending_booking_if_needed
from app import smart_availability
from app import office_status_tool
from app import config
from datetime import datetime
from pytz import timezone
from app import wati_client
from app.clients import manychat_client

logger = logging.getLogger(__name__)

OPENAI_API_BASE = "https://api.openai.com/v1"

tools = [
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
                "required": ["date_str"],
            },
        },
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
    }
]

async def add_message_to_thread(thread_id: str, content: str):
    """Adds a message to an existing OpenAI thread, used for history import."""
    try:
        logger.info(f"[HISTORY_IMPORT] Adding historical context to thread {thread_id}...")
        headers = {
            "Authorization": f"Bearer {config.OPENAI_API_KEY}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "assistants=v2"
        }
        payload = {
            "role": "user",
            "content": content
        }
        url = f"{OPENAI_API_BASE}/threads/{thread_id}/messages"
        async with httpx.AsyncClient(timeout=60.0) as client: # Use a long timeout
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status() # Will raise an exception for 4xx/5xx responses
        logger.info(f"[HISTORY_IMPORT] Successfully added historical context to thread {thread_id}.")
        return True
    except httpx.HTTPStatusError as e:
        logger.error(f"[HISTORY_IMPORT] HTTP error adding message to thread {thread_id}: {e.response.status_code} - {e.response.text}")
        return False
    except Exception as e:
        logger.error(f"[HISTORY_IMPORT] An unexpected error occurred while adding message to thread {thread_id}: {e}")
        return False

async def get_thread_messages(thread_id: str, limit: int = 20) -> list:
    """Retrieves the most recent messages from a given OpenAI thread."""
    if not client:
        logger.error("[CONTEXT] OpenAI client not initialized.")
        return []
    try:
        logger.info(f"[CONTEXT] Fetching messages for thread_id: {thread_id}")
        messages = await client.beta.threads.messages.list(
            thread_id=thread_id,
            limit=limit,
            order="desc"  # Fetch most recent first
        )
        logger.info(f"[CONTEXT] Retrieved {len(messages.data)} messages from OpenAI thread.")
        return messages.data
    except Exception as e:
        logger.error(f"[CONTEXT] Failed to retrieve messages for thread {thread_id}: {e}")
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
}

async def get_openai_response(
    message: str,
    thread_id: Optional[str] = None,
    phone_number: Optional[str] = None,
    subscriber_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> Tuple[str, str]:
    """Send message to OpenAI Assistant v2 and return response, handling function calls.

    Now supports multi-channel messaging by auto-injecting `subscriber_id` and `channel`
    into tool calls that accept them (ManyChat), while preserving WhatsApp compatibility
    via `phone_number`/`wa_id`.
    """
    el_salvador_tz = timezone("America/El_Salvador")
        
    now_in_sv = datetime.now(el_salvador_tz)
    datetime_str = now_in_sv.strftime("%A, %Y-%m-%d, %H:%M") # e.g., 'Sunday, 2025-07-06, 20:44'
    contextualized_message = (
        f"The current date, day, and time in El Salvador (GMT-6) is {datetime_str}. "
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
        f"  1. Inform customer that offices are open and they need to speak with a human agent. "
        f"  2. Send a message containing 'handover' to trigger the WATI handover process (this will mark conversation as PENDING and assign to operator). "
        f"  3. DO NOT attempt booking. "
        f"6. **Booking Confirmation**: "
        f"- **CRITICAL**: After confirming automation is allowed via `check_office_status`, immediately call the `make_booking` function to reserve the room. DO NOT ask for the information again; use the data you have already collected. "
        f"- After calling `make_booking`, inform the user that their booking is confirmed and they will receive an email confirmation. "
        f"When asked for location, use `send_location_pin`. "
        f"When asked for the menu, use `send_menu_pdf`. "
        f"When asked for availability for multi-night stays (2+ nights), use `check_smart_availability` to offer partial stay options if full period unavailable. For single night stays, use `check_room_availability`. "
        f"When asked for pictures of accommodations, use `send_bungalow_pictures`. When asked for pictures of public areas, facilities, or common spaces, use `send_public_areas_pictures`. "
        f"To create a payment link, use `create_compraclick_link`. "
        f"Do not answer from memory. User query: {message}"
    )
    logger.info(f"Contextualized message for assistant: {contextualized_message}")

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

    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
        "OpenAI-Beta": "assistants=v2"
    }
    async with httpx.AsyncClient(timeout=60) as client:
        # 1. Create a thread if one doesn't exist
        if not thread_id:
            try:
                thread_resp = await client.post(f"{OPENAI_API_BASE}/threads", headers=headers)
                thread_resp.raise_for_status()
                thread_id = thread_resp.json()["id"]
                logger.info(f"Created new thread: {thread_id}")
            except httpx.HTTPStatusError as e:
                logger.exception("Failed to create a new thread.")
                return f"(Error: Could not create a new thread: {e})", None

        # 2. Cancel any active runs BEFORE adding message to prevent conflicts
        try:
            existing_runs_resp = await client.get(f"{OPENAI_API_BASE}/threads/{thread_id}/runs", headers=headers)
            existing_runs_resp.raise_for_status()
            cancelled_runs = []
            for run in existing_runs_resp.json().get("data", []):
                if run['status'] in ['queued', 'in_progress', 'requires_action']:
                    logger.warning(f"[PRE-MSG] Cancelling active run {run['id']} with status {run['status']} before adding message.")
                    cancel_url = f"{OPENAI_API_BASE}/threads/{thread_id}/runs/{run['id']}/cancel"
                    await client.post(cancel_url, headers=headers)
                    cancelled_runs.append(run['id'])
            if cancelled_runs:
                logger.info(f"[PRE-MSG] Cancelled {len(cancelled_runs)} active runs before message addition.")
        except httpx.HTTPStatusError as e:
            logger.warning(f"[PRE-MSG] Failed to list or cancel existing runs for thread {thread_id}: {e}")

        # 3. Add user message to the thread
        try:
            msg_payload = {"role": "user", "content": contextualized_message}
            msg_resp = await client.post(f"{OPENAI_API_BASE}/threads/{thread_id}/messages", json=msg_payload, headers=headers)
            msg_resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.exception(f"Failed to add message to thread {thread_id}. Response: {e.response.text}")
            return f"(Error: Could not add message to thread: {e})", thread_id

        # 4. Cancel any remaining active runs AFTER adding message for extra safety
        try:
            existing_runs_resp = await client.get(f"{OPENAI_API_BASE}/threads/{thread_id}/runs", headers=headers)
            existing_runs_resp.raise_for_status()
            cancelled_runs = []
            for run in existing_runs_resp.json().get("data", []):
                if run['status'] in ['queued', 'in_progress', 'requires_action']:
                    logger.warning(f"[POST-MSG] Cancelling active run {run['id']} with status {run['status']} after adding message.")
                    cancel_url = f"{OPENAI_API_BASE}/threads/{thread_id}/runs/{run['id']}/cancel"
                    await client.post(cancel_url, headers=headers)
                    cancelled_runs.append(run['id'])
            if cancelled_runs:
                logger.info(f"[POST-MSG] Cancelled {len(cancelled_runs)} additional active runs after message addition.")
        except httpx.HTTPStatusError as e:
            logger.warning(f"[POST-MSG] Failed to list or cancel existing runs for thread {thread_id}: {e}")

        # 3. Create a run (with infinite retry)
        while True:
            try:
                run_payload = {
                    "assistant_id": config.OPENAI_AGENT_ID,
                    "tools": tools
                }
                run_resp = await client.post(f"{OPENAI_API_BASE}/threads/{thread_id}/runs", json=run_payload, headers=headers)
                run_resp.raise_for_status()
                run_id = run_resp.json()["id"]
                logger.info(f"Successfully created run {run_id} for thread {thread_id}.")
                break  # Exit loop on success
            except httpx.HTTPStatusError as e:
                logger.exception(f"Failed to create a run for thread {thread_id}. Response: {e.response.text}. Retrying in 10 seconds...")
                await asyncio.sleep(10)  # Wait before retrying

        # 4. Poll for the run to complete with timeout checks to prevent infinite retries
        run_start_time = asyncio.get_event_loop().time()
        run_timeout_seconds = 180  # 3 minutes timeout for run polling
        
        while True:
            # Check timeout before each retry cycle
            current_time = asyncio.get_event_loop().time()
            elapsed_time = current_time - run_start_time
            
            if elapsed_time >= run_timeout_seconds:
                logger.warning(f"OpenAI run polling timeout after {elapsed_time:.1f} seconds. Attempting recovery in-place...")
                
                # 1) Check current run status to decide if cancel is applicable
                current_run_status = None
                try:
                    status_resp = await client.get(f"{OPENAI_API_BASE}/threads/{thread_id}/runs/{run_id}", headers=headers)
                    status_resp.raise_for_status()
                    current_run_status = status_resp.json().get("status")
                    logger.info(f"[RECOVERY] Current run status before cancel attempt: {current_run_status}")
                except Exception as e:
                    logger.warning(f"[RECOVERY] Unable to fetch run status before cancel: {e}")

                # 2) Only attempt cancel if the run is cancellable
                if current_run_status in ("queued", "in_progress"):
                    try:
                        logger.info(f"Cancelling OpenAI run {run_id} in thread {thread_id} (status={current_run_status})")
                        cancel_url = f"{OPENAI_API_BASE}/threads/{thread_id}/runs/{run_id}/cancel"
                        cancel_resp = await client.post(cancel_url, json={}, headers=headers)
                        cancel_resp.raise_for_status()
                        logger.info(f"Successfully cancelled run: {run_id}")
                        # small delay to allow backend to settle
                        await asyncio.sleep(2)
                    except Exception as cancel_error:
                        logger.warning(f"[RECOVERY] Cancel attempt failed (proceeding to new run anyway): {cancel_error}")

                # 3) Always attempt to create a new run in the SAME thread to preserve context
                try:
                    logger.info(f"Creating new run in existing thread {thread_id} with preserved context...")
                    new_run_payload = {
                        "assistant_id": config.OPENAI_AGENT_ID,
                        "tools": tools
                    }
                    new_run_resp = await client.post(f"{OPENAI_API_BASE}/threads/{thread_id}/runs", json=new_run_payload, headers=headers)
                    new_run_resp.raise_for_status()
                    new_run_id = new_run_resp.json()["id"]
                    logger.info(f"Created new run {new_run_id} in existing thread {thread_id} with full context preserved")
                    
                    # Continue with new run (thread context preserved!)
                    run_id = new_run_id
                    run_start_time = asyncio.get_event_loop().time()  # Reset timeout for new run
                    continue  # Continue with the polling loop for the new run
                except Exception as recovery_error:
                    logger.error(f"[RECOVERY] Failed to create new run after timeout: {recovery_error}")
                    # Backoff briefly before next loop iteration
                    await asyncio.sleep(5)
                    continue
            
            # Retry loop for OpenAI API calls with timeout awareness
            api_retry_count = 0
            max_api_retries = 30  # Max 30 retries (5 minutes with 10-second delays)
            
            while api_retry_count < max_api_retries:
                try:
                    run_status_resp = await client.get(f"{OPENAI_API_BASE}/threads/{thread_id}/runs/{run_id}", headers=headers)
                    run_status_resp.raise_for_status()
                    run_data = run_status_resp.json()
                    status = run_data["status"]
                    break  # Success, exit retry loop
                except Exception as e:
                    api_retry_count += 1
                    logger.error(f"OpenAI API error occurred, retrying in 10 seconds ({api_retry_count}/{max_api_retries}): {e}")
                    if api_retry_count >= max_api_retries:
                        # If we've exceeded API retries, let the timeout mechanism handle it
                        logger.warning(f"Exceeded max API retries ({max_api_retries}), allowing timeout mechanism to trigger...")
                        break
                    await asyncio.sleep(10)

            if status == "completed":
                break

            if status == "requires_action":
                tool_calls = run_data.get('required_action', {}).get('submit_tool_outputs', {}).get('tool_calls', [])
                tool_outputs = []

                for tool_call in tool_calls:
                    output = None
                    result = None
                    try:
                        function_name = tool_call['function']['name']
                        function_to_call = available_functions.get(function_name)
                        function_args = json.loads(tool_call['function']['arguments'])

                        if function_to_call:
                            logger.info(f"Calling function: {function_name} with args: {function_args}")
                            if asyncio.iscoroutinefunction(function_to_call):
                                try:
                                    # Inspect the function signature to see if it needs identifiers
                                    sig = inspect.signature(function_to_call)
                                    # Determine if we can route via ManyChat (requires both in signature and values provided)
                                    manychat_ready = (
                                        ('subscriber_id' in sig.parameters) and ('channel' in sig.parameters)
                                        and bool(subscriber_id) and bool(channel)
                                    )

                                    if 'phone_number' in sig.parameters:
                                        if phone_number:
                                            function_args['phone_number'] = phone_number
                                        else:
                                            # Do not raise; allow function to handle missing identifiers gracefully
                                            logger.warning(f"[IDENTIFIERS] phone_number not provided for {function_name}. Proceeding without it.")
                                    
                                    # CRITICAL FIX: Also auto-inject wa_id for functions that need it
                                    if 'wa_id' in sig.parameters:
                                        if phone_number:
                                            function_args['wa_id'] = phone_number
                                            logger.info(f"[WA_ID_INJECTION] Auto-injected wa_id '{phone_number}' for function {function_name}")
                                        else:
                                            logger.warning(f"[IDENTIFIERS] wa_id not provided for {function_name}. Proceeding without it.")

                                    # NEW: Auto-inject ManyChat identifiers when accepted by the function
                                    if 'subscriber_id' in sig.parameters and subscriber_id:
                                        function_args['subscriber_id'] = subscriber_id
                                        logger.info(
                                            f"[MANYCHAT_INJECTION] Auto-injected subscriber_id for function {function_name}"
                                        )
                                    if 'channel' in sig.parameters and channel:
                                        function_args['channel'] = channel
                                        logger.info(
                                            f"[MANYCHAT_INJECTION] Auto-injected channel={channel} for function {function_name}"
                                        )

                                    result = await function_to_call(**function_args)
                                except Exception as e:
                                    logger.exception(f"An error occurred during function call: {function_name}")
                                    output = json.dumps({"error": f"An error occurred while executing tool {function_name}: {str(e)}"})
                            else:
                                result = function_to_call(**function_args)
                            if output is None:
                                output = json.dumps(result)
                        else:
                            output = json.dumps({"error": f"Function {function_name} not found."})
                    except Exception as e:
                        logger.exception(f"An error occurred during function call: {function_name}")
                        output = json.dumps({"error": f"An error occurred while executing tool {function_name}: {str(e)}"})
                    
                    tool_outputs.append({
                        "tool_call_id": tool_call['id'],
                        "output": output,
                    })

                if tool_outputs:
                    # Infinite retry for tool output submission
                    while True:
                        try:
                            submit_url = f"{OPENAI_API_BASE}/threads/{thread_id}/runs/{run_id}/submit_tool_outputs"
                            submit_payload = {"tool_outputs": tool_outputs}
                            await client.post(submit_url, json=submit_payload, headers=headers)
                            break  # Success, exit retry loop
                        except Exception as e:
                            logger.error(f"Error submitting tool outputs, retrying in 10 seconds: {e}")
                            await asyncio.sleep(10)

            elif status in ("failed", "cancelled", "expired"):
                error_info = run_data.get('last_error')
                logger.error(f"Run failed with status: {status}. Details: {error_info}")
                
                # Check for existing active runs before creating new one to prevent race condition
                try:
                    logger.info(f"Checking for existing runs in thread {thread_id} before creating new run...")
                    runs_resp = await client.get(f"{OPENAI_API_BASE}/threads/{thread_id}/runs", headers=headers)
                    runs_resp.raise_for_status()
                    runs_data = runs_resp.json()
                    
                    # Check if there are any active/pending runs
                    active_runs = [r for r in runs_data.get('data', []) if r.get('status') in ('queued', 'in_progress', 'requires_action')]
                    if active_runs:
                        logger.warning(f"Found {len(active_runs)} active runs in thread {thread_id}, cancelling them first...")
                        for active_run in active_runs:
                            try:
                                cancel_resp = await client.post(f"{OPENAI_API_BASE}/threads/{thread_id}/runs/{active_run['id']}/cancel", headers=headers)
                                logger.info(f"Cancelled run {active_run['id']}")
                            except Exception as cancel_e:
                                logger.warning(f"Failed to cancel run {active_run['id']}: {cancel_e}")
                        
                        # Wait a moment for cancellations to process
                        await asyncio.sleep(2)
                    
                    # Create a NEW run in the SAME thread to preserve conversation context
                    logger.info(f"Recreating run in existing thread {thread_id} after status '{status}'...")
                    new_run_payload = {
                        "assistant_id": config.OPENAI_AGENT_ID,
                        "tools": tools
                    }
                    new_run_resp = await client.post(f"{OPENAI_API_BASE}/threads/{thread_id}/runs", json=new_run_payload, headers=headers)
                    new_run_resp.raise_for_status()
                    new_run_id = new_run_resp.json()["id"]
                    logger.info(f"Created new run {new_run_id} in existing thread {thread_id}")
                    run_id = new_run_id
                    run_start_time = asyncio.get_event_loop().time()  # Reset timeout for new run
                    continue
                    
                except Exception as e:
                    # Exponential backoff for retries to prevent infinite loops
                    retry_attempts = getattr(self, '_run_retry_attempts', 0)
                    max_retries = 5
                    
                    if retry_attempts >= max_retries:
                        logger.error(f"Max retry attempts ({max_retries}) reached for run recovery, giving up")
                        raise Exception(f"Failed to recover from run failure after {max_retries} attempts: {e}")
                    
                    self._run_retry_attempts = retry_attempts + 1
                    backoff_time = min(10 * (2 ** retry_attempts), 60)  # Exponential backoff, max 60s
                    
                    logger.exception(f"Failed to create new run after failure (attempt {retry_attempts + 1}/{max_retries}); retrying in {backoff_time} seconds...")
                    await asyncio.sleep(backoff_time)
                    continue
            
            await asyncio.sleep(1)

        # 5. Fetch the latest messages from the thread with 5-minute timeout
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = 300  # 5 minutes
        recovery_attempts = 0
        max_recovery_attempts = 3
        
        while True:
            current_time = asyncio.get_event_loop().time()
            elapsed_time = current_time - start_time
            
            # Check if we've exceeded the 5-minute timeout
            if elapsed_time >= timeout_seconds:
                if recovery_attempts >= max_recovery_attempts:
                    logger.error(f"[MSG_RECOVERY] Max recovery attempts ({max_recovery_attempts}) reached. Giving up after {elapsed_time:.1f} seconds.")
                    raise Exception(f"OpenAI assistant stuck for {elapsed_time:.1f} seconds. Max recovery attempts exceeded.")
                
                recovery_attempts += 1
                logger.warning(f"OpenAI assistant stuck for {elapsed_time:.1f} seconds while fetching messages. Creating a new run in the same thread to unblock... (attempt {recovery_attempts}/{max_recovery_attempts})")
                
                try:
                    # Check if there's already an active run before creating a new one
                    runs_resp = await client.get(f"{OPENAI_API_BASE}/threads/{thread_id}/runs?limit=1", headers=headers)
                    runs_resp.raise_for_status()
                    runs_data = runs_resp.json().get("data", [])
                    
                    if runs_data and runs_data[0].get("status") in ["queued", "in_progress", "requires_action"]:
                        logger.info(f"[MSG_RECOVERY] Found active run {runs_data[0]['id']} with status '{runs_data[0]['status']}'. Waiting instead of creating new run.")
                        # Reset timeout to give the existing run more time
                        start_time = asyncio.get_event_loop().time()
                        recovery_attempts = 0  # Reset since we're not actually recovering
                        await asyncio.sleep(10)
                        continue
                    
                    # Create a new run in the SAME thread to prompt assistant to produce a message
                    new_run_payload = {
                        "assistant_id": config.OPENAI_AGENT_ID,
                        "tools": tools
                    }
                    new_run_resp = await client.post(f"{OPENAI_API_BASE}/threads/{thread_id}/runs", json=new_run_payload, headers=headers)
                    new_run_resp.raise_for_status()
                    new_run_id = new_run_resp.json()["id"]
                    logger.info(f"[MSG_RECOVERY] Created new run {new_run_id} in existing thread {thread_id} after message retrieval timeout")
                    
                    # CRITICAL FIX: Reset timeout timer after successful recovery
                    run_id = new_run_id
                    run_start_time = asyncio.get_event_loop().time()
                    start_time = asyncio.get_event_loop().time()  # Reset message fetch timeout
                    recovery_attempts = 0  # Reset recovery counter after successful creation
                    continue
                    
                except Exception as recovery_error:
                    logger.error(f"[MSG_RECOVERY] Failed to create new run after message retrieval timeout (attempt {recovery_attempts}/{max_recovery_attempts}): {recovery_error}")
                    # Exponential backoff for recovery attempts
                    backoff_time = min(30, 5 * (2 ** (recovery_attempts - 1)))
                    logger.info(f"[MSG_RECOVERY] Backing off for {backoff_time} seconds before retry...")
                    await asyncio.sleep(backoff_time)
                    continue
            
            try:
                msgs_resp = await client.get(f"{OPENAI_API_BASE}/threads/{thread_id}/messages?limit=1", headers=headers)
                msgs_resp.raise_for_status()
                messages = msgs_resp.json().get("data", [])
                
                if messages and messages[0].get("role") == "assistant":
                    content_blocks = messages[0].get("content", [])
                    response_texts = [
                        block.get("text", {}).get("value")
                        for block in content_blocks
                        if block.get("type") == "text"
                    ]
                    final_response = "\n".join(filter(None, response_texts))
                    return final_response, thread_id
                
                # If no assistant message found, wait and retry
                logger.warning(f"No assistant message found, retrying in 10 seconds... (elapsed: {elapsed_time:.1f}s)")
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error retrieving final assistant message, retrying in 10 seconds: {e}")
                await asyncio.sleep(10)
