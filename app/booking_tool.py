"""
WhatsApp Booking Tool

This module provides the make_booking function to process customer bookings
after payment verification. It handles:
1. Time availability checking (El Salvador timezone)
2. Customer information validation
3. Room availability and selection
4. Booking API calls
5. Database updates for payment records
"""

import os
import logging
import asyncio
import random
from datetime import datetime, timedelta
from pytz import timezone
import holidays
from typing import Dict, List, Optional, Any
from .database_client import get_db_connection, get_price_for_date
from .wati_client import send_wati_message, update_chat_status
from .bank_transfer_tool import reserve_bank_transfer
import httpx
from app import config
import json
import os
from . import thread_store, openai_agent

# Configure logging
logger = logging.getLogger(__name__)

# El Salvador timezone
EL_SALVADOR_TZ = timezone('America/El_Salvador')

# El Salvador holidays
EL_SALVADOR_HOLIDAYS = holidays.country_holidays('SV')

# Shared Normalization and Assistant Classification Functions

def _extract_phone_from_wa_id(wa_id: str) -> str:
    """
    Extract local phone number from international WhatsApp ID (waId).
    
    Supports major country codes and handles various phone number formats.
    For El Salvador customers, removes country code to get local format.
    For international customers, preserves full number for API compatibility.
    
    Args:
        wa_id: WhatsApp ID (e.g., "50376304472", "12125551234", "5215512345678")
        
    Returns:
        Extracted phone number suitable for booking API
    """
    if not wa_id or not wa_id.strip():
        logger.warning("[PHONE_EXTRACTION] Empty wa_id provided")
        return ""
    
    wa_id = wa_id.strip()
    logger.info(f"[PHONE_EXTRACTION] Processing wa_id: {wa_id}")
    
    # Country code mapping with extraction rules
    country_codes = {
        # El Salvador - Extract local number (remove 503 prefix)
        '503': {
            'name': 'El Salvador',
            'extract_local': True,
            'min_length': 11,  # 503 + 8 digits
            'max_length': 11
        },
        # USA/Canada - Keep full number for international handling
        '1': {
            'name': 'USA/Canada', 
            'extract_local': False,
            'min_length': 11,  # 1 + 10 digits
            'max_length': 11
        },
        # Mexico - Keep full number
        '52': {
            'name': 'Mexico',
            'extract_local': False, 
            'min_length': 12,  # 52 + 10 digits
            'max_length': 13   # 52 + 11 digits (mobile)
        },
        # Guatemala - Keep full number
        '502': {
            'name': 'Guatemala',
            'extract_local': False,
            'min_length': 11,  # 502 + 8 digits
            'max_length': 11
        },
        # Central America and common international codes
        '504': {'name': 'Honduras', 'extract_local': False, 'min_length': 11, 'max_length': 11},
        '505': {'name': 'Nicaragua', 'extract_local': False, 'min_length': 11, 'max_length': 11},
        '506': {'name': 'Costa Rica', 'extract_local': False, 'min_length': 11, 'max_length': 11},
        '507': {'name': 'Panama', 'extract_local': False, 'min_length': 11, 'max_length': 11}
    }
    
    # Try to match country codes (longest first to handle overlaps like "1" vs "52")
    for code in sorted(country_codes.keys(), key=len, reverse=True):
        if wa_id.startswith(code):
            country_info = country_codes[code]
            
            # Validate length
            if len(wa_id) < country_info['min_length'] or len(wa_id) > country_info['max_length']:
                logger.warning(f"[PHONE_EXTRACTION] wa_id {wa_id} has invalid length for {country_info['name']} (expected {country_info['min_length']}-{country_info['max_length']})")
                continue
            
            if country_info['extract_local']:
                # Extract local number (El Salvador)
                local_number = wa_id[len(code):]
                logger.info(f"[PHONE_EXTRACTION] Extracted local number for {country_info['name']}: {local_number}")
                return local_number
            else:
                # Keep full international number for API compatibility
                logger.info(f"[PHONE_EXTRACTION] Keeping full international number for {country_info['name']}: {wa_id}")
                return wa_id
    
    # No recognized country code - handle as unknown international number
    logger.warning(f"[PHONE_EXTRACTION] Unrecognized country code in wa_id: {wa_id}. Using full number as fallback.")
    return wa_id

def _normalize_bungalow_type(bungalow_type: str) -> dict:
    """
    Shared normalization function for bungalow types.
    Returns dict with success status and normalized type.
    
    Returns:
        dict: {
            "success": bool,
            "type": str (if success=True),
            "original_input": str (if success=False)
        }
    """
    bungalow_type_lower = bungalow_type.lower().strip()
    
    # Normalize to standard types using substring matching for robustness
    if any(keyword in bungalow_type_lower for keyword in ['junior', 'junor', 'jr']):
        return {"success": True, "type": "Junior"}
    elif any(keyword in bungalow_type_lower for keyword in ['familiar', 'familliar', 'family', 'familia']):
        return {"success": True, "type": "Familiar"}
    elif any(keyword in bungalow_type_lower for keyword in ['matrimonial', 'matrimonio', 'matrimonial', 'matri', 'couple']):
        return {"success": True, "type": "Matrimonial"}
    elif any(keyword in bungalow_type_lower for keyword in ['habitacion', 'habitación', 'room', 'hab']):
        return {"success": True, "type": "Habitación"}
    elif any(keyword in bungalow_type_lower for keyword in ['pasadia', 'pasadía', 'day']):
        return {"success": True, "type": "Pasadía"}
    else:
        # Normalization failed - return original input for assistant classification
        return {"success": False, "original_input": bungalow_type}

async def _get_openai_thread_id(wa_id: str) -> Optional[str]:
    """
    Get OpenAI thread ID for a customer.
    This function should be implemented based on your database schema.
    """
    try:
        conn = get_db_connection()
        if not conn:
            return None
            
        cursor = conn.cursor()
        query = "SELECT thread_id FROM customer_threads WHERE wa_id = %s ORDER BY created_at DESC LIMIT 1"
        cursor.execute(query, (wa_id,))
        result = cursor.fetchone()
        
        return result[0] if result else None
    except Exception as e:
        logger.warning(f"Could not get thread_id for {wa_id}: {e}")
        return None
    finally:
        if conn and conn.is_connected():
            conn.close()

async def _get_full_conversation_context(wa_id: str) -> str:
    """
    Get the complete conversation thread from the OpenAI Assistant thread.

    Returns:
        str: Complete conversation history formatted for AI classification
    """
    try:
        thread_info = thread_store.get_thread_id(wa_id)
        if not thread_info or not thread_info.get('thread_id'):
            logger.warning(f"[CONTEXT] No OpenAI thread_id found for {wa_id}")
            return "No conversation history found"

        thread_id = thread_info['thread_id']
        messages = await openai_agent.get_thread_messages(thread_id, limit=20)

        if not messages:
            logger.warning(f"[CONTEXT] No messages found in OpenAI thread {thread_id} for {wa_id}")
            return "No conversation history found"

        context_lines = []
        # Messages are descending, so reverse to get chronological order
        for msg in reversed(messages):
            role = "CUSTOMER" if msg.role == "user" else "ASSISTANT"
            # content is a list, iterate through it
            for content_item in msg.content:
                if content_item.type == "text":
                    context_lines.append(f"{role}: {content_item.text.value}")
        
        conversation_context = "\n".join(context_lines)
        logger.info(f"[CONTEXT] Retrieved {len(context_lines)} message lines for {wa_id} from thread {thread_id}")
        
        return conversation_context

    except Exception as e:
        logger.warning(f"Could not get full conversation context for {wa_id} from OpenAI: {e}")
        return f"No conversation context available due to error: {str(e)}"


async def _classify_accommodation_with_openai(prompt: str) -> str:
    """
    Call OpenAI API to classify accommodation type.
    This is a placeholder - implement according to your OpenAI integration.
    """
    try:
        # This would integrate with your existing OpenAI client
        # For now, return a safe fallback
        logger.warning("[ASSISTANT_CLASSIFY] OpenAI classification not implemented, using fallback")
        return "Habitación"  # Safe default
    except Exception as e:
        logger.error(f"OpenAI classification failed: {e}")
        return "Habitación"  # Safe fallback

