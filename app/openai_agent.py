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
from app import menu_prices_reader
from app import operations_tool
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

def build_classification_system_prompt() -> str:
    """Build minimal system prompt with base modules for classification"""
    
    with open('app/resources/system_instructions_new.txt', 'r', encoding='utf-8') as f:
        modular_data = json.loads(f.read())
    
    base_modules = {
        "MODULE_SYSTEM": modular_data.get("MODULE_SYSTEM", {}),
        "DECISION_TREE": modular_data.get("DECISION_TREE", {}),
        "MODULE_DEPENDENCIES": modular_data.get("MODULE_DEPENDENCIES", {}),
        "CORE_CONFIG": modular_data.get("CORE_CONFIG", {})
    }
    
    classification_prompt = f"""
🚨🚨🚨 ABSOLUTE BLOCKING RULE - READ THIS FIRST 🚨🚨🚨

BEFORE YOU DO ANYTHING ELSE, YOU MUST:
1. Look at the user query
2. Determine which modules it needs:
   - MODULE_1_CRITICAL_WORKFLOWS (blocking protocols)
   - MODULE_2A_PACKAGE_CONTENT (what's included), MODULE_2B_PRICE_INQUIRY (quotes/payment), MODULE_2C_AVAILABILITY (inventory), MODULE_2D_SPECIAL_SCENARIOS (membership/all-inclusive/events)
   - MODULE_3_SERVICE_FLOWS (existing reservations)
   - MODULE_4_INFORMATION (facilities/policies)
3. IMMEDIATELY call load_additional_modules() tool with the required modules
4. WAIT for the tool response with the module content
5. ONLY THEN respond to the user using the loaded module information

YOU ARE ABSOLUTELY FORBIDDEN TO:
- Skip module loading
- Go directly to price tools
- Answer questions without loading required modules first
- Assume modules are already loaded

IF YOU DO NOT FOLLOW THIS RULE, YOU WILL CAUSE REVENUE LOSS AND CUSTOMER SERVICE FAILURES.

{json.dumps(base_modules, ensure_ascii=False)}

DYNAMIC MODULE LOADING INSTRUCTIONS:

You have been loaded with the base system configuration above. Now you must:

1. Analyze the user's query and conversation context
2. Use the DECISION_TREE and MODULE_DEPENDENCIES to determine which additional modules you need:
   - MODULE_1_CRITICAL_WORKFLOWS: For specialized blocking protocols (member handling, handover, occupancy enforcement, date validation, etc.)
   - MODULE_2A_PACKAGE_CONTENT: For package details ("qué incluye")
   - MODULE_2B_PRICE_INQUIRY: For pricing, quotes, payments (CONTAINS ROMÁNTICO +$20 RULE!)
   - MODULE_2C_AVAILABILITY: For checking room availability and inventory
   - MODULE_2D_SPECIAL_SCENARIOS: For membership, all-inclusive objections, special events (can micro-load)
   - MODULE_3_SERVICE_FLOWS: For existing reservations, changes, cancellations  
   - MODULE_4_INFORMATION: For facilities, schedules, general information

3. 🚨 MANDATORY MODULE EVALUATION: For EVERY query, you MUST evaluate what modules you need
4. 🚨 ALWAYS LOAD REQUIRED MODULES: Even if loaded before, load again if needed for current query
5. For pricing/quotes: ALWAYS load MODULE_2B_PRICE_INQUIRY (contains pricing_logic with Romántico +$20 surcharge)
6. For availability: ALWAYS load MODULE_2C_AVAILABILITY before pricing
7. BLOCKED: You CANNOT respond until required modules are loaded for THIS SPECIFIC QUERY
7. Use ONLY the loaded module instructions to respond

RULE: Don't assume previous loads are sufficient. Each query = fresh module evaluation + loading.

EXAMPLE: If user requests "cotización" you MUST:
1. FIRST: load_additional_modules(["MODULE_2C_AVAILABILITY", "MODULE_2B_PRICE_INQUIRY"], "Need availability check then pricing for quote")
2. THEN: Use the loaded pricing_logic rules (like Romántico +$20 surcharge)  
3. FINALLY: Respond with accurate pricing

This ensures optimal performance and relevant responses.
"""
    
    return classification_prompt