async def _ask_assistant_for_accommodation_type(
    original_input: str,
    wa_id: str,
    full_conversation: str
) -> str:
    """
    Ask the OpenAI assistant to choose accommodation type using complete conversation context.
    
    Args:
        original_input: The bungalow type string that failed normalization
        wa_id: Customer WhatsApp ID
        full_conversation: Complete conversation history
    
    Returns:
        str: One of "Junior", "Familiar", "Matrimonial", "Habitación"
    """
    prompt = f"""ACCOMMODATION TYPE CLASSIFICATION REQUIRED

You need to classify the accommodation type based on the customer's request and conversation context.

FAILED NORMALIZATION INPUT: "{original_input}"
CUSTOMER waId: {wa_id}

COMPLETE CONVERSATION CONTEXT:
{full_conversation}

Based on the COMPLETE conversation above, analyze:
- How many people are traveling (adults + children)
- What type of accommodation they seem to prefer
- Any specific mentions of room features or needs
- Family composition and requirements

Classify into exactly ONE of these 4 accommodation types:
- "Junior": Single room, 2 beds, 1 bathroom, terraza (good for 1-3 people)
- "Familiar": 2 rooms, 2 bathrooms, sala, terraza (good for families, 4+ people)  
- "Matrimonial": Single room, 1 double bed, 1 bathroom, terraza (good for couples)
- "Habitación": Standard hotel room, 2 beds, 1 bathroom (basic accommodation)

Respond with ONLY the type name: Junior, Familiar, Matrimonial, or Habitación

Think about the conversation context and customer needs, then choose the most appropriate accommodation type."""
    
    try:
        # Call OpenAI for classification
        response = await _classify_accommodation_with_openai(prompt)
        
        # Validate response is one of the 4 valid types
        valid_types = ["Junior", "Familiar", "Matrimonial", "Habitación"]
        if response.strip() in valid_types:
            logger.info(f"[ASSISTANT_CLASSIFY] '{original_input}' classified as '{response.strip()}' based on conversation context")
            return response.strip()
        else:
            logger.warning(f"Assistant gave invalid response: {response}, defaulting to Habitación")
            return "Habitación"
            
    except Exception as e:
        logger.error(f"Assistant classification failed for '{original_input}': {e}")
        return "Habitación"  # Safe fallback

async def make_booking(
    customer_name: str,
    email: str,
    phone_number: str,
    city: str,
    dui_passport: str,
    nationality: str,
    check_in_date: str,
    check_out_date: str,
    adults: int,
    children_0_5: int,
    children_6_10: int,
    bungalow_type: str,
    package_type: str,
    payment_method: str,
    payment_amount: float,
    payment_maker_name: str,
    wa_id: str,
    authorization_number: str = None,
    transfer_id: str = None,
    force_process: bool = False,
    extra_beds: int = 0,
    extra_beds_cost: float = 0.0,
    customer_instructions: str = None
) -> dict:
    """
    Process a customer booking after payment verification.
    
    CRITICAL: This function implements internal chain of thought reasoning
    that must NEVER be exposed to customers. Only results are communicated.
    
    Args:
        customer_name: Full name of the customer
        email: Customer's email address
        phone_number: Customer's phone number (will be extracted from wa_id if empty)
        city: Customer's city of origin
        dui_passport: Customer's DUI or passport number
        nationality: Customer's nationality
        check_in_date: Check-in date in YYYY-MM-DD format
        check_out_date: Check-out date in YYYY-MM-DD format
        adults: Number of adults
        children_0_5: Number of children 0-5 years old
        children_6_10: Number of children 6-10 years old
        bungalow_type: Type of accommodation (Familiar, Junior, Matrimonial, Habitación)
        package_type: Package type (Las Hojas, Escapadita, Pasadía, Romántico)
        payment_method: Payment method (CompraClick or Depósito BAC)
        payment_amount: Total payment amount
        payment_maker_name: Name of the person who made the payment
        wa_id: WhatsApp ID (used for phone extraction and context)
        authorization_number: CompraClick authorization number (if applicable)
        transfer_id: Bank transfer ID (if applicable)
        force_process: Bypass time validation (used by retry mechanism)
        extra_beds: Number of extra beds requested (0 if none)
        extra_beds_cost: Total cost for extra beds (0.0 if free or none)
        customer_instructions: Special instructions from customer in Spanish (optional)
    
    Returns:
        dict: Booking result with success status and customer message
    """
    
    # Log booking attempt (internal only)
    logger.info(f"Booking attempt started for customer: {customer_name}, dates: {check_in_date} to {check_out_date}")
    
    # Extract phone number from wa_id if not provided
    if not phone_number or phone_number.strip() == '':
        phone_number = _extract_phone_from_wa_id(wa_id)
        logger.info(f"Phone number extracted from wa_id {wa_id}: {phone_number}")
    
    try:
        # NOTE: Time availability checking is now handled by the assistant via check_office_status() 
        # before calling this function. This ensures proper office status and automation eligibility.
        current_time = datetime.now(EL_SALVADOR_TZ)
        logger.debug(f"Current El Salvador time: {current_time}")
        logger.debug("Time validation bypassed - handled by assistant via check_office_status")
        
        # Internal Chain of Thought: Validate all required information
        logger.debug("Starting booking information validation")
        validation_result = _validate_booking_info(
            customer_name, email, phone_number, city, dui_passport, nationality,
            check_in_date, check_out_date, adults, children_0_5, children_6_10,
            bungalow_type, package_type, payment_method, payment_amount, payment_maker_name
        )
        
        if not validation_result["valid"]:
            logger.warning(f"Booking validation failed: {validation_result['error']}")
            return {
                "success": False,
                "error": validation_result["error"],
                "customer_message": validation_result["customer_message"]
            }
        
        logger.debug("Booking information validation passed")
        
        # Internal Chain of Thought: Check room availability
        logger.debug(f"Checking room availability for {check_in_date} to {check_out_date}")
        availability_result = await _check_room_availability(check_in_date, check_out_date)
        if not availability_result["success"]:
            logger.warning(f"Room availability check failed: {availability_result['error']}")
            return {
                "success": False,
                "error": availability_result["error"],
                "customer_message": availability_result["customer_message"]
            }
            
        # Internal Chain of Thought: Select appropriate room
        logger.debug(f"Available rooms: {availability_result['rooms']}")
        selected_room = _select_room(availability_result["rooms"], bungalow_type, package_type)
        if not selected_room:
            logger.warning(f"No suitable room available for {bungalow_type} - {package_type}")
            return {
                "success": False,
                "error": "No suitable room available",
                "customer_message": _get_no_availability_message(check_in_date, check_out_date)
            }
        
        logger.info(f"Selected room: {selected_room} for {bungalow_type} - {package_type}")
        
        # Internal Chain of Thought: Reserve payment just before booking (if applicable)
        if payment_method == "Depósito BAC" and transfer_id:
            logger.debug(f"Reserving bank transfer ID {transfer_id} for amount {payment_amount}")
            from .bank_transfer_tool import reserve_bank_transfer
            
            reservation_result = reserve_bank_transfer(int(transfer_id), payment_amount)
            if not reservation_result["success"]:
                logger.error(f"Bank transfer reservation failed: {reservation_result['message']}")
                return {
                    "success": False,
                    "error": f"Bank transfer reservation failed: {reservation_result['message']}",
                    "customer_message": "Hubo un error al procesar su pago. Por favor contacte a soporte."
                }
            logger.info(f"Bank transfer reservation successful for transfer ID {transfer_id}")
        
        elif payment_method == "CompraClick" and authorization_number:
            # CRITICAL: CompraClick payments must be validated BEFORE calling make_booking
            # The assistant should call sync_compraclick_payments -> validate_compraclick_payment first
            logger.info(f"Processing pre-validated CompraClick payment '{authorization_number}' for amount {payment_amount}")
            
            # Reserve the payment amount now that validation is complete
            from .compraclick_tool import reserve_compraclick_payment
            reservation_result = await reserve_compraclick_payment(authorization_number, payment_amount)
            
            if not reservation_result["success"]:
                # If reservation fails here, it means validation wasn't done properly
                logger.error(f"CompraClick payment reservation failed (validation required): {reservation_result['error']}")
                return {
                    "success": False,
                    "error": f"Payment validation required before booking: {reservation_result['error']}",
                    "customer_message": "Error en la validación del pago. El proceso debe incluir sincronización y validación antes de la reserva."
                }
            
            logger.info(f"CompraClick payment reservation successful for auth '{authorization_number}'")
        
        # Internal Chain of Thought: Enhanced booking with validation and retry logic
        logger.debug("Starting enhanced booking process with validation and retry")
        booking_result = await _make_booking_with_validation_and_retry(
            customer_name, email, phone_number, city, dui_passport, nationality,
            check_in_date, check_out_date, adults, children_0_5, children_6_10,
            bungalow_type, package_type, payment_method, payment_amount,
            payment_maker_name, selected_room, phone_number,  # wa_id = phone_number
            authorization_number, transfer_id, extra_beds, extra_beds_cost, customer_instructions
        )
        
        if not booking_result["success"]:
            logger.error(f"Enhanced booking process failed: {booking_result['error']}")
            return {
                "success": False,
                "error": booking_result["error"],
                "customer_message": booking_result.get("customer_message", "Hubo un error al procesar su reserva. Por favor contacte a soporte.")
            }
        
        logger.info(f"Booking successful, reserva: {booking_result['reserva']}")
        
        # Internal Chain of Thought: Update payment record in database
        try:
            logger.debug("Updating payment record in database")
            await _update_payment_record(
                payment_method, authorization_number, transfer_id, booking_result["reserva"]
            )
            logger.info("Payment record updated successfully")
        except Exception as e:
            logger.error(f"Payment record update failed: {e}")
            # Note: Don't fail the booking if database update fails
        
        # Customer Communication: Success message
        logger.info(f"Booking completed successfully for {customer_name}")
        return {
            "success": True,
            "reserva": booking_result["reserva"],
            "customer_message": f"""¡Excelente! Su reserva ha sido confirmada exitosamente. 🎉

Código de reserva: {booking_result['reserva']}

Los detalles de su reserva han sido enviados a su correo electrónico. Si tiene alguna pregunta o necesita asistencia adicional, no dude en contactarnos por este medio o llamándonos al 2505-2800.

¡Gracias por confiar en nosotros y por elegirnos para pasar momentos de calidad con su familia y amigos! Su confianza es muy importante para nosotros.

¡Esperamos verle pronto en Las Hojas Resort! 🌴"""
        }
        
    except Exception as e:
        logger.error(f"Unexpected error in make_booking: {e}")
        return {
            "success": False,
            "error": f"Unexpected error in booking process: {e}",
            "customer_message": "Hubo un error al procesar su reserva. Por favor contacte a soporte."
        }


def _calculate_booking_total(
    check_in_date: str, check_out_date: str, adults: int, children_0_5: int, 
    children_6_10: int, package_type: str
) -> dict:
    """
    Calculate the actual total booking cost using database rates.
    NOTE: Extra bed costs are NOT included - they are charged at reception.
    
    Args:
        check_in_date: Check-in date in YYYY-MM-DD format
        check_out_date: Check-out date in YYYY-MM-DD format
        adults: Number of adults
        children_0_5: Number of children 0-5 years old
        children_6_10: Number of children 6-10 years old
        package_type: Package type (Las Hojas, Escapadita, Pasadía, Romántico)
    
    Returns:
        dict: {
            "success": bool,
            "total_amount": float (excluding extra beds),
            "adult_rate": float,
            "child_rate": float,
            "breakdown": dict,
            "error": str (if failed)
        }
    """
    try:
        # Get pricing data for the check-in date
        pricing_data = get_price_for_date(check_in_date)
        if "error" in pricing_data:
            return {
                "success": False,
                "error": f"Could not get pricing data: {pricing_data['error']}"
            }
        
        # Map package type to pricing fields
        package_rate_map = {
            "Las Hojas": {"adult_field": "lh_adulto", "child_field": "lh_nino"},
            "Pasadía": {"adult_field": "pa_adulto", "child_field": "pa_nino"},
            "Escapadita": {"adult_field": "es_adulto", "child_field": "es_nino"},
            "Romántico": {"adult_field": "lh_adulto", "child_field": "lh_nino"}
        }
        
        if package_type not in package_rate_map:
            return {
                "success": False,
                "error": f"Unknown package type: {package_type}"
            }
        
        rate_fields = package_rate_map[package_type]
        adult_rate = float(pricing_data.get(rate_fields["adult_field"], 0))
        child_rate = float(pricing_data.get(rate_fields["child_field"], 0))
        
        if adult_rate == 0:
            return {
                "success": False,
                "error": f"No adult rate found for package {package_type} on {check_in_date}"
            }
        
        # Calculate nights (0 for Pasadía)
        check_in_dt = datetime.strptime(check_in_date, "%Y-%m-%d")
        check_out_dt = datetime.strptime(check_out_date, "%Y-%m-%d")
        nights = (check_out_dt - check_in_dt).days
        nights = max(1, nights)  # At least 1 day for Pasadía
        
        # Apply promotion logic before calculating totals
        adults_paying = adults  # Start with all adults paying
        promotion_details = {"type": "none", "adults_free": 0, "adults_paying": adults}
        
        # Apply 5x4 Pasadía promotion: For every 5 adults, 1 is free (max 2 free)
        if package_type == "Pasadía" and adults >= 5:
            potential_free = adults // 5  # How many could be free
            actual_free = min(potential_free, 2)  # Max 2 free
            adults_paying = adults - actual_free
            promotion_details = {
                "type": "5x4_pasadia",
                "adults_free": actual_free,
                "adults_paying": adults_paying,
                "note": f"Promoción 5x4: {actual_free} pase(s) gratis (máximo 2)"
            }
        
        # Calculate base totals with promotion applied
        adult_total = adults_paying * adult_rate * nights
        # Children 0-5 are FREE (rate = 0)
        children_0_5_total = 0.0  # Children 0-5 do not pay
        # Children 6-10 pay the child rate
        children_6_10_total = children_6_10 * child_rate * nights
        subtotal = adult_total + children_0_5_total + children_6_10_total
        
        # Apply single occupancy surcharge for accommodation packages (not Pasadía)
        single_occupancy_surcharge = 0.0
        if package_type != "Pasadía" and adults_paying == 1 and children_0_5 == 0 and children_6_10 == 0:
            single_occupancy_surcharge = 20.00 * nights  # $20 per night for single occupancy
            promotion_details["single_occupancy_surcharge"] = single_occupancy_surcharge
        
        total_amount = subtotal + single_occupancy_surcharge
        
        breakdown = {
            "adults": adults,
            "adults_paying": adults_paying,
            "children_0_5": children_0_5,
            "children_6_10": children_6_10,
            "nights": nights,
            "adult_rate_per_night": adult_rate,
            "child_rate_per_night": child_rate,
            "adult_total": adult_total,
            "children_0_5_total": children_0_5_total,
            "children_6_10_total": children_6_10_total,
            "subtotal_before_surcharges": subtotal,
            "single_occupancy_surcharge": single_occupancy_surcharge,
            "total_amount": total_amount,
            "promotion_applied": promotion_details,
            "note": "Extra bed costs excluded - charged at reception; promotions and surcharges applied"
        }
        
        logger.info(f"Calculated booking total for {package_type}: ${total_amount:.2f} (breakdown: {breakdown})")
        
        return {
            "success": True,
            "total_amount": total_amount,
            "adult_rate": adult_rate,
            "child_rate": child_rate,
            "breakdown": breakdown
        }
        
    except Exception as e:
        logger.error(f"Error calculating booking total: {e}")
        return {
            "success": False,
            "error": f"Calculation error: {str(e)}"
        }


def _is_booking_available(current_time: datetime) -> bool:
    """
    Internal Chain of Thought: Check if bookings are available at current time.
    NEVER expose this logic to customers.
    """
    # Check if it's a holiday
    current_date = current_time.date()
    if current_date in EL_SALVADOR_HOLIDAYS:
        return True  # Available all day on holidays
    
    # Check day of week and time
    weekday = current_time.weekday()  # 0 = Monday, 6 = Sunday
    hour = current_time.hour
    
    if weekday == 6:  # Sunday
        return True  # Available all day
    elif weekday == 5:  # Saturday
        return hour >= 13 or hour < 8  # 1:00 PM - 8:00 AM next day
    else:  # Monday-Friday
        return hour >= 17 or hour < 8  # 5:00 PM - 8:00 AM next day