async def load_additional_modules(modules: List[str], reasoning: str, user_identifier: str = None, **kwargs) -> str:
    """
    Tool function that loads and returns requested module content.
    
    Supports:
    1. Sub-modules: "MODULE_2A_PACKAGE_CONTENT", "MODULE_2B_PRICE_INQUIRY", "MODULE_2C_AVAILABILITY", "MODULE_2D_SPECIAL_SCENARIOS"
    2. Micro-loading: "MODULE_2D_SPECIAL_SCENARIOS.membership_sales_protocol"
    
    Optimization: Skips modules loaded within the last 3 messages unless forced.
    
    Examples:
        - load_additional_modules(["MODULE_2A_PACKAGE_CONTENT"], "Customer asks what's included")
        - load_additional_modules(["MODULE_2D_SPECIAL_SCENARIOS.all_inclusive_inquiry_protocol"], "All-inclusive objection")
        - load_additional_modules(["MODULE_2B_PRICE_INQUIRY", "MODULE_2C_AVAILABILITY"], "Price inquiry needs availability check")
    """
    from .thread_store import get_loaded_modules, save_loaded_modules, get_message_count
    
    # Check if modules were recently loaded (within last 3 messages)
    modules_to_load = []
    skipped_modules = []
    current_message_num = get_message_count(user_identifier) if user_identifier else 0
    
    if user_identifier:
        loaded_info = get_loaded_modules(user_identifier)
        if loaded_info:
            recently_loaded = loaded_info.get("modules", [])
            load_message_num = loaded_info.get("message_num", 0)
            
            # Skip modules loaded within last 3 messages
            if current_message_num - load_message_num <= 3:
                for module in modules:
                    if module in recently_loaded:
                        skipped_modules.append(module)
                        logger.info(f"[MODULE_OPTIMIZATION] Skipping {module} - loaded {current_message_num - load_message_num} message(s) ago")
                    else:
                        modules_to_load.append(module)
            else:
                modules_to_load = modules
        else:
            modules_to_load = modules
    else:
        modules_to_load = modules
    
    # If all modules were skipped, return early
    if not modules_to_load:
        loaded_content = "=== BASE MODULES ALREADY LOADED ===\n"
        loaded_content += "MODULE_SYSTEM, DECISION_TREE, MODULE_DEPENDENCIES, CORE_CONFIG\n\n"
        loaded_content += f"=== REQUESTED MODULES ALREADY LOADED ===\n"
        loaded_content += f"Reasoning: {reasoning}\n"
        loaded_content += f"Modules: {', '.join(skipped_modules)}\n"
        loaded_content += f"These modules were loaded {current_message_num - loaded_info.get('message_num', 0)} message(s) ago and are still in context.\n"
        logger.info(f"[MODULE_OPTIMIZATION] All requested modules already loaded - saved tokens")
        return loaded_content
    
    with open('app/resources/system_instructions_new.txt', 'r', encoding='utf-8') as f:
        all_modules = json.loads(f.read())
    
    # Always include base modules
    loaded_content = "=== BASE MODULES ALREADY LOADED ===\n"
    loaded_content += "MODULE_SYSTEM, DECISION_TREE, MODULE_DEPENDENCIES, CORE_CONFIG (includes universal safety protocols)\n\n"
    
    if skipped_modules:
        loaded_content += f"=== RECENTLY LOADED MODULES (SKIPPED) ===\n"
        loaded_content += f"{', '.join(skipped_modules)} - already loaded {current_message_num - loaded_info.get('message_num', 0)} message(s) ago\n\n"
    
    loaded_content += f"=== LOADING ADDITIONAL MODULES ===\nReasoning: {reasoning}\n\n"
    
    for module_ref in modules_to_load:
        # Check if micro-loading (has dot notation)
        if '.' in module_ref:
            # Micro-load: MODULE_2D_SPECIAL_SCENARIOS.membership_sales_protocol
            parts = module_ref.split('.')
            module_name = parts[0]
            protocol_path = parts[1:]
            
            if module_name not in all_modules:
                logger.warning(f"[DYNAMIC_LOADING] Module not found: {module_name}")
                continue
            
            # Navigate to the specific protocol
            content = all_modules[module_name]
            for key in protocol_path:
                if isinstance(content, dict) and key in content:
                    content = content[key]
                else:
                    logger.warning(f"[DYNAMIC_LOADING] Protocol path not found: {module_ref}")
                    content = None
                    break
            
            if content is not None:
                loaded_content += f"=== {module_ref} (MICRO-LOAD) ===\n"
                loaded_content += json.dumps({protocol_path[-1]: content}, ensure_ascii=False, indent=2) + "\n\n"
                logger.info(f"[DYNAMIC_LOADING] Micro-loaded: {module_ref}")
            
        else:
            # Full module or sub-module load
            if module_ref in all_modules:
                loaded_content += f"=== {module_ref} ===\n"
                loaded_content += json.dumps(all_modules[module_ref], ensure_ascii=False, indent=2) + "\n\n"
                logger.info(f"[DYNAMIC_LOADING] Loaded full module: {module_ref}")
            else:
                logger.warning(f"[DYNAMIC_LOADING] Module not found: {module_ref}")
    
    loaded_content += "\n=== INSTRUCTIONS ===\n"
    loaded_content += "Use ALL loaded modules (base + additional) to provide a comprehensive response to the user.\n"
    loaded_content += "Follow all protocols and guidelines from the loaded modules."
    
    # Save loaded modules for tracking
    if user_identifier:
        all_loaded = list(set(modules_to_load + skipped_modules))  # Combine and deduplicate
        save_loaded_modules(user_identifier, all_loaded, current_message_num)
        logger.info(f"[MODULE_OPTIMIZATION] Saved {len(all_loaded)} loaded modules at message {current_message_num}")
    
    return loaded_content