def _validate_booking_info(
    customer_name: str, email: str, phone_number: str, city: str,
    dui_passport: str, nationality: str, check_in_date: str, check_out_date: str,
    adults: int, children_0_5: int, children_6_10: int, bungalow_type: str,
    package_type: str, payment_method: str, payment_amount: float, payment_maker_name: str
) -> dict:
    """
    Internal Chain of Thought: Validate all booking information with intelligent normalization.
    NEVER expose validation logic to customers.
    """
    
    # Intelligent normalization for bungalow_type before validation
    # This prevents unnecessary user confirmations for obvious variations
    original_bungalow_type = bungalow_type
    bungalow_type_lower = bungalow_type.lower().strip()
    
    # Normalize common variations to standard format
    if "bungalow familiar" in bungalow_type_lower or bungalow_type_lower == "familiar":
        bungalow_type = "Familiar"
    elif "bungalow junior" in bungalow_type_lower or bungalow_type_lower == "junior":
        bungalow_type = "Junior"
    elif "bungalow matrimonial" in bungalow_type_lower or bungalow_type_lower == "matrimonial":
        bungalow_type = "Matrimonial"
    elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower:
        bungalow_type = "Habitación"

    
    # Check required fields
    if not all([customer_name, email, phone_number, city, dui_passport, nationality,
               check_in_date, check_out_date, bungalow_type, package_type,
               payment_method, payment_maker_name]):
        return {
            "valid": False,
            "error": "Missing required booking information",
            "customer_message": "Falta información requerida para completar la reserva. Por favor proporcione todos los datos solicitados."
        }
    
    # Validate email format
    if "@" not in email or "." not in email:
        return {
            "valid": False,
            "error": "Invalid email format",
            "customer_message": "El formato del correo electrónico no es válido. Por favor proporcione un correo válido."
        }
    
    # Validate dates
    try:
        check_in = datetime.strptime(check_in_date, "%Y-%m-%d")
        check_out = datetime.strptime(check_out_date, "%Y-%m-%d")
        # Special case: Pasadía packages allow same-day check-in and check-out
        if package_type == "Pasadía":
            if check_out != check_in:
                return {
                    "valid": False,
                    "error": "Pasadía packages must have same check-in and check-out date",
                    "customer_message": "Para el paquete Pasadía, las fechas de entrada y salida deben ser el mismo día."
                }
        else:
            # For other packages, check-out must be after check-in
            if check_out <= check_in:
                return {
                    "valid": False,
                    "error": "Check-out date must be after check-in date",
                    "customer_message": "La fecha de salida debe ser posterior a la fecha de entrada."
                }
    except ValueError:
        return {
            "valid": False,
            "error": "Invalid date format",
            "customer_message": "El formato de las fechas no es válido. Use el formato YYYY-MM-DD."
        }
    
    # Validate and normalize bungalow type
    valid_bungalow_types = ["Familiar", "Junior", "Matrimonial", "Habitación"]
    
    # Special case: Pasadía is valid only when package_type is also Pasadía
    if package_type == "Pasadía" and bungalow_type == "Pasadía":
        # Pasadía is valid for day pass bookings
        pass
    else:
        # Intelligent normalization for common variations
        original_type = bungalow_type
        bungalow_type_lower = bungalow_type.lower().strip()
        
        # Handle "Bungalow X" variations → "X"
        if "bungalow familiar" in bungalow_type_lower or bungalow_type_lower == "familiar":
            bungalow_type = "Familiar"
        elif "bungalow junior" in bungalow_type_lower or bungalow_type_lower == "junior":
            bungalow_type = "Junior"
        elif "bungalow matrimonial" in bungalow_type_lower or bungalow_type_lower == "matrimonial":
            bungalow_type = "Matrimonial"
        elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower:
            bungalow_type = "Habitación"
        elif bungalow_type not in valid_bungalow_types:
            return {
                "valid": False,
                "error": f"Invalid bungalow type: {original_type}",
                "customer_message": "Tipo de alojamiento no válido. Por favor seleccione: Familiar, Junior, Matrimonial o Habitación."
            }
    
    # Validate package type
    valid_package_types = ["Las Hojas", "Escapadita", "Pasadía", "Romántico"]
    if package_type not in valid_package_types:
        return {
            "valid": False,
            "error": f"Invalid package type: {package_type}",
            "customer_message": "Tipo de paquete no válido. Por favor seleccione: Las Hojas, Escapadita, Pasadía o Romántico."
        }
    
    # Validate guest counts
    if adults < 1:
        return {
            "valid": False,
            "error": "At least one adult required",
            "customer_message": "Se requiere al menos un adulto para la reserva."
        }
    
    # Validate payment method
    valid_payment_methods = ["CompraClick", "Depósito BAC"]
    if payment_method not in valid_payment_methods:
        return {
            "valid": False,
            "error": f"Invalid payment method: {payment_method}",
            "customer_message": "Método de pago no válido."
        }
    
    return {"valid": True}


async def _check_room_availability(check_in_date: str, check_out_date: str) -> dict:
    """
    Internal Chain of Thought: Check room availability via external API.
    NEVER expose API details to customers.
    """
    try:
        url = "https://booking.lashojasresort.club/api/getRooms"
        params = {
            "checkIn": check_in_date,
            "checkOut": check_out_date
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params, timeout=300.0)
            response.raise_for_status()
            
            data = response.json()
            
            # DEBUG: Log available rooms for double-booking investigation
            logger.info(f"[ROOM_DEBUG] Available rooms for {check_in_date} to {check_out_date}: {data}")
            
            if "info" not in data:
                return {
                    "success": False,
                    "error": "Invalid response format from room availability API",
                    "customer_message": "Error al verificar disponibilidad de habitaciones."
                }
            
            return {
                "success": True,
                "rooms": data["info"]
            }
            
    except Exception as e:
        logger.error(f"Room availability check failed: {e}")
        return {
            "success": False,
            "error": f"Room availability check failed: {e}",
            "customer_message": "Error al verificar disponibilidad de habitaciones."
        }


def _select_room(available_rooms: dict, bungalow_type: str, package_type: str) -> Optional[str]:
    """
    Internal Chain of Thought: Select appropriate room based on type and availability.
    NEVER expose room selection logic to customers.
    """
    
    # DEBUG: Log room selection inputs
    logger.info(f"[ROOM_DEBUG] Room selection - Available rooms: {available_rooms}, Bungalow type: {bungalow_type}, Package type: {package_type}")
    
    # Use shared normalization function
    normalization_result = _normalize_bungalow_type(bungalow_type)
    
    if normalization_result["success"]:
        normalized_bungalow_type = normalization_result["type"]
        logger.info(f"[ROOM_DEBUG] Normalized '{bungalow_type}' to '{normalized_bungalow_type}'")
    else:
        # If normalization fails in room selection, use original input as fallback
        # This should be rare since room selection happens after booking type is determined
        normalized_bungalow_type = bungalow_type
        logger.warning(f"[ROOM_DEBUG] Normalization failed for '{bungalow_type}', using original input")
    
    # CRITICAL FIX: API response format is {"index": "room_number"}
    # We need to map room NUMBERS (values) to their indices (keys) for booking
    room_number_to_index = {}  # Maps actual room number to API index
    available_room_numbers = []  # List of actual room numbers available
    
    for room_index, room_number in available_rooms.items():
        try:
            # Parse room number (the VALUE in the API response)
            if room_number == "Pasadía":
                # Special case: Pasadía uses index directly
                room_number_to_index["Pasadía"] = room_index
                available_room_numbers.append("Pasadía")
            elif room_number.endswith('A'):
                # Room with 'A' suffix (like "10A")
                room_number_to_index[room_number] = room_index
                available_room_numbers.append(room_number)
            else:
                # Regular numeric room
                room_num = int(room_number)
                room_number_to_index[room_num] = room_index
                available_room_numbers.append(room_num)
        except ValueError:
            logger.warning(f"[ROOM_DEBUG] Could not parse room number: {room_number}")
            continue
    
    logger.info(f"[ROOM_DEBUG] Available room numbers: {available_room_numbers}")
    logger.info(f"[ROOM_DEBUG] Room number to index mapping: {room_number_to_index}")
    
    # Special case: Pasadía package can only use Pasadía
    if package_type == "Pasadía":
        if "Pasadía" in available_room_numbers:
            selected_index = room_number_to_index["Pasadía"]
            logger.info(f"[ROOM_DEBUG] Selected Pasadía (index {selected_index}) for Pasadía package")
            return str(selected_index)
        else:
            logger.info(f"[ROOM_DEBUG] Pasadía not available for Pasadía package")
            return None
    
    # Filter rooms based on normalized bungalow type using ACTUAL room numbers
    suitable_room_numbers = []
    
    if normalized_bungalow_type == "Familiar":
        # Bungalow Familiar: rooms 1-17
        suitable_room_numbers = [r for r in available_room_numbers if isinstance(r, int) and 1 <= r <= 17]
        logger.info(f"[ROOM_DEBUG] Familiar bungalow - suitable room numbers: {suitable_room_numbers}")
    elif normalized_bungalow_type == "Junior":
        # Bungalow Junior: rooms 18-59, avoid Matrimonial rooms if possible
        matrimonial_rooms = {22, 42, 47, 48, 53}
        regular_junior = [r for r in available_room_numbers if isinstance(r, int) and 18 <= r <= 59 and r not in matrimonial_rooms]
        if regular_junior:
            suitable_room_numbers = regular_junior
            logger.info(f"[ROOM_DEBUG] Junior bungalow - using regular junior room numbers: {suitable_room_numbers}")
        else:
            # If no regular junior rooms, allow Matrimonial rooms
            suitable_room_numbers = [r for r in available_room_numbers if isinstance(r, int) and 18 <= r <= 59]
            logger.info(f"[ROOM_DEBUG] Junior bungalow - falling back to matrimonial room numbers: {suitable_room_numbers}")
    elif normalized_bungalow_type == "Matrimonial":
        # Bungalow Matrimonial: ONLY rooms 22, 42, 47, 48, 53
        matrimonial_rooms = {22, 42, 47, 48, 53}
        suitable_room_numbers = [r for r in available_room_numbers if isinstance(r, int) and r in matrimonial_rooms]
        logger.info(f"[ROOM_DEBUG] Matrimonial bungalow - suitable room numbers: {suitable_room_numbers}")
    elif normalized_bungalow_type == "Habitación":
        # Habitación: rooms with 'A' suffix (1A-14A)
        suitable_room_numbers = []
        for room_name in available_room_numbers:
            if isinstance(room_name, str) and room_name.endswith('A'):
                try:
                    # Extract number from room name like "10A"
                    num = int(room_name[:-1])
                    if 1 <= num <= 14:
                        suitable_room_numbers.append(room_name)
                except ValueError:
                    continue
        logger.info(f"[ROOM_DEBUG] Habitación - suitable room numbers: {suitable_room_numbers}")
    
    # Randomly select from suitable rooms and return the actual room number
    if suitable_room_numbers:
        selected_room_number = random.choice(suitable_room_numbers)
        selected_index = room_number_to_index[selected_room_number]
        logger.info(f"[ROOM_DEBUG] Final selected room number: {selected_room_number} (API index: {selected_index}) from suitable rooms: {suitable_room_numbers}")
        return str(selected_room_number)
    
    logger.info(f"[ROOM_DEBUG] No suitable rooms found for {normalized_bungalow_type} {package_type}")
    return None


async def _make_booking_with_validation_and_retry(
    customer_name: str, email: str, phone_number: str, city: str,
    dui_passport: str, nationality: str, check_in_date: str, check_out_date: str,
    adults: int, children_0_5: int, children_6_10: int, bungalow_type: str,
    package_type: str, payment_method: str, payment_amount: float,
    payment_maker_name: str, selected_room: str, wa_id: str,
    authorization_number: str = None, transfer_id: str = None, 
    extra_beds: int = 0, extra_beds_cost: float = 0.0,
    customer_instructions: str = None
) -> dict:
    """
    Enhanced booking process with validation and retry logic to prevent double-booking.
    
    This function implements:
    1. Re-validation: Checks room availability immediately before booking
    2. Retry Logic: If booking fails due to unavailability, retries with different room
    
    NEVER expose internal logic to customers.
    """
    
    max_retries = 3  # Maximum number of retry attempts
    attempt = 0
    
    while attempt < max_retries:
        attempt += 1
        logger.info(f"[ROOM_DEBUG] Booking attempt {attempt}/{max_retries} for room {selected_room}")
        
        # Step 1: Re-validate room availability immediately before booking
        logger.debug(f"[ROOM_DEBUG] Re-validating availability for room {selected_room}")
        revalidation_result = await _check_room_availability(check_in_date, check_out_date)
        
        if not revalidation_result["success"]:
            logger.error(f"[ROOM_DEBUG] Re-validation failed: {revalidation_result['error']}")
            return {
                "success": False,
                "error": f"Room availability re-validation failed: {revalidation_result['error']}",
                "customer_message": "Error al verificar disponibilidad de habitaciones."
            }
        
        # Check if our selected room is still available
        current_available_rooms = revalidation_result["rooms"]
        if selected_room not in current_available_rooms:
            logger.warning(f"[ROOM_DEBUG] Room {selected_room} no longer available. Available rooms: {list(current_available_rooms.keys())}")
            
            # Try to select a different room of the same type
            new_selected_room = _select_room(current_available_rooms, bungalow_type, package_type)
            if new_selected_room:
                logger.info(f"[ROOM_DEBUG] Selected alternative room {new_selected_room} (attempt {attempt})")
                selected_room = new_selected_room
            else:
                logger.warning(f"[ROOM_DEBUG] No alternative rooms available for {bungalow_type} - {package_type}")
                if attempt == max_retries:
                    return {
                        "success": False,
                        "error": "No suitable rooms available after retries",
                        "customer_message": _get_no_availability_message(check_in_date, check_out_date)
                    }
                continue  # Try again on next iteration
        else:
            logger.info(f"[ROOM_DEBUG] Room {selected_room} confirmed available for booking")
        
        # Step 2: Attempt the booking with the validated room
        logger.debug(f"[ROOM_DEBUG] Making booking API call for room {selected_room}")
        try:
            booking_result = await _make_booking_api_call(
                customer_name, email, phone_number, city, dui_passport, nationality,
                check_in_date, check_out_date, adults, children_0_5, children_6_10,
                bungalow_type, package_type, payment_method, payment_amount,
                payment_maker_name, selected_room, wa_id, authorization_number, transfer_id,
                extra_beds, extra_beds_cost, customer_instructions
            )
        except Exception as e:
            logger.error(f"[ROOM_DEBUG] Booking API call failed with unexpected exception: {e}", exc_info=True)
            return {
                "success": False,
                "error": f"Unexpected API call exception: {e}",
                "customer_message": "Hubo un error inesperado al comunicarse con el sistema de reservas. Por favor, intente más tarde."
            }
        
        if booking_result.get("reserva"):
            logger.info(f"[ROOM_DEBUG] Booking successful for room {selected_room} on attempt {attempt}")
            return booking_result
        
        # Step 3: Handle booking failure
        error_msg = booking_result.get("error", "").lower()
        
        # Check if failure is due to room unavailability/double-booking
        if any(keyword in error_msg for keyword in ["unavailable", "occupied", "reserved", "not available", "already booked"]):
            logger.warning(f"[ROOM_DEBUG] Booking failed due to room unavailability: {booking_result['error']}")
            
            if attempt < max_retries:
                logger.info(f"[ROOM_DEBUG] Retrying with different room (attempt {attempt + 1}/{max_retries})")
                # Force selection of a different room by removing current one from available list
                filtered_rooms = {k: v for k, v in current_available_rooms.items() if k != selected_room}
                new_selected_room = _select_room(filtered_rooms, bungalow_type, package_type)
                
                if new_selected_room:
                    selected_room = new_selected_room
                    continue  # Retry with new room immediately
                else:
                    logger.warning(f"[ROOM_DEBUG] No more alternative rooms available")
                    break
            else:
                logger.error(f"[ROOM_DEBUG] Max retries reached, booking failed")
                break
        else:
            # Non-availability related error - implement 5-minute retry delay
            if attempt < max_retries:
                delay_minutes = 5
                logger.warning(f"[ROOM_DEBUG] Booking failed with error: {booking_result['error']}")
                logger.info(f"[ROOM_DEBUG] Waiting {delay_minutes} minutes before retry attempt {attempt + 1}/{max_retries}")
                await asyncio.sleep(delay_minutes * 60)  # Wait 5 minutes
                continue  # Retry the entire booking process
            else:
                logger.error(f"[ROOM_DEBUG] Booking failed with non-availability error after all retries: {booking_result['error']}")
                return booking_result
    
    # If we reach here, all attempts failed
    logger.error(f"[ROOM_DEBUG] Booking failed after {max_retries} attempts")
    return {
        "success": False,
        "error": f"Booking failed after {max_retries} validation and retry attempts",
        "customer_message": "¡Gracias por su pago! Hemos validado correctamente su comprobante, pero ocurrió un inconveniente técnico temporal al procesar la reserva. Su pago está seguro y registrado. Un ejecutivo se comunicará con usted a la brevedad para completar su confirmación. 🌴"
    }