tools = [
    {
        "type": "function",
        "name": "load_additional_modules",
        "description": "🚨 HIGHEST PRIORITY TOOL 🚨 Load additional instruction modules/sub-modules needed to respond to the user query. Supports: (1) Sub-modules: MODULE_2A_PACKAGE_CONTENT, MODULE_2B_PRICE_INQUIRY, MODULE_2C_AVAILABILITY, MODULE_2D_SPECIAL_SCENARIOS, (2) Micro-loading: MODULE_2D_SPECIAL_SCENARIOS.membership_sales_protocol. MUST BE CALLED FIRST for pricing/quotes/bookings/service requests.",
        "parameters": {
            "type": "object",
            "properties": {
                "modules": {
                    "type": "array", 
                    "items": {"type": "string"},
                    "description": "Array of additional module/sub-module/protocol names to load. Options: MODULE_1_CRITICAL_WORKFLOWS, MODULE_2A_PACKAGE_CONTENT, MODULE_2B_PRICE_INQUIRY, MODULE_2C_AVAILABILITY, MODULE_2D_SPECIAL_SCENARIOS (or micro-load with dot notation: MODULE_2D_SPECIAL_SCENARIOS.membership_sales_protocol), MODULE_3_SERVICE_FLOWS, MODULE_4_INFORMATION."
                },
                "reasoning": {
                    "type": "string",
                    "description": "Brief explanation of why these modules were selected based on the query analysis"
                }
            },
            "required": ["modules", "reasoning"]
        }
    },
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
        "name": "transfer_to_human_agent",
        "description": "Transfer the conversation to a human agent. Use this when check_office_status indicates can_automate=false, or when a complex situation requires human intervention. This will change the conversation status to PENDING and assign it to the reservations team.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
        "name": "get_price_for_date",
        "description": "🚨 REQUIRES MODULE_2B_PRICE_INQUIRY LOADED FIRST 🚨 Get the price for a specific date for all available packages: Day Pass/Pasadía (pa_adulto, pa_nino), Accommodation/Las Hojas (lh_adulto, lh_nino), and Paquete Escapadita (es_adulto, es_nino). CRITICAL: You MUST call load_additional_modules(['MODULE_2B_PRICE_INQUIRY']) BEFORE using this tool to get pricing rules like Romántico +$20 surcharge. IMPORTANT: For daypass/pasadía questions use pa_ prices, for accommodation/overnight stays use lh_ prices.",
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
        "description": "Sends the hotel's restaurant menu in PDF format to the user. Use this when the user asks for the menu or food options. IMPORTANT: The caption you provide will be sent as the complete message to the user along with the PDF - do NOT generate an additional response after calling this tool.",
        "parameters": {
            "type": "object",
            "properties": {
                "caption": {
                    "type": "string",
                    "description": "A complete, friendly message to send along with the menu PDF. This will be the ONLY message the user receives, so make it complete and informative. For example: '¡Con mucho gusto! 🌴 Aquí le envío nuestro menú. Si desea recomendaciones o información sobre platillos premium, con gusto le ayudo. ☀️'"
                }
            },
            "required": ["caption"]
        }
    },
    {
        "type": "function",
        "name": "read_menu_content",
        "description": "Converts the current menu PDF to high-resolution PNG images for visual analysis. MANDATORY when customer requests comprehensive menu information like 'all seafood options', 'menu details', 'list all dishes', etc. Use this to visually examine the menu and provide COMPLETE lists of dishes in requested categories. After calling this tool, you MUST analyze the images carefully and list ALL items in the requested category, not just 1-2 examples. This provides accurate dish names, prices, and descriptions exactly as they appear visually in the menu.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    },
    {
        "type": "function",
        "name": "send_menu_prices",
        "description": "🚨 HIDDEN TOOL - NEVER PROACTIVE 🚨 Sends the menu with prices in PDF format to the user. ONLY use this when the customer EXPLICITLY asks about prices of dishes, beverages, or cocktails not included in their package (wanting to exceed package limits or buy more expensive items). IMPORTANT: The caption you provide will be sent as the complete message to the user along with the menu - do NOT generate an additional response after calling this tool. NEVER mention or offer this proactively.",
        "parameters": {
            "type": "object",
            "properties": {
                "caption": {
                    "type": "string",
                    "description": "A complete, friendly message to send along with the menu prices. This will be the ONLY message the user receives, so make it complete and informative. For example: '¡Con mucho gusto! 🌴 Aquí le envío nuestro menú con precios para que pueda ver las opciones adicionales disponibles. ☀️'"
                }
            },
            "required": ["caption"]
        }
    },
    {
        "type": "function",
        "name": "read_menu_prices_content",
        "description": "🚨 HIDDEN TOOL - NEVER PROACTIVE 🚨 Converts the menu with prices PDF to high-resolution PNG images for visual analysis. ONLY use when customer asks about specific prices or costs of additional items (exceeding package limits or premium options). Use this to visually examine the menu prices and provide accurate pricing information. NEVER mention or offer this proactively.",
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
                    "description": "Customer's phone number. For WhatsApp (WATI) users, use 'AUTO' and it will be extracted from waId. For Facebook/Instagram users, this MUST be explicitly asked from the customer and provided here (cannot be inferred)."
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
                    "description": "HEAD COUNT of children aged 0-5 years old (must be explicitly provided, 0 if none). This is the NUMBER OF CHILDREN in this age bracket, NOT the number of child packages purchased for them."
                },
                "children_6_10": {
                    "type": "integer",
                    "description": "HEAD COUNT of children aged 6-10 years old (must be explicitly provided, 0 if none). This is the NUMBER OF CHILDREN in this age bracket who automatically receive child package pricing."
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
        "description": "Sends an email to internal hotel staff based on specific use cases. CRITICAL: This tool is ONLY for internal notifications and MUST NOT be used to email customers. The to_emails parameter is strictly validated. Use cases: 1. For membership inquiries, email promociones@lashojasresort.com. 2. For group quotes (30+ people), email sbartenfeld@lashojasresort.com and acienfuegos@lashojasresort.com. 3. For last-minute customer information for reception, email reservas@lashojasresort.com. 4. For job inquiries/employment opportunities, email lnajera@lashojasresort.com and recursoshumanos@lashojasresort.com.",
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
                    "description": "The subject of the email, clearly stating the purpose (e.g., 'Membership Inquiry', 'Group Quote Request', 'Job Inquiry')."
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
    },
    {
        "type": "function",
        "name": "handle_customer_transferencia_type_response",
        "description": "Handle customer response when they clarify whether they used 'Transferencia UNI' or 'Transferencia 365'. Call this when customer responds to questions about their transfer type after 60 minutes of failed bank transfer validation. UNI transfers have limited business hours (Mon-Fri 9AM-5PM) and may require escalation to human agents.",
        "parameters": {
            "type": "object",
            "properties": {
                "phone_number": {
                    "type": "string",
                    "description": "Customer's phone number"
                },
                "response_text": {
                    "type": "string", 
                    "description": "Customer's response message about which transfer type they used"
                }
            },
            "required": ["phone_number", "response_text"]
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
                model="gpt-5.1",
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
                model="gpt-5.1",
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
    base_path = "/home/robin/watibot4/app/resources/pictures"
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
    base_path = "/home/robin/watibot4/app/resources/pictures"
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
) -> str:
    """Send the restaurant menu PDF via ManyChat (FB/IG) or WhatsApp.

    - Uses ManyChat when `subscriber_id` and `channel` provided.
    - Falls back to WATI (WhatsApp) when `phone_number` provided.
    
    Returns a confirmation message that serves as the final response to the user.
    """
    menu_pdf_path = "/home/robin/watibot4/app/resources/menu.pdf"
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
                # Return empty string since caption already contains the complete message
                return ""
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
                # Return empty string since caption already contains the complete message
                return ""

        # Facebook: send PDF as file attachment
        await manychat_client.send_media_message(
            subscriber_id=subscriber_id,
            file_path=menu_pdf_path,
            media_type="file",
            channel=channel,
            caption=caption,
        )
        # Return empty string since caption already contains the complete message
        return ""
    elif phone_number:
        result = await wati_client.send_wati_file(
            phone_number=phone_number, caption=caption, file_path=menu_pdf_path
        )
        # Return empty string since caption already contains the complete message
        return ""
    else:
        logger.error("No subscriber_id/channel or phone_number provided to send menu PDF.")
        return "Lo siento, no pude identificar tu canal para enviar el menú. Por favor intenta de nuevo."

async def send_menu_prices_wrapper(
    caption: str,
    phone_number: Optional[str] = None,
    subscriber_id: Optional[str] = None,
    channel: Optional[str] = None,
) -> str:
    """Send the restaurant menu with prices via ManyChat (FB/IG) or WhatsApp.

    - Uses ManyChat when `subscriber_id` and `channel` provided.
    - Falls back to WATI (WhatsApp) when `phone_number` provided.
    - For Instagram: converts PDF to images (IG doesn't support PDF attachments)
    
    Returns a confirmation message that serves as the final response to the user.
    """
    menu_prices_pdf_path = "/home/robin/watibot4/app/resources/menu_prices.pdf"
    if subscriber_id and channel in ("facebook", "instagram"):
        # Instagram: convert PDF to images and send as images (IG doesn't support PDF attachments)
        if channel == "instagram":
            try:
                import fitz  # PyMuPDF
                from pathlib import Path
                import time as _time
                out_dir = Path(__file__).resolve().parent / "resources" / "pictures" / "menu_prices_converted"
                out_dir.mkdir(parents=True, exist_ok=True)
                ts = int(_time.time())
                doc = fitz.open(menu_prices_pdf_path)
                image_paths = []
                for i, page in enumerate(doc):
                    pix = page.get_pixmap(dpi=144)
                    img_path = out_dir / f"menu_prices_{ts}_p{i+1}.png"
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
                # Return empty string since caption already contains the complete message
                return ""
            except Exception as e:
                logger.warning(
                    f"[IG] PDF->image conversion unavailable or failed ({e}). Sending link as text."
                )
                # Fallback: send caption then link as text message
                await manychat_client.send_media_message(
                    subscriber_id=subscriber_id,
                    file_path=menu_prices_pdf_path,
                    media_type="file",
                    channel=channel,
                    caption=caption,
                )
                # Return empty string since caption already contains the complete message
                return ""

        # Facebook: send PDF as file attachment
        await manychat_client.send_media_message(
            subscriber_id=subscriber_id,
            file_path=menu_prices_pdf_path,
            media_type="file",
            channel=channel,
            caption=caption,
        )
        # Return empty string since caption already contains the complete message
        return ""
    elif phone_number:
        result = await wati_client.send_wati_file(
            phone_number=phone_number, caption=caption, file_path=menu_prices_pdf_path
        )
        # Return empty string since caption already contains the complete message
        return ""
    else:
        logger.error("No subscriber_id/channel or phone_number provided to send menu prices.")
        return "Lo siento, no pude identificar tu canal para enviar el menú de precios. Por favor intenta de nuevo."

async def transfer_to_human_agent_wrapper(phone_number: str = None, subscriber_id: str = None, channel: str = None) -> str:
    """Transfer conversation to human agent by triggering the handover process."""
    if phone_number:
        # WATI platform
        await wati_client.handle_handover(phone_number)
        logger.info(f"[TRANSFER] Successfully transferred {phone_number} to human agent")
        return "transfer_completed"
    else:
        # ManyChat platform - not yet implemented
        logger.warning(f"[TRANSFER] ManyChat handover not implemented for subscriber {subscriber_id}")
        return "transfer_not_supported_for_platform"

available_functions = {
    "get_price_for_date": database_client.get_price_for_date,
    "send_location_pin": format_location_as_text,
    "send_menu_pdf": send_menu_pdf_wrapper,
    "read_menu_content": menu_reader.read_menu_content_wrapper,
    "send_menu_prices": send_menu_prices_wrapper,
    "read_menu_prices_content": menu_prices_reader.read_menu_prices_content_wrapper,
    "analyze_payment_proof": payment_proof_analyzer.analyze_payment_proof,
    "check_office_status": office_status_tool.check_office_status,
    "transfer_to_human_agent": transfer_to_human_agent_wrapper,
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
    "handle_customer_transferencia_type_response": bank_transfer_retry.handle_customer_transferencia_type_response,
    "trigger_compraclick_retry_for_missing_payment": compraclick_tool.trigger_compraclick_retry_for_missing_payment,
    "make_booking": booking_tool.make_booking,
    "send_email": email_service.send_email,
    "notify_operations_department": operations_tool.notify_operations_department,
    "load_additional_modules": load_additional_modules,
}

async def rotate_conversation_thread(old_conversation_id: str, wa_id: str, current_message: str, system_instructions: str) -> str:
    """
    Creates a new conversation thread when the current one exceeds context window.
    Seeds the new thread with essential context from the old one.
    """
    from .thread_store import reset_message_count, clear_loaded_modules
    
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
        
        # Reset message count and clear loaded modules for fresh start
        reset_message_count(wa_id)
        clear_loaded_modules(wa_id)
        logger.info(f"[MODULE_OPTIMIZATION] Reset message count and cleared loaded modules for {wa_id}")
        
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
    time_since_last_message: Optional[float] = None,
) -> Tuple[str, str]:
    """Send message to OpenAI using new Responses API and return response, handling function calls.

    Migrated from Assistant API to Responses API. Now supports multi-channel messaging by 
    auto-injecting `subscriber_id` and `channel` into tool calls that accept them (ManyChat), 
    while preserving WhatsApp compatibility via `phone_number`/`wa_id`.
    
    Uses conversation IDs instead of thread IDs for conversation context management.
    """
    from .thread_store import (
        get_conversation_id, save_conversation_id, get_last_response_id, save_response_id,
        increment_message_count, get_message_count
    )
    
    el_salvador_tz = timezone("America/El_Salvador")
    now_in_sv = datetime.now(el_salvador_tz)
    datetime_str = now_in_sv.strftime("%A, %Y-%m-%d, %H:%M")
    
    # Get or increment message count for this conversation
    user_identifier = phone_number or subscriber_id or "unknown"
    current_message_count = increment_message_count(user_identifier)
    
    # Load dynamic system instructions with base modules
    system_instructions = build_classification_system_prompt()
    
    # Log token optimization
    try:
        with open('app/resources/system_instructions.txt', 'r', encoding='utf-8') as f:
            original_size = len(f.read())
        base_size = len(system_instructions)
        initial_reduction = ((original_size - base_size) / original_size) * 100
        logger.info(f"[DYNAMIC_LOADING] Base modules loaded: {base_size} chars, Initial reduction: {initial_reduction:.1f}%")
    except Exception as e:
        logger.info(f"[DYNAMIC_LOADING] Using dynamic loading with base size: {len(system_instructions)} chars")
    
    # Build the contextualized user message (same as before)
    contextualized_message = (
        "Persist until the customer's query is fully handled end-to-end within the current turn: "
        "Before responding, evaluate what additional modules are needed based on core_config, decision_tree and module_dependencies, "
        "call load_additional_modules to load them, wait for content, then respond using that information. "
        "Do not stop at partial responses or skip required module loading steps.\n\n"
        "CRITICAL Agent Discipline: Even if the base modules: core_config, decision_tree and module_dependencies were last sent several turns ago, you must keep obeying every rule they contain. Persist through each customer directive end-to-end—gather all required details, call the mandated tools, validate the outcome, and confirm completion before moving on. No shortcuts, no early exits. "
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
        f"  * 🚨 CRITICAL: When analyze_payment_proof returns 'receipt_type: compraclick', the 'transaction_id' field in 'extracted_info' IS the authorization code. Use extracted_info.transaction_id directly as authorization_number. DO NOT ask customer for authorization code if already extracted. "
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
        f"  1. IMMEDIATELY call `transfer_to_human_agent` to transfer the conversation to the reservations team. "
        f"  2. After calling transfer_to_human_agent, send a complete customer message that: "
        f"     - Confirms payment validation success "
        f"     - Explains that since offices are open, they'll be transferred to a human agent to complete booking "
        f"     - Provides booking summary and reassurance "
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
    # NOTE: user_identifier already set above when incrementing message count
    
    # CRITICAL FIX: Only use thread_id if it's a valid conversation ID (starts with 'conv_')
    # Old data may have phone numbers stored as thread_id, which causes 400 errors
    if thread_id and thread_id.startswith('conv_'):
        conversation_id = thread_id
        save_conversation_id(user_identifier, thread_id)
    else:
        # Either no thread_id, or it's invalid (phone number) - get from database
        conversation_id = get_conversation_id(user_identifier)
        if thread_id and not thread_id.startswith('conv_'):
            logger.warning(f"[OpenAI] Invalid thread_id format: {thread_id}. Will create new conversation.")
    
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
                get_agent_context_for_system_injection,
                get_manychat_context_for_system_injection
            )
            
            needs_agent_context = not check_if_agent_context_injected(conversation_id)
            agent_context_system_msg = ""
            
            if needs_agent_context:
                # Get context based on user type (same logic as thread rotation)
                if phone_number and phone_number.isdigit():
                    # WATI user: use WATI API to fetch message history
                    agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                else:
                    # ManyChat user (Facebook/Instagram): use local thread_store to fetch message history
                    agent_context_system_msg = get_manychat_context_for_system_injection(user_identifier)
                
                if agent_context_system_msg:
                    logger.info(f"[AGENT_CONTEXT] Injecting agent context for conversation {conversation_id}")
                    
                    # FIRST API CALL: Send ONLY agent context in developer input
                    # For fresh conversations, use conversation parameter for first call only
                    agent_response = await openai_client.responses.create(
                        model="gpt-5.1",
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
            
            # REMOVED: Duplicate missed messages check - now handled in main.py timer_callback
            # The missed messages are already prepended to the user_message before this function is called
            # This prevents the race condition where the timestamp is updated before the check
            
            # Use regular developer message (missed messages already in user_message if applicable)
            enhanced_developer_message = contextualized_message
            
            # SECOND API CALL: Send normal message (system + developer + user)
            # Always use previous_response_id to avoid stale tool call conflicts
            
            # Determine if we should send system_instructions (base_modules)
            # Send on: conversation start, after context injection, every 4 messages, or if >2 hours since last message
            time_gap_refresh = time_since_last_message and time_since_last_message > 7200  # 2 hours (7200 seconds)
            should_send_base_modules = (
                not previous_response_id or  # New conversation
                needs_agent_context or  # Just injected agent context
                current_message_count % 4 == 0 or  # Every 4 messages
                time_gap_refresh  # >2 hours since last message (prevents stale module cache)
            )
            
            if should_send_base_modules:
                time_str = f"{time_since_last_message:.1f}s" if time_since_last_message is not None else "N/A"
                logger.info(f"[MODULE_OPTIMIZATION] Sending base_modules at message {current_message_count} (new_conv={not previous_response_id}, after_context={needs_agent_context}, periodic={current_message_count % 4 == 0}, time_gap={time_gap_refresh} [{time_str} since last])")
            else:
                time_str = f"{time_since_last_message:.1f}s" if time_since_last_message is not None else "N/A"
                logger.info(f"[MODULE_OPTIMIZATION] Skipping base_modules at message {current_message_count} - using cached from previous_response_id ({time_str} since last)")

            
            if previous_response_id:
                # Continue from existing response
                input_messages = []
                
                # Conditionally add system instructions
                if should_send_base_modules:
                    input_messages.append({
                        "type": "message",
                        "role": "system",
                        "content": [{"type": "input_text", "text": system_instructions}]
                    })
                
                input_messages.extend([
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
                ])
                
                response = await openai_client.responses.create(
                    model="gpt-5.1",
                    previous_response_id=previous_response_id,
                    input=input_messages,
                    tools=tools,
                    max_output_tokens=4000
                )
                logger.info(f"[OpenAI] Continued from response {previous_response_id}")
            else:
                # New conversation - use conversation parameter only for the very first call
                # Always send system_instructions on first call
                response = await openai_client.responses.create(
                    model="gpt-5.1",
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
                
                # Reset message count and clear loaded modules for fresh start
                from .thread_store import reset_message_count, clear_loaded_modules
                from agent_context_injector import (
                    get_agent_context_for_system_injection,
                    get_manychat_context_for_system_injection
                )
                reset_message_count(user_identifier)
                clear_loaded_modules(user_identifier)
                # Increment to get message 1 for fresh conversation
                current_message_count = increment_message_count(user_identifier)
                logger.info(f"[OpenAI] Created fresh conversation {conversation_id} with reset counters (now at message {current_message_count})")
                
                # INJECT AGENT CONTEXT first for fresh conversation (channel-aware)
                if phone_number and phone_number.isdigit():
                    # WATI user: use WATI API to fetch message history
                    agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                else:
                    # ManyChat user (Facebook/Instagram): use local thread_store to fetch message history
                    agent_context_system_msg = get_manychat_context_for_system_injection(user_identifier)
                
                if agent_context_system_msg:
                    logger.info(f"[AGENT_CONTEXT] Injecting agent context for fresh conversation {conversation_id}")
                    agent_response = await openai_client.responses.create(
                        model="gpt-5.1",
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
                    
                    # Determine if we should send base_modules (always True for fresh conversation)
                    should_send_base_modules = (
                        True  # Fresh conversation after recovery - always send base modules
                    )
                    
                    logger.info(f"[MODULE_OPTIMIZATION] Sending base_modules at message {current_message_count} (new_conv=True, after_context=True, periodic=False)")
                    
                    # RESTART THE ENTIRE FLOW with fresh conversation
                    input_messages = [
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
                    ]
                    
                    response = await openai_client.responses.create(
                        model="gpt-5.1",
                        previous_response_id=agent_response.id,
                        input=input_messages,
                        tools=tools,
                        max_output_tokens=4000
                    )
                    
                    # Save main response ID
                    save_response_id(user_identifier, response.id)
                    logger.info(f"[OpenAI] Successfully restarted with fresh conversation")
                else:
                    # No agent context available - make API call directly with fresh conversation
                    logger.info(f"[AGENT_CONTEXT] No agent context available for fresh conversation {conversation_id}")
                    
                    # Determine if we should send base_modules (always True for fresh conversation)
                    should_send_base_modules = True
                    logger.info(f"[MODULE_OPTIMIZATION] Sending base_modules at message {current_message_count} (new_conv=True, after_context=False, periodic=False)")
                    
                    response = await openai_client.responses.create(
                        model="gpt-5.1",
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
                    
                    # Save response ID
                    save_response_id(user_identifier, response.id)
                    logger.info(f"[OpenAI] Successfully started fresh conversation without context")
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
                            
                            # CRITICAL: Always override wa_id with the actual identifier
                            if 'wa_id' in sig.parameters:
                                # For WATI users, wa_id is the phone_number
                                # For ManyChat users, wa_id should be the subscriber_id
                                if phone_number and phone_number.isdigit():
                                    fn_args['wa_id'] = phone_number
                                elif subscriber_id:
                                    fn_args['wa_id'] = subscriber_id
                            
                            # CRITICAL: Handle phone_number parameter based on channel and function type
                            if 'phone_number' in sig.parameters:
                                current_phone = fn_args.get('phone_number', '')
                                # Override if assistant sent placeholder values or empty
                                if current_phone in ['', 'AUTO', 'from_wa_id', 'from_waid']:
                                    if phone_number and phone_number.isdigit():
                                        # Check if this function needs the actual phone_number (not extracted from wa_id)
                                        # These functions send media or perform WATI API operations requiring phone_number
                                        functions_needing_phone = [
                                            'send_bungalow_pictures', 
                                            'send_public_areas_pictures', 
                                            'send_menu_pdf',  # Tool name, not function name
                                            'send_menu_prices',  # Tool name, not function name
                                            'transfer_to_human_agent'
                                        ]
                                        
                                        if fn_name in functions_needing_phone:
                                            # Media functions need the actual phone_number to send files
                                            logger.info(f"[PHONE_FIX] WATI user - Injecting phone_number for {fn_name}: {phone_number}")
                                            fn_args['phone_number'] = phone_number
                                        else:
                                            # WATI user: keep empty so booking_tool extracts local number from wa_id
                                            # The _extract_phone_from_wa_id function handles country code removal properly
                                            logger.info(f"[PHONE_FIX] WATI user - phone_number was '{current_phone}', keeping empty so booking_tool extracts from wa_id: {phone_number}")
                                            fn_args['phone_number'] = ''
                                    else:
                                        # ManyChat user: phone must be asked from customer, keep empty
                                        logger.warning(f"[PHONE_FIX] ManyChat user - phone_number was '{current_phone}' but cannot auto-fill. Function will receive empty string.")
                                        fn_args['phone_number'] = ''
                            
                            if 'subscriber_id' in sig.parameters and subscriber_id:
                                fn_args.setdefault('subscriber_id', subscriber_id)
                            if 'channel' in sig.parameters and channel:
                                fn_args.setdefault('channel', channel)
                            if 'user_identifier' in sig.parameters and user_identifier:
                                fn_args.setdefault('user_identifier', user_identifier)
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
                    model="gpt-5.1",
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
                                model="gpt-5.1",
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
                    
                    # Reset message count and clear loaded modules for fresh start
                    from .thread_store import reset_message_count, clear_loaded_modules
                    from agent_context_injector import (
                        get_agent_context_for_system_injection,
                        get_manychat_context_for_system_injection
                    )
                    reset_message_count(user_identifier)
                    clear_loaded_modules(user_identifier)
                    # Increment to get message 1 for fresh conversation
                    current_message_count = increment_message_count(user_identifier)
                    logger.info(f"[Tool] Created fresh conversation {conversation_id} for recovery with reset counters (now at message {current_message_count})")
                    
                    # INJECT AGENT CONTEXT first for fresh conversation (channel-aware)
                    if phone_number and phone_number.isdigit():
                        # WATI user: use WATI API to fetch message history
                        agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                    else:
                        # ManyChat user (Facebook/Instagram): use local thread_store to fetch message history
                        agent_context_system_msg = get_manychat_context_for_system_injection(user_identifier)
                    if agent_context_system_msg:
                        logger.info(f"[AGENT_CONTEXT] Injecting agent context for fresh recovery conversation {conversation_id}")
                        # Fresh conversation recovery - use conversation parameter for first call
                        agent_response = await openai_client.responses.create(
                            model="gpt-5.1",
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
                        
                        # Log base_modules sending
                        logger.info(f"[MODULE_OPTIMIZATION] Sending base_modules at message {current_message_count} (new_conv=True, after_context=True, periodic=False)")
                        
                        # RESTART THE ENTIRE FLOW with fresh conversation - use previous_response_id after context
                        response = await openai_client.responses.create(
                            model="gpt-5.1",
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
                    else:
                        # No agent context - send directly with conversation ID
                        logger.info(f"[AGENT_CONTEXT] No agent context available for fresh recovery conversation {conversation_id}")
                        logger.info(f"[MODULE_OPTIMIZATION] Sending base_modules at message {current_message_count} (new_conv=True, after_context=False, periodic=False)")
                        
                        response = await openai_client.responses.create(
                            model="gpt-5.1",
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
        final_response = _extract_text_from_output(getattr(response, "output", [])) or ""
        
        # Check if send_menu_pdf or send_menu_prices was called - if so, suppress duplicate final response
        # The caption parameter already contains the complete message to the user
        menu_pdf_called = any(fn_name == "send_menu_pdf" for fn_name, _ in all_tool_outputs)
        menu_prices_called = any(fn_name == "send_menu_prices" for fn_name, _ in all_tool_outputs)
        if menu_pdf_called or menu_prices_called:
            tool_name = "send_menu_pdf" if menu_pdf_called else "send_menu_prices"
            logger.info(f"[MENU_PDF] {tool_name} was called with caption - suppressing duplicate final response")
            return "", conversation_id  # Return empty string to prevent duplicate message
        
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
                model="gpt-5.1",
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
            
            final_response = _extract_text_from_output(getattr(response, "output", [])) or "No response generated."
        
        # Apply JSON guard (preserve from memory) - only if still seems problematic
        if final_response and len(final_response) < 200:
            try:
                parsed = json.loads(final_response)
                if any(k in str(parsed).lower() for k in ['function', 'arguments', 'name']):
                    logger.info("[JSON_GUARD] Detected tool-like JSON, repairing")
                    # JSON repair - use conversation for simplicity since this is error recovery
                    repair_response = await openai_client.responses.create(
                        model="gpt-5.1",
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
                    final_response = _extract_text_from_output(getattr(repair_response, "output", [])) or final_response
            except:
                pass  # Not JSON, use as-is
        
        logger.info(f"Final response from OpenAI: {final_response[:100]}...")
        
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
                    model="gpt-5.1",
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
                final_response = _extract_text_from_output(getattr(response, "output", [])) or "No response after retry."
                
                return final_response, conversation_id
            except Exception as retry_error:
                logger.error(f"Rate limit retry failed: {retry_error}")
                # Let this bubble up to main.py retry logic instead of returning error message
                raise retry_error
        elif "context_length_exceeded" in str(e).lower() or "context window" in str(e).lower():
            # Handle context window overflow by rotating to new conversation
            logger.warning(f"[THREAD_ROTATION] Context window exceeded, rotating to new conversation thread")
            try:
                new_conversation_id = await rotate_conversation_thread(conversation_id, user_identifier, contextualized_message, system_instructions)
                if new_conversation_id:
                    # CRITICAL: Clear old response ID to prevent using old conversation's response
                    save_response_id(user_identifier, None)
                    logger.info(f"[THREAD_ROTATION] Cleared old response ID for fresh start")
                    # Note: Message count and loaded modules already reset in rotate_conversation_thread
                    
                    # Force agent context injection for new conversation
                    logger.info(f"[THREAD_ROTATION] Injecting agent context for new conversation {new_conversation_id}")
                    
                    from agent_context_injector import (
                        get_agent_context_for_system_injection,
                        get_manychat_context_for_system_injection,
                        mark_agent_context_injected
                    )
                    
                    # Get context based on user type
                    if phone_number:
                        # WATI user: use WATI API to fetch message history
                        agent_context_system_msg = get_agent_context_for_system_injection(phone_number)
                    else:
                        # ManyChat user: use local thread_store to fetch message history
                        agent_context_system_msg = get_manychat_context_for_system_injection(user_identifier)
                    if agent_context_system_msg:
                        # FIRST API CALL: Send agent context only  
                        # Fresh conversation - use conversation parameter for first call
                        agent_response = await openai_client.responses.create(
                            model="gpt-5.1",
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
                    # Note: The recursive call will increment message count to 1 and send base_modules
                    logger.info(f"[THREAD_ROTATION] Retrying with new conversation {new_conversation_id} through complete flow (base_modules will be sent)")
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