async def _make_booking_api_call(
    customer_name: str, email: str, phone_number: str, city: str,
    dui_passport: str, nationality: str, check_in_date: str, check_out_date: str,
    adults: int, children_0_5: int, children_6_10: int, bungalow_type: str,
    package_type: str, payment_method: str, payment_amount: float,
    payment_maker_name: str, selected_room: str, wa_id: str,
    authorization_number: str = None, transfer_id: str = None, 
    extra_beds: int = 0, extra_beds_cost: float = 0.0,
    customer_instructions: str = None
) -> dict:
    """
    Internal Chain of Thought: Make booking API call with proper data mapping.
    NEVER expose API details to customers.
    """
    
    # Internal Chain of Thought: Parse customer name
    name_parts = customer_name.strip().split()
    if len(name_parts) >= 2:
        firstname = name_parts[0]
        lastname = " ".join(name_parts[1:])
    else:
        firstname = customer_name
        lastname = ""
    
    # Internal Chain of Thought: Determine title
    titulo = "Sra." if any(indicator in firstname.lower() for indicator in ["maria", "ana", "rosa", "carmen"]) else "Sr."
    
    # Internal Chain of Thought: Format dates for API
    check_in_formatted = datetime.strptime(check_in_date, "%Y-%m-%d").strftime("%m/%d/%Y")
    check_out_formatted = datetime.strptime(check_out_date, "%Y-%m-%d").strftime("%m/%d/%Y")
    
    # Internal Chain of Thought: Calculate actual booking total and rates from database
    check_in_dt = datetime.strptime(check_in_date, "%Y-%m-%d")
    check_out_dt = datetime.strptime(check_out_date, "%Y-%m-%d")
    nights = (check_out_dt - check_in_dt).days
    
    # Calculate actual booking total using database rates (excluding extra beds)
    booking_total_result = _calculate_booking_total(
        check_in_date, check_out_date, adults, children_0_5, children_6_10,
        package_type
    )
    
    if not booking_total_result.get("success"):
        logger.error(f"Failed to calculate booking total: {booking_total_result.get('error')}")
        # Fallback to simplified calculation if database fails
        if nights == 0:  # Pasadía same-day booking
            adult_rate = payment_amount / adults if adults > 0 else 0
            booking_total = payment_amount
        else:
            adult_rate = payment_amount / (adults * nights) if adults > 0 else 0
            booking_total = payment_amount
        child_rate = adult_rate * 0.5
    else:
        adult_rate = booking_total_result["adult_rate"]
        child_rate = booking_total_result["child_rate"]
        booking_total = booking_total_result["total_amount"]
    
    # CRITICAL FIX: Get accommodation description with normalization and assistant fallback
    async def _get_accommodation_description(bungalow_type: str, wa_id: str) -> str:
        """
        Get accommodation description with intelligent normalization and assistant classification fallback.
        This prevents the null acomodacion constraint violation.
        """
        # Standard accommodation descriptions
        accommodation_map = {
            "Familiar": "Bungalow Familiar: 2 cuartos, 2 baños, sala y terraza para hamacas.",
            "Junior": "Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas.",
            "Matrimonial": "Bungalow Matrimonial: 1 ambiente, 1 cama matrimonial, 1 baño, terraza para hamacas.",
            "Habitación": "Habitación: 1 ambiente, 2 camas, 1 baño.",
            "Pasadía": "Pasadía: Acceso a todas las instalaciones del resort por el día."
        }
        
        # Step 1: Try normalization first
        normalization_result = _normalize_bungalow_type(bungalow_type)
        
        if normalization_result["success"]:
            normalized_type = normalization_result["type"]
            logger.info(f"[ACCOMMODATION_DEBUG] Normalized '{bungalow_type}' to '{normalized_type}'")
            
            accommodation_description = accommodation_map.get(normalized_type)
            if accommodation_description:
                return accommodation_description
        
        # Step 2: Normalization failed - ask assistant for classification
        logger.warning(f"[ACCOMMODATION_DEBUG] Normalization failed for '{bungalow_type}', requesting assistant classification")
        
        try:
            # Get full conversation context
            full_conversation = await _get_full_conversation_context(wa_id)
            
            # Ask assistant to classify based on conversation context
            classified_type = await _ask_assistant_for_accommodation_type(
                original_input=bungalow_type,
                wa_id=wa_id,
                full_conversation=full_conversation
            )
            
            # Get description for classified type
            accommodation_description = accommodation_map.get(classified_type)
            if accommodation_description:
                logger.info(f"[ACCOMMODATION_DEBUG] Assistant classified '{bungalow_type}' as '{classified_type}'")
                return accommodation_description
        
        except Exception as e:
            logger.error(f"[ACCOMMODATION_DEBUG] Assistant classification failed: {e}")
        
        # Step 3: Final fallback - use descriptive text to prevent null constraint
        fallback_description = f"Accommodation type: {bungalow_type}"
        logger.warning(f"[ACCOMMODATION_DEBUG] Using fallback description: '{fallback_description}'")
        return fallback_description
    
    # Internal Chain of Thought: Map accommodation description (kept for reference)
    accommodation_map = {
        "Familiar": "Bungalow Familiar: 2 cuartos, 2 baños, sala y terraza para hamacas.",
        "Junior": "Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas.",
        "Matrimonial": "Bungalow Matrimonial: 1 ambiente, 1 cama matrimonial, 1 baño, terraza para hamacas.",
        "Habitación": "Habitación: 1 ambiente, 2 camas, 1 baño.",
        "Pasadía": "Pasadía: Acceso a todas las instalaciones del resort por el día."
    }
    
    # Internal Chain of Thought: Map service type
    service_map = {
        "Las Hojas": "Paquete Las Hojas",
        "Escapadita": "Paquete Escapadita",
        "Pasadía": "Pasadía",
        "Romántico": "Paquete Romántico"
    }
    
    # Internal Chain of Thought: Map payment method
    payway = "Tarjeta de crédito" if payment_method == "CompraClick" else "Depósito a cuenta BAC"
    
    # Internal Chain of Thought: Prepare API payload
    # Special case: For Pasadía bookings, reserverooms must be "1-Pasadía"
    if package_type == "Pasadía":
        reserverooms_value = "1-Pasadía"
    else:
        reserverooms_value = f"1-{selected_room}"
    
    # Internal Chain of Thought: Build dynamic commenthotel content
    comment_parts = ["Reserva realizada por Valeria Mendoza"]
    
    # Add extra bed information if applicable
    if extra_beds > 0:
        if extra_beds_cost > 0:
            comment_parts.append(f"Cama extra agregada (${extra_beds_cost:.2f} a cobrar en recepción)")
        else:
            comment_parts.append(f"Cama extra agregada (sin costo por grupo > 4 personas)")
    
    # Add customer instructions if provided
    if customer_instructions and customer_instructions.strip():
        comment_parts.append(f"Instrucciones especiales: {customer_instructions.strip()}")
    
    # Join all parts with " | " separator
    commenthotel_content = " | ".join(comment_parts)
    
    payload = {
        "titulo": titulo,
        "firstname": firstname,
        "lastname": lastname,
        "commenthotel": commenthotel_content,
        "phone": phone_number.replace("+503", ""),  # Remove country code
        "reserverooms": reserverooms_value,
        "ciudad": city,
        "checkIn": check_in_formatted,
        "checkOut": check_out_formatted,
        "acomodacion": await _get_accommodation_description(bungalow_type, wa_id),
        "adultcount": str(adults),
        "childcount": str(children_0_5),
        "childcount1": str(children_6_10),
        "payway": payway,
        "loadamount": f"{payment_amount:.2f}",  # Amount actually paid
        "email": email,
        "dui": dui_passport,
        "national": nationality,
        "adultrate": f"{adult_rate:.2f}",
        "childrate": f"{child_rate:.2f}",
        "cardusername": payment_maker_name,
        "reseramount": f"{booking_total:.2f}",  # Total booking cost (may differ from payment)
        "cardnumer": "0",
        "duedate": "0",
        "comment": commenthotel_content,
        "compraclick": authorization_number if payment_method == "CompraClick" else None,
        "username": "VM",
        "cancel_flag": "no",
        "service": service_map.get(package_type, "")
    }
    
    # Internal Chain of Thought: Make API call
    logger.info(f"[BOOKING_API] Making booking API call for customer {firstname} {lastname} (wa_id: {wa_id})")
    logger.info(f"[BOOKING_API] Booking details: {check_in_date} to {check_out_date}, {adults}+{children_0_5}+{children_6_10}, {bungalow_type}, {package_type}")
    logger.info(f"[BOOKING_API] Payment: {payment_method} ${payment_amount:.2f} (booking total: ${booking_total:.2f})")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://booking.lashojasresort.club/api/addBookingUserRest",
                data=payload,
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=300
            )
            
            logger.info(f"[BOOKING_API] Response status: {response.status_code}")
            
            if response.status_code != 200:
                # Enhanced error logging with full context
                error_details = {
                    "status_code": response.status_code,
                    "response_text": response.text[:500],  # Limit to avoid log spam
                    "customer": f"{firstname} {lastname}",
                    "wa_id": wa_id,
                    "booking_dates": f"{check_in_date} to {check_out_date}",
                    "accommodation": f"{bungalow_type} - {package_type}",
                    "payment_method": payment_method,
                    "payment_amount": payment_amount,
                    "booking_total": booking_total,
                    "selected_room": selected_room,
                    "payload_summary": {
                        "phone": payload.get("phone"),
                        "email": payload.get("email"),
                        "reserverooms": payload.get("reserverooms"),
                        "acomodacion": payload.get("acomodacion"),
                        "service": payload.get("service"),
                        "payway": payload.get("payway")
                    }
                }
                logger.error(f"[BOOKING_API_ERROR] Booking API call failed: {error_details}")
                
                return {
                    "success": False,
                    "error": f"Booking API returned status {response.status_code}: {response.text[:200]}"  # Truncate for user
                }
            
            # Parse response to extract reservation ID with enhanced error handling
            try:
                content_type = response.headers.get("content-type", "")
                logger.info(f"[BOOKING_API] Response content-type: {content_type}")
                logger.info(f"[BOOKING_API] Response text (first 200 chars): {response.text[:200]}")
                
                if content_type.startswith("application/json"):
                    response_data = response.json()
                else:
                    logger.warning(f"[BOOKING_API] Non-JSON response, attempting to parse anyway")
                    try:
                        response_data = response.json()
                    except:
                        # Fallback: try to extract reserva from text response
                        response_text = response.text
                        if "reserva" in response_text.lower():
                            # Try to extract reservation number from text
                            import re
                            match = re.search(r'"?reserva"?\s*:\s*"?(\w+)"?', response_text, re.IGNORECASE)
                            if match:
                                reserva = match.group(1)
                                logger.info(f"[BOOKING_API] Extracted reserva from text: {reserva}")
                                return {
                                    "success": True,
                                    "reserva": reserva,
                                    "response": {"reserva": reserva, "raw_text": response_text[:500]}
                                }
                        
                        logger.warning(f"[BOOKING_API] Could not parse response as JSON, treating as success with unknown reserva")
                        return {
                            "success": True,
                            "reserva": "unknown",
                            "response": {"raw_text": response_text[:500]}
                        }
                
                reserva = response_data.get("reserva", "unknown")
                logger.info(f"[BOOKING_API] Successfully parsed JSON, reserva: {reserva}")
                
                return {
                    "success": True,
                    "reserva": reserva,
                    "response": response_data
                }
                
            except Exception as parse_error:
                logger.error(f"[BOOKING_API] Response parsing failed but API returned 200: {parse_error}")
                logger.info(f"[BOOKING_API] Treating as successful booking with parsing error")
                # Since API returned 200, treat as success despite parsing error
                return {
                    "success": True,
                    "reserva": "unknown",
                    "response": {"parse_error": str(parse_error), "raw_text": response.text[:500]}
                }
            
    except Exception as e:
        error_msg = str(e) if str(e).strip() else f"Unknown error (type: {type(e).__name__})"
        logger.error(f"Booking API call failed: {error_msg}", exc_info=True)
        return {
            "success": False,
            "error": f"Booking API call failed: {error_msg}"
        }


async def _update_payment_record(
    payment_method: str, authorization_number: str, transfer_id: str, reserva: str
) -> None:
    """
    Internal Chain of Thought: Update payment record with booking reference.
    NEVER expose database operations to customers.
    """
    
    conn = get_db_connection()
    if not conn:
        raise Exception("Database connection failed")
    
    try:
        cursor = conn.cursor()
        codreser = f"HR{reserva}"
        dateused = datetime.now(EL_SALVADOR_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        if payment_method == "CompraClick" and authorization_number:
            # Update CompraClick payment record
            query = """
                UPDATE compraclick 
                SET codreser = %s, dateused = %s
                WHERE autorizacion = %s
            """
            cursor.execute(query, (codreser, dateused, authorization_number))
        elif payment_method == "Depósito BAC" and transfer_id:
            # Update bank transfer payment record
            query = """
                UPDATE bac 
                SET codreser = %s, dateused = %s
                WHERE id = %s
            """
            cursor.execute(query, (codreser, dateused, transfer_id))
        
        conn.commit()
        logger.info(f"Payment record updated: {payment_method}, reserva: {reserva}")
        
    except Exception as e:
        logger.error(f"Failed to update payment record: {e}")
        raise
    finally:
        if conn and conn.is_connected():
            conn.close()


def _get_no_availability_message(check_in_date: str, check_out_date: str) -> str:
    """
    Internal Chain of Thought: Generate no availability message.
    NEVER expose this logic to customers.
    """
    return f"""En relación a su reciente solicitud de reservación para el día {check_in_date} al {check_out_date}.

Primero, queremos confirmarle que hemos recibido la notificación de su pago. Sin embargo, lamentamos informarle que, durante el lapso en el que se le informó de la disponibilidad y el que nos enviara su comprobante de pago, el bungalow que seleccionó fue confirmado por otro cliente a través de una transacción que se completó instantes antes. Esto sucede en raras ocasiones debido a que nuestro sistema asigna la disponibilidad en tiempo real al primer pago que se verifica con éxito.

Entendemos perfectamente que esta no es la noticia que esperaba y le ofrecemos una sincera disculpa por el inconveniente. Queremos solucionarlo para usted de inmediato.

Le presentamos dos opciones:

Buscar una nueva fecha: Como primera solución, nos encantaría ayudarle a encontrar fechas alternativas para su estadía. Por favor, indíquenos si le gustaría que revisemos la disponibilidad en otras fechas que le convengan.

Reembolso completo: Si un cambio de fecha no se ajusta a sus planes, procederemos con la devolución íntegra de su dinero. Iniciaremos el proceso a la brevedad posible para asegurar que reciba el reembolso cuanto antes.

Por favor, háganos saber qué opción prefiere para poder gestionarla de inmediato.

Quedamos a su entera disposición."""


# Payment Safety Check Functions

async def _is_payment_already_used(payment_method: str, authorization_number: str = None, transfer_id: str = None) -> bool:
    """
    Check if a payment has already been used for a booking by checking codereser column.
    
    Args:
        payment_method: "CompraClick" or "Depósito BAC"
        authorization_number: CompraClick authorization number (if CompraClick)
        transfer_id: Bank transfer ID (if bank transfer)
        
    Returns:
        True if payment already has a booking reference (codereser is not empty)
        False if payment is unused (codereser is NULL/empty)
    """
    conn = get_db_connection()
    if not conn:
        logger.error("Database connection failed for payment usage check")
        return True  # Err on the side of caution - assume used
    
    try:
        cursor = conn.cursor()
        
        if payment_method == "CompraClick" and authorization_number:
            # Check CompraClick table
            query = "SELECT codereser FROM compraclick WHERE auth = %s"
            cursor.execute(query, (authorization_number,))
            result = cursor.fetchone()
            
            if result:
                codereser = result[0]
                is_used = codereser is not None and codereser.strip() != ""
                logger.info(f"CompraClick payment {authorization_number} usage check: codereser='{codereser}', is_used={is_used}")
                return is_used
            else:
                logger.warning(f"CompraClick payment {authorization_number} not found in database")
                return True  # Payment not found - assume used
                
        elif payment_method == "Depósito BAC" and transfer_id:
            # Check BAC table
            query = "SELECT codereser FROM bac WHERE id = %s"
            cursor.execute(query, (transfer_id,))
            result = cursor.fetchone()
            
            if result:
                codereser = result[0]
                is_used = codereser is not None and codereser.strip() != ""
                logger.info(f"Bank transfer {transfer_id} usage check: codereser='{codereser}', is_used={is_used}")
                return is_used
            else:
                logger.warning(f"Bank transfer {transfer_id} not found in database")
                return True  # Payment not found - assume used
        
        logger.warning(f"Invalid payment method or missing identifiers: {payment_method}, auth={authorization_number}, transfer_id={transfer_id}")
        return True  # Invalid parameters - assume used
        
    except Exception as e:
        logger.error(f"Failed to check payment usage: {e}")
        return True  # Error - assume used for safety
    finally:
        if conn and conn.is_connected():
            conn.close()


def _is_explicit_booking_confirmation(message: str) -> bool:
    """
    Check if message contains explicit booking confirmation intent.
    Only accepts very clear confirmation commands, not common Spanish words.
    
    Args:
        message: Customer message to analyze
        
    Returns:
        True if message contains explicit booking confirmation
        False if message is just a regular conversation
    """
    message_lower = message.lower().strip()
    
    # Explicit booking confirmation phrases
    explicit_confirmations = [
        "sí, proceda con la reserva",
        "si, proceda con la reserva",
        "proceda con la reserva",
        "confirmo la reserva",
        "si confirmo la reserva",
        "sí confirmo la reserva",
        "por favor haga la reserva",
        "por favor proceda",
        "haga la reserva",
        "realice la reserva",
        "procesar la reserva",
        "continúe con la reserva",
        "continue con la reserva",
        "adelante con la reserva",
        "confirmar reserva",
        "confirmar booking",
        "proceder con booking",
        "proceder con la reservación",
        "sí, reserve",
        "si, reserve",
        "reserve por favor"
    ]
    
    # Check for exact matches or phrases within the message
    for confirmation in explicit_confirmations:
        if confirmation in message_lower:
            logger.info(f"Explicit booking confirmation detected: '{confirmation}' in message: '{message_lower}'")
            return True
    
    # Log that no explicit confirmation was found
    logger.info(f"No explicit booking confirmation found in message: '{message_lower}'")
    return False


# PENDING Booking Management Functions

PENDING_BOOKINGS_FILE = "/tmp/pending_bookings.json"

async def _store_pending_booking(phone_number: str, booking_data: Dict[str, Any]) -> None:
    """
    Store PENDING booking data with timestamp for later processing.
    """
    try:
        # Load existing pending bookings
        if os.path.exists(PENDING_BOOKINGS_FILE):
            with open(PENDING_BOOKINGS_FILE, 'r') as f:
                pending_bookings = json.load(f)
        else:
            pending_bookings = {}
        
        # Store the booking data
        pending_bookings[phone_number] = booking_data
        
        # Save back to file
        with open(PENDING_BOOKINGS_FILE, 'w') as f:
            json.dump(pending_bookings, f, indent=2)
        
        # Mark conversation as PENDING
        await update_chat_status(phone_number, "PENDING")
        logger.info(f"Stored PENDING booking for {phone_number} and marked conversation as PENDING")
        
    except Exception as e:
        logger.error(f"Failed to store PENDING booking for {phone_number}: {e}")
        raise

async def _get_pending_booking(phone_number: str) -> Optional[Dict[str, Any]]:
    """
    Retrieve PENDING booking data for a phone number.
    """
    try:
        if not os.path.exists(PENDING_BOOKINGS_FILE):
            return None
        
        with open(PENDING_BOOKINGS_FILE, 'r') as f:
            pending_bookings = json.load(f)
        
        return pending_bookings.get(phone_number)
        
    except Exception as e:
        logger.error(f"Failed to retrieve PENDING booking for {phone_number}: {e}")
        return None

async def _remove_pending_booking(phone_number: str) -> None:
    """
    Remove PENDING booking data after processing.
    """
    try:
        if not os.path.exists(PENDING_BOOKINGS_FILE):
            return
        
        with open(PENDING_BOOKINGS_FILE, 'r') as f:
            pending_bookings = json.load(f)
        
        if phone_number in pending_bookings:
            del pending_bookings[phone_number]
            
            with open(PENDING_BOOKINGS_FILE, 'w') as f:
                json.dump(pending_bookings, f, indent=2)
            
            logger.info(f"Removed PENDING booking for {phone_number}")
        
    except Exception as e:
        logger.error(f"Failed to remove PENDING booking for {phone_number}: {e}")

# REMOVED: _should_process_pending_booking function
# This function was DANGEROUS as it auto-booked based on time elapsed.
# Safety-first approach: Only process bookings with explicit customer confirmation.

async def process_pending_booking_if_needed(phone_number: str, message: str) -> Optional[Dict[str, Any]]:
    """
    SAFE VERSION: Check if there's a PENDING booking that should be processed.
    
    CRITICAL SAFETY REQUIREMENTS:
    1. Only processes booking with EXPLICIT customer confirmation (not common words)
    2. Verifies payment has NOT already been used for another booking
    3. NO automatic processing based on time elapsed
    4. Keeps conversation PENDING for human agent if payment already used
    
    Returns:
        - None if no PENDING booking needs processing or safety checks fail
        - Booking result dict if a PENDING booking was safely processed
    """
    try:
        pending_booking = await _get_pending_booking(phone_number)
        if not pending_booking:
            return None
        
        logger.info(f"[BOOKING_SAFETY] Checking PENDING booking for {phone_number} with message: '{message}'")
        
        # SAFETY CHECK 1: Require EXPLICIT booking confirmation (not common words)
        is_explicit_confirmation = _is_explicit_booking_confirmation(message)
        if not is_explicit_confirmation:
            logger.info(f"[BOOKING_SAFETY] No explicit booking confirmation detected for {phone_number}. Keeping PENDING.")
            return None
        
        # SAFETY CHECK 2: Verify payment has not already been used
        booking_data = pending_booking.copy()
        payment_method = booking_data.get('payment_method')
        authorization_number = booking_data.get('authorization_number')
        transfer_id = booking_data.get('transfer_id')
        
        if payment_method:
            payment_already_used = await _is_payment_already_used(
                payment_method=payment_method,
                authorization_number=authorization_number,
                transfer_id=transfer_id
            )
            
            if payment_already_used:
                logger.warning(f"[BOOKING_SAFETY] Payment already used for {phone_number}. Keeping PENDING for human agent.")
                # Send message to customer explaining situation
                await send_wati_message(
                    phone_number,
                    "Su pago ya ha sido utilizado para una reserva anterior. Un agente humano revisará su caso para asistirle adecuadamente."
                )
                return None
        
        # SAFETY CHECK 3: Log successful safety validation
        logger.info(f"[BOOKING_SAFETY] All safety checks passed for {phone_number}. Processing PENDING booking.")
        
        # Remove the timestamp before processing
        booking_data.pop('timestamp', None)
        
        # Process the booking safely with force_process=True (safety checks already passed)
        result = await make_booking(force_process=True, **booking_data)
        
        # Remove the pending booking data
        await _remove_pending_booking(phone_number)
        
        return result
        
    except Exception as e:
        logger.error(f"[BOOKING_SAFETY] Failed to safely process PENDING booking for {phone_number}: {e}")
        return None
