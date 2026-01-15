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
import threading
import time
from datetime import datetime, timedelta
from pytz import timezone
import holidays
from typing import Dict, List, Optional, Any
from .database_client import get_db_connection, get_price_for_date, execute_with_retry
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

# Room capacity constraints - ALIGNED WITH system_instructions_new.txt (line 1300-1302)
# Format: {"min_occupancy": X, "max_occupancy": Y}
# Occupancy formula: adults + (children_6_10 * 0.5). Children 0-5 = 0
# NOTE: "Doble" is normalized to "Habitación" by _normalize_bungalow_type(), so no separate entry needed
ROOM_CAPACITY = {
    "Familiar": {"min_occupancy": 5, "max_occupancy": 8},    # Rooms 1-17
    "Junior": {"min_occupancy": 2, "max_occupancy": 8},      # Rooms 18-59
    "Habitación": {"min_occupancy": 2, "max_occupancy": 4},  # Rooms 1A-14A (also called "Doble")
    "Matrimonial": {"min_occupancy": 2, "max_occupancy": 2}, # Junior subset: 22,42,47,48,53
    "Pasadía": {"min_occupancy": 0, "max_occupancy": 999}    # No room capacity for day pass
}

# Booking lock to prevent duplicate bookings from concurrent requests
_booking_locks = {}  # Per-customer locks
_booking_locks_mutex = threading.Lock()  # Protects the locks dictionary
_used_authorizations = {}  # Track recently used authorization codes {auth_code: (wa_id, timestamp)}
_used_authorizations_mutex = threading.Lock()
AUTHORIZATION_COOLDOWN_SECONDS = 300  # 5 minutes - prevent same auth code reuse

def _get_booking_lock(wa_id: str) -> threading.Lock:
    """Get or create a lock for a specific customer."""
    with _booking_locks_mutex:
        if wa_id not in _booking_locks:
            _booking_locks[wa_id] = threading.Lock()
        return _booking_locks[wa_id]

def _check_authorization_duplicate(auth_code: str, wa_id: str) -> tuple:
    """
    Check if authorization code was recently used (does NOT mark as used).
    Returns (is_duplicate, message).
    """
    if not auth_code:
        return False, ""
    
    current_time = time.time()
    auth_key = auth_code.strip().upper()
    
    with _used_authorizations_mutex:
        # Clean old entries (older than cooldown)
        expired = [k for k, v in _used_authorizations.items() 
                   if current_time - v[1] > AUTHORIZATION_COOLDOWN_SECONDS]
        for k in expired:
            del _used_authorizations[k]
        
        # Check if this auth code was recently used
        if auth_key in _used_authorizations:
            prev_wa_id, prev_time = _used_authorizations[auth_key]
            age_seconds = current_time - prev_time
            logger.warning(f"[DUPLICATE_BOOKING_PREVENTION] Authorization {auth_code} was used {age_seconds:.0f}s ago for {prev_wa_id}")
            return True, f"Authorization code {auth_code} was already used for a booking"
        
        return False, ""


def _mark_authorization_used(auth_code: str, wa_id: str) -> None:
    """
    Mark authorization code as used AFTER successful booking.
    Should only be called after booking is confirmed successful.
    """
    if not auth_code:
        return
    
    auth_key = auth_code.strip().upper()
    current_time = time.time()
    
    with _used_authorizations_mutex:
        _used_authorizations[auth_key] = (wa_id, current_time)
        logger.info(f"[DUPLICATE_BOOKING_PREVENTION] Marked authorization {auth_code} as used for {wa_id}")

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
    elif any(keyword in bungalow_type_lower for keyword in ['habitacion', 'habitación', 'room', 'hab', 'doble']):
        return {"success": True, "type": "Habitación"}
    elif any(keyword in bungalow_type_lower for keyword in ['pasadia', 'pasadía', 'day']):
        return {"success": True, "type": "Pasadía"}
    else:
        # Normalization failed - return original input for assistant classification
        return {"success": False, "original_input": bungalow_type}


def _validate_room_capacity(
    bungalow_type: str,
    adults: int,
    children_0_5: int,
    children_6_10: int
) -> Dict[str, Any]:
    """
    Validate that guest count meets room capacity constraints.
    
    Uses existing _normalize_bungalow_type() for robustness.
    
    Occupancy formula (from system_instructions_new.txt line 1300):
    occupancy_score = (adults × 1) + (children_6_10 × 0.5)
    NOTE: Children 0-5 count as 0 (not 0.5)!
    
    Args:
        bungalow_type: Type of room (will be normalized)
        adults: Number of adults (count as 1 each)
        children_0_5: Children 0-5 years (count as 0 - don't affect occupancy)
        children_6_10: Children 6-10 years (count as 0.5 each)
    
    Returns:
        {
            "valid": True/False,
            "occupancy_score": float,
            "min_occupancy": int,
            "max_occupancy": int,
            "error": "..." if invalid,
            "suggestion": "..." room type suggestion if invalid
        }
    """
    # Use existing normalization function for robustness
    norm_result = _normalize_bungalow_type(bungalow_type)
    if norm_result["success"]:
        normalized_type = norm_result["type"]
    else:
        normalized_type = bungalow_type  # Fallback to original
    
    capacity = ROOM_CAPACITY.get(normalized_type)
    if not capacity:
        return {"valid": False, "error": f"Unknown room type: {bungalow_type}"}
    
    # Calculate occupancy score - children_0_5 count as 0!
    occupancy_score = adults + (children_6_10 * 0.5)
    
    # Check MINIMUM occupancy (too few people)
    if occupancy_score < capacity["min_occupancy"]:
        suggestion = _suggest_room_for_group(adults, children_0_5, children_6_10)
        return {
            "valid": False,
            "occupancy_score": occupancy_score,
            "min_occupancy": capacity["min_occupancy"],
            "max_occupancy": capacity["max_occupancy"],
            "error": f"{bungalow_type} requires min {capacity['min_occupancy']} occupancy, got {occupancy_score}",
            "suggestion": suggestion
        }
    
    # Check MAXIMUM occupancy (too many people)
    if occupancy_score > capacity["max_occupancy"]:
        suggestion = _suggest_room_for_group(adults, children_0_5, children_6_10)
        return {
            "valid": False,
            "occupancy_score": occupancy_score,
            "min_occupancy": capacity["min_occupancy"],
            "max_occupancy": capacity["max_occupancy"],
            "error": f"{bungalow_type} max occupancy {capacity['max_occupancy']}, got {occupancy_score}",
            "suggestion": suggestion
        }
    
    return {
        "valid": True,
        "occupancy_score": occupancy_score,
        "min_occupancy": capacity["min_occupancy"],
        "max_occupancy": capacity["max_occupancy"]
    }


def _suggest_room_for_group(adults: int, children_0_5: int, children_6_10: int) -> str:
    """
    Suggest appropriate room type(s) for group size.
    Uses same occupancy formula: adults + (children_6_10 * 0.5)
    """
    occupancy = adults + (children_6_10 * 0.5)
    
    if occupancy <= 2:
        return "Junior, Habitación, o Matrimonial"
    elif occupancy <= 4:
        return "Junior o Habitación"
    elif occupancy <= 8:
        if occupancy >= 5:
            return "Familiar o Junior"  # Both valid for 5-8
        else:
            return "Junior"  # 4.5 or less, only Junior valid
    else:
        # Need multiple rooms (occupancy > 8)
        rooms_needed = -(-int(occupancy) // 8)  # Ceiling division by max Junior capacity
        return f"Se necesitan {rooms_needed} habitaciones para {int(occupancy)} personas"


async def _get_multiple_rooms(
    check_in_date: str,
    check_out_date: str,
    room_requests: List[Dict[str, Any]],
    package_type: str = "Las Hojas"
) -> Dict[str, Any]:
    """
    Get available rooms for a multi-room booking request.
    
    IMPORTANT: Uses ONE API call to get ALL available rooms, then selects
    from that pool. This is efficient and matches how _check_room_availability() works.
    
    Args:
        check_in_date: Check-in date (YYYY-MM-DD)
        check_out_date: Check-out date (YYYY-MM-DD)
        room_requests: List of room requests, each with:
            - bungalow_type: Type of room
            - adults: Number of adults
            - children_0_5: Children 0-5 years
            - children_6_10: Children 6-10 years
        package_type: Package type for all rooms (Las Hojas, Escapadita, Romántico)
    
    Returns:
        {
            "success": True/False,
            "rooms": ["24", "25", "26", "27"],  # Selected room numbers
            "room_details": [
                {"room": "24", "type": "Junior", "adults": 2, ...},
                ...
            ],
            "error": "..." if failed
        }
    """
    
    # STEP 1: Validate capacity for ALL rooms FIRST (no API call needed)
    for i, request in enumerate(room_requests):
        bungalow_type = request["bungalow_type"]
        adults = request["adults"]
        children_0_5 = request.get("children_0_5", 0)
        children_6_10 = request.get("children_6_10", 0)
        
        # _validate_room_capacity already uses _normalize_bungalow_type internally (Phase 0)
        capacity_result = _validate_room_capacity(bungalow_type, adults, children_0_5, children_6_10)
        if not capacity_result["valid"]:
            return {
                "success": False,
                "error": f"Room {i+1} ({bungalow_type}): {capacity_result['error']}",
                "suggestion": capacity_result.get("suggestion"),
                "customer_message": f"La habitación {i+1} ({bungalow_type}) no tiene capacidad para {adults} adultos. {capacity_result.get('suggestion', '')}"
            }
    
    # STEP 2: Get ALL available rooms with ONE API call
    # The API returns ALL rooms: {"info": {"1": "24", "2": "25", "3": "1", ...}}
    availability = await _check_room_availability(check_in_date, check_out_date)
    
    if not availability.get("success"):
        return {
            "success": False,
            "error": "Failed to check room availability",
            "customer_message": "Error al verificar disponibilidad de habitaciones."
        }
    
    all_available_rooms = availability["rooms"]  # {"1": "24", "2": "25", ...}
    logger.info(f"[MULTI_ROOM] All available rooms: {all_available_rooms}")
    
    # STEP 3: Select rooms for each request from the pool
    selected_rooms = []
    room_details = []
    excluded = []  # Track already-selected rooms to prevent duplicates
    
    for i, request in enumerate(room_requests):
        bungalow_type = request["bungalow_type"]
        
        # Normalize bungalow_type for _select_room (handles "bungalow familiar" -> "Familiar", etc.)
        norm_result = _normalize_bungalow_type(bungalow_type)
        normalized_type = norm_result["type"] if norm_result["success"] else bungalow_type
        
        # Select a room of this type (excluding already-selected ones)
        selected = _select_room(
            all_available_rooms, 
            normalized_type,
            package_type,
            excluded_rooms=excluded
        )
        
        if not selected:
            # 🚨 FALLBACK: Calculate single-room and alternative multi-room options
            total_adults = sum(r.get("adults", 0) for r in room_requests)
            total_children_6_10 = sum(r.get("children_6_10", 0) for r in room_requests)
            total_occupancy = total_adults + (total_children_6_10 * 0.5)
            
            capacity_rules = {
                'bungalow_familiar': (5, 8),
                'bungalow_junior': (2, 8),
                'habitacion': (2, 4),
            }
            type_display_names = {
                'bungalow_familiar': 'Bungalow Familiar',
                'bungalow_junior': 'Bungalow Junior',
                'habitacion': 'Habitación Doble',
            }
            
            # Count available rooms by type
            room_counts = {'bungalow_familiar': 0, 'bungalow_junior': 0, 'habitacion': 0}
            for room_idx, room_num in all_available_rooms.items():
                if room_num == "Pasadía":
                    continue
                elif isinstance(room_num, str) and room_num.endswith('A'):
                    room_counts['habitacion'] += 1
                else:
                    try:
                        num = int(room_num)
                        if 1 <= num <= 17:
                            room_counts['bungalow_familiar'] += 1
                        elif 18 <= num <= 59:
                            room_counts['bungalow_junior'] += 1
                    except (ValueError, TypeError):
                        pass
            
            # Check single-room alternatives (if group fits in one room)
            compatible_single_room = []
            for room_type, (min_cap, max_cap) in capacity_rules.items():
                if room_counts.get(room_type, 0) > 0 and min_cap <= total_occupancy <= max_cap:
                    compatible_single_room.append(type_display_names.get(room_type, room_type))
            
            # Check alternative multi-room options
            compatible_multi_room = []
            rooms_needed = len(room_requests)
            for room_type, (min_cap, max_cap) in capacity_rules.items():
                available_count = room_counts.get(room_type, 0)
                if available_count >= rooms_needed:
                    per_room = total_occupancy / rooms_needed
                    if min_cap <= per_room <= max_cap:
                        compatible_multi_room.append(f"{rooms_needed}x {type_display_names.get(room_type, room_type)}")
            
            logger.info(f"[MULTI_ROOM] Not enough {bungalow_type}. Single-room options: {compatible_single_room}, Multi-room options: {compatible_multi_room}")
            
            # Build instruction based on alternatives
            all_alternatives = compatible_single_room + compatible_multi_room
            if compatible_single_room:
                instruction = f"No hay suficientes {bungalow_type} disponibles. Sin embargo, su grupo de {total_adults} adultos{' y ' + str(total_children_6_10) + ' niños' if total_children_6_10 > 0 else ''} SÍ cabe en UNA SOLA habitación: {', '.join(compatible_single_room)}. El precio se recalculará. ¿Desea cambiar o buscar otras fechas?"
            elif compatible_multi_room:
                instruction = f"No hay suficientes {bungalow_type} disponibles. Alternativas: {', '.join(compatible_multi_room)}. ¿Desea cambiar o buscar otras fechas?"
            else:
                instruction = f"🚨 No hay suficientes {bungalow_type} disponibles y NO HAY ALTERNATIVAS compatibles. DEBES ofrecer: 1) Buscar otras fechas, o 2) Reembolso completo."
            
            return {
                "success": False,
                "error": f"No available {bungalow_type} rooms (need {len(room_requests) - i} more)",
                "partial_rooms": selected_rooms,
                "customer_message": f"No hay suficientes habitaciones {bungalow_type} disponibles.",
                "compatible_single_room": compatible_single_room,
                "compatible_multi_room": compatible_multi_room,
                "total_occupancy": total_occupancy,
                "group_size": {"adults": total_adults, "children_6_10": total_children_6_10},
                "assistant_instruction": instruction
            }
        
        selected_rooms.append(selected)
        excluded.append(selected)
        room_details.append({
            "room": selected,
            "type": bungalow_type,
            "adults": request["adults"],
            "children_0_5": request.get("children_0_5", 0),
            "children_6_10": request.get("children_6_10", 0)
        })
        
        logger.info(f"[MULTI_ROOM] Room {i+1}: Selected {selected} ({bungalow_type})")
    
    return {
        "success": True,
        "rooms": selected_rooms,
        "room_details": room_details
    }


async def _revalidate_multi_room_availability(
    check_in_date: str,
    check_out_date: str,
    selected_rooms: List[str],
    room_bookings: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Re-validate that selected rooms are still available immediately before booking.
    This prevents race conditions between room selection and API call.
    
    Similar to _make_booking_with_revalidation() logic for single bookings.
    
    Args:
        check_in_date: Check-in date (YYYY-MM-DD)
        check_out_date: Check-out date (YYYY-MM-DD)
        selected_rooms: Previously selected room numbers ["24", "25", "26"]
        room_bookings: Original room requests for reference
    
    Returns:
        {
            "success": True/False,
            "unavailable_rooms": ["24"] if any became unavailable,
            "error": "..." if failed
        }
    """
    unavailable_rooms = []
    
    # Get current availability
    availability_result = await _check_room_availability(check_in_date, check_out_date)
    
    if not availability_result["success"]:
        return {
            "success": False,
            "error": "Failed to check current availability",
            "unavailable_rooms": selected_rooms
        }
    
    # Check if all selected rooms are still available
    # .values() returns room numbers like "24", "25", "1A", etc.
    current_available = set(str(v) for v in availability_result["rooms"].values())
    
    for room in selected_rooms:
        # Convert to string for consistent comparison
        room_str = str(room)
        if room_str not in current_available:
            unavailable_rooms.append(room)
    
    if unavailable_rooms:
        logger.warning(f"[MULTI_ROOM] Rooms became unavailable: {unavailable_rooms}")
        return {
            "success": False,
            "error": f"Rooms {unavailable_rooms} no longer available",
            "unavailable_rooms": unavailable_rooms
        }
    
    return {
        "success": True,
        "unavailable_rooms": []
    }


async def make_multi_room_booking(
    customer_name: str,
    email: str,
    phone_number: str,
    city: str,
    dui_passport: str,
    nationality: str,
    check_in_date: str,
    check_out_date: str,
    room_bookings: List[Dict[str, Any]],
    package_type: str,
    payment_method: str,
    payment_amount: float,
    payment_maker_name: str,
    wa_id: str,
    authorization_number: str = None,
    transfer_id: str = None,
    extra_beds: int = 0,
    extra_beds_cost: float = 0.0,
    customer_instructions: str = None
) -> dict:
    """
    Process a multi-room booking using native API multi-room support.
    
    The API accepts:
    - reserverooms: "24+25+26+27" (rooms joined by +)
    - adultcount: "2+3+2+4" (PAX per room joined by +)
    
    This is a SINGLE atomic transaction - all rooms succeed or all fail.
    
    NOTE: Time validation is handled by assistant via check_office_status()
    BEFORE calling this function (same as single booking).
    
    Args:
        customer_name: Full name of primary guest
        email: Customer email
        phone_number: Customer phone (or extracted from wa_id)
        city: City of residence
        dui_passport: DUI or passport number
        nationality: Customer nationality
        check_in_date: Check-in date (YYYY-MM-DD)
        check_out_date: Check-out date (YYYY-MM-DD)
        room_bookings: List of room configurations, each with:
            - bungalow_type: "Junior", "Familiar", "Matrimonial", "Habitación"
            - adults: Number of adults
            - children_0_5: Children 0-5 years
            - children_6_10: Children 6-10 years
        package_type: Las Hojas, Escapadita, Romántico
        payment_method: CompraClick or Depósito BAC
        payment_amount: Total payment amount
        payment_maker_name: Name on payment
        wa_id: WhatsApp ID or subscriber ID
        authorization_number: CompraClick auth code
        transfer_id: Bank transfer ID
        extra_beds: Number of extra beds
        extra_beds_cost: Cost for extra beds
        customer_instructions: Special instructions
    
    Returns:
        {
            "success": True/False,
            "reserva": "HR12345" or list of codes,
            "rooms_booked": ["24", "25", "26", "27"],
            "total_rooms": 4,
            "customer_message": "...",
            "error": "..." if failed
        }
    """
    logger.info(f"[MULTI_ROOM] Starting multi-room booking for {customer_name}, {len(room_bookings)} rooms")
    
    # 🚨 CRITICAL: Block CompraClick bookings without authorization_number to prevent duplicates
    if payment_method == "CompraClick" and not authorization_number:
        logger.error(f"[MULTI_ROOM] BLOCKED: CompraClick booking without authorization_number for {wa_id}")
        return {
            "success": False,
            "error": "authorization_number parameter is required for CompraClick payments"
        }
    
    # DUPLICATE BOOKING PREVENTION: Check if this payment reference was already used (don't mark yet)
    payment_ref = authorization_number or transfer_id
    if payment_ref:
        is_duplicate, dup_message = _check_authorization_duplicate(payment_ref, wa_id)
        if is_duplicate:
            logger.warning(f"[DUPLICATE_BOOKING_PREVENTION] Blocking duplicate multi-room booking for {wa_id} with payment ref {payment_ref}")
            return {
                "success": True,
                "already_booked": True,
                "customer_message": "Esta reserva ya fue procesada anteriormente. Si necesita verificar su reservación, por favor indíquenos."
            }
    
    # Extract phone number from wa_id if not provided (same as make_booking)
    if not phone_number or phone_number.strip() == '':
        phone_number = _extract_phone_from_wa_id(wa_id)
        logger.info(f"[MULTI_ROOM] Phone number extracted from wa_id {wa_id}: {phone_number}")
    
    try:
        # NOTE: Time validation is handled by assistant via check_office_status()
        # before calling this function. This matches the pattern in make_booking().
        current_time = datetime.now(EL_SALVADOR_TZ)
        logger.debug(f"[MULTI_ROOM] Current El Salvador time: {current_time}")
        
        # Step 0: Validate customer info (including placeholder detection)
        placeholder_values = [
            "pendiente", "por_definir", "por definir", "tbd", "auto", 
            "n/a", "na", "ninguno", "none", "unknown", "desconocido",
            "huésped", "huesped", "cliente", "usuario", "guest"
        ]
        def _is_placeholder(value: str) -> bool:
            if not value:
                return True
            return value.strip().lower() in placeholder_values
        
        # Check for missing or placeholder values
        missing_fields = []
        if not customer_name or len(customer_name.strip().split()) < 2 or _is_placeholder(customer_name):
            missing_fields.append("nombre completo (nombre y apellido)")
        if not email or "@" not in email or _is_placeholder(email):
            missing_fields.append("correo electrónico válido")
        if not city or _is_placeholder(city):
            missing_fields.append("ciudad de residencia")
        if not dui_passport or _is_placeholder(dui_passport):
            missing_fields.append("número de DUI o pasaporte")
        if not nationality or _is_placeholder(nationality):
            missing_fields.append("nacionalidad")
        if not payment_maker_name or _is_placeholder(payment_maker_name):
            missing_fields.append("nombre del titular del pago")
        
        if missing_fields:
            missing_list = ", ".join(missing_fields)
            logger.warning(f"[MULTI_ROOM] Missing required fields (incl. placeholders): {missing_list}")
            return {
                "success": False,
                "error": "missing_customer_data",
                "missing_fields": missing_fields,
                "assistant_action": "ASK_CUSTOMER_FOR_MISSING_DATA_NATURALLY",
                "customer_message": f"Para completar su reserva, necesitamos los siguientes datos: {missing_list}. ¿Podría proporcionárnoslos?"
            }
        
        # Validate dates
        try:
            check_in = datetime.strptime(check_in_date, "%Y-%m-%d")
            check_out = datetime.strptime(check_out_date, "%Y-%m-%d")
            if check_out <= check_in:
                return {
                    "success": False,
                    "error": "Check-out date must be after check-in date",
                    "customer_message": "La fecha de salida debe ser posterior a la fecha de entrada."
                }
        except ValueError:
            return {
                "success": False,
                "error": "Invalid date format",
                "customer_message": "El formato de las fechas no es válido. Use el formato YYYY-MM-DD."
            }
        
        # Step 1: Get available rooms for each request (includes capacity validation)
        room_result = await _get_multiple_rooms(
            check_in_date, check_out_date, room_bookings, package_type
        )
        
        if not room_result["success"]:
            return {
                "success": False,
                "error": room_result["error"],
                "customer_message": f"Lo sentimos, no hay suficientes habitaciones disponibles: {room_result['error']}"
            }
        
        selected_rooms = room_result["rooms"]
        room_details = room_result["room_details"]
        
        # Step 2: Reserve payment (full amount for all rooms)
        if payment_method == "Depósito BAC" and transfer_id:
            from .bank_transfer_tool import reserve_bank_transfer
            reserve_result = reserve_bank_transfer(int(transfer_id), payment_amount)
            if not reserve_result.get("success"):
                return {
                    "success": False,
                    "error": "Payment reservation failed",
                    "customer_message": reserve_result.get("message", "Error al reservar el pago.")
                }
            logger.info(f"[MULTI_ROOM] Bank transfer reservation successful for transfer ID {transfer_id}")
        elif payment_method == "CompraClick" and authorization_number:
            from .compraclick_tool import reserve_compraclick_payment
            reserve_result = await reserve_compraclick_payment(authorization_number, payment_amount)
            if not reserve_result.get("success"):
                return {
                    "success": False,
                    "error": "CompraClick reservation failed",
                    "customer_message": "Error al procesar el pago CompraClick."
                }
            logger.info(f"[MULTI_ROOM] CompraClick reservation successful for auth '{authorization_number}'")
        
        # Step 3: RE-VALIDATE availability immediately before booking
        revalidation_result = await _revalidate_multi_room_availability(
            check_in_date, check_out_date, selected_rooms, room_bookings
        )
        
        if not revalidation_result["success"]:
            logger.warning(f"[MULTI_ROOM] Re-validation failed: {revalidation_result['error']}")
            retry_result = await _get_multiple_rooms(check_in_date, check_out_date, room_bookings, package_type)
            if not retry_result["success"]:
                return {
                    "success": False,
                    "error": "Rooms became unavailable during booking",
                    "customer_message": "Lo sentimos, algunas habitaciones ya no están disponibles."
                }
            selected_rooms = retry_result["rooms"]
            room_details = retry_result["room_details"]
        
        # Step 4: Format API parameters
        reserverooms = "+".join(str(r) for r in selected_rooms)
        adult_counts = [str(r["adults"]) for r in room_details]
        adultcount = "+".join(adult_counts)
        total_children_0_5 = sum(r["children_0_5"] for r in room_details)
        total_children_6_10 = sum(r["children_6_10"] for r in room_details)
        
        # Step 5: Build accommodation description for mixed-type bookings
        room_type_counts = {}
        for r in room_details:
            t = r["type"]
            room_type_counts[t] = room_type_counts.get(t, 0) + 1
        
        # Format: "2x Junior, 1x Familiar" or just "Junior" if all same type
        if len(room_type_counts) == 1:
            primary_bungalow_type = list(room_type_counts.keys())[0]
        else:
            primary_bungalow_type = ", ".join(f"{count}x {t}" for t, count in room_type_counts.items())
        
        # Step 6: Make API call
        booking_result = await _make_multi_room_api_call(
            customer_name=customer_name,
            email=email,
            phone_number=phone_number,
            city=city,
            dui_passport=dui_passport,
            nationality=nationality,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            reserverooms=reserverooms,
            adultcount=adultcount,
            children_0_5=total_children_0_5,
            children_6_10=total_children_6_10,
            bungalow_type=primary_bungalow_type,
            package_type=package_type,
            payment_method=payment_method,
            payment_amount=payment_amount,
            payment_maker_name=payment_maker_name,
            wa_id=wa_id,
            authorization_number=authorization_number,
            transfer_id=transfer_id,
            extra_beds=extra_beds,
            extra_beds_cost=extra_beds_cost,
            customer_instructions=customer_instructions,
            room_count=len(selected_rooms)
        )
        
        if not booking_result["success"]:
            return {
                "success": False,
                "error": booking_result.get("error"),
                "customer_message": f"Error al procesar la reserva: {booking_result.get('error', 'Error desconocido')}"
            }
        
        # Step 7: Update payment record
        reserva = booking_result.get("reserva", "unknown")
        try:
            await _update_payment_record(payment_method, authorization_number, transfer_id, reserva)
            logger.info(f"[MULTI_ROOM] Payment record updated for reserva {reserva}")
        except Exception as e:
            logger.error(f"[MULTI_ROOM] Payment record update failed: {e}")
        
        # Step 8: Success response
        total_adults = sum(r["adults"] for r in room_details)
        total_children = total_children_0_5 + total_children_6_10
        
        logger.info(f"[MULTI_ROOM] Booking successful, reserva: {reserva}")
        
        # DUPLICATE BOOKING PREVENTION: Mark auth as used ONLY after successful booking
        if payment_ref:
            _mark_authorization_used(payment_ref, wa_id)
        
        return {
            "success": True,
            "reserva": reserva,
            "rooms_booked": selected_rooms,
            "total_rooms": len(selected_rooms),
            "customer_message": f"""¡Excelente! Su reserva de {len(selected_rooms)} habitaciones ha sido confirmada exitosamente. 🎉

📋 **Detalles de la Reserva:**
• Código: {reserva}
• Habitaciones: {', '.join(str(r) for r in selected_rooms)}
• Check-in: {check_in_date}
• Check-out: {check_out_date}
• Huéspedes: {total_adults} adultos{f', {total_children} niños' if total_children > 0 else ''}
• Paquete: {package_type}
• Total pagado: ${payment_amount:.2f}

Los detalles han sido enviados a su correo electrónico. ¡Esperamos verle pronto en Las Hojas Resort! 🌴"""
        }
    
    except Exception as e:
        logger.error(f"[MULTI_ROOM] Unexpected error: {e}")
        return {
            "success": False,
            "error": f"Unexpected error: {e}",
            "customer_message": "Hubo un error al procesar su reserva. Por favor contacte a soporte."
        }


async def _get_openai_thread_id(wa_id: str) -> Optional[str]:
    """
    Get OpenAI thread ID for a customer with infinite retry.
    This function should be implemented based on your database schema.
    """
    def _execute_query():
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            query = "SELECT thread_id FROM customer_threads WHERE wa_id = %s ORDER BY created_at DESC LIMIT 1"
            cursor.execute(query, (wa_id,))
            result = cursor.fetchone()
            return result[0] if result else None
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    return execute_with_retry(_execute_query, f"_get_openai_thread_id({wa_id})")

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
    
    # 🚨 CRITICAL: Block CompraClick bookings without authorization_number to prevent duplicates
    if payment_method == "CompraClick" and not authorization_number:
        logger.error(f"[PAYMENT_VALIDATION] BLOCKED: CompraClick booking without authorization_number for {wa_id}")
        return {
            "success": False,
            "error": "authorization_number parameter is required for CompraClick payments"
        }
    
    # DUPLICATE BOOKING PREVENTION: Check if this payment reference was already used (don't mark yet)
    payment_ref = authorization_number or transfer_id
    if payment_ref:
        is_duplicate, dup_message = _check_authorization_duplicate(payment_ref, wa_id)
        if is_duplicate:
            logger.warning(f"[DUPLICATE_BOOKING_PREVENTION] Blocking duplicate booking for {wa_id} with payment ref {payment_ref}")
            return {
                "success": True,  # Return success to prevent retry loops
                "already_booked": True,
                "customer_message": "Esta reserva ya fue procesada anteriormente. Si necesita verificar su reservación, por favor indíquenos."
            }
    
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
            
            # 🚨 OCCUPANCY-BASED FILTERING: Find single-room AND multi-room alternatives
            compatible_single_room = []
            compatible_multi_room = []
            available_rooms = availability_result.get("rooms", {})
            
            # Determine what room types are actually available and count them
            available_types = set()
            room_counts = {'bungalow_familiar': 0, 'bungalow_junior': 0, 'habitacion': 0}
            for room_idx, room_num in available_rooms.items():
                if room_num == "Pasadía":
                    continue
                elif isinstance(room_num, str) and room_num.endswith('A'):
                    available_types.add('habitacion')
                    room_counts['habitacion'] += 1
                else:
                    try:
                        num = int(room_num)
                        if 1 <= num <= 17:
                            available_types.add('bungalow_familiar')
                            room_counts['bungalow_familiar'] += 1
                        elif 18 <= num <= 59:
                            available_types.add('bungalow_junior')
                            room_counts['bungalow_junior'] += 1
                    except (ValueError, TypeError):
                        pass
            
            # Calculate occupancy
            total_occupancy = adults + (children_6_10 * 0.5)
            capacity_rules = {
                'bungalow_familiar': (5, 8),
                'bungalow_junior': (2, 8),
                'habitacion': (2, 4),
            }
            type_display_names = {
                'bungalow_familiar': 'Bungalow Familiar',
                'bungalow_junior': 'Bungalow Junior',
                'habitacion': 'Habitación Doble',
            }
            
            # Check single-room alternatives
            for avail_type in available_types:
                if avail_type in capacity_rules:
                    min_cap, max_cap = capacity_rules[avail_type]
                    if min_cap <= total_occupancy <= max_cap:
                        compatible_single_room.append(type_display_names.get(avail_type, avail_type))
            
            # 🚨 MULTI-ROOM ALTERNATIVES: If no single-room fits, check multi-room options
            if not compatible_single_room and total_occupancy > 0:
                for room_type, (min_cap, max_cap) in capacity_rules.items():
                    available_count = room_counts.get(room_type, 0)
                    if available_count >= 2:
                        for num_rooms_needed in range(2, min(available_count + 1, 5)):
                            per_room = total_occupancy / num_rooms_needed
                            if min_cap <= per_room <= max_cap:
                                display_name = type_display_names.get(room_type, room_type)
                                compatible_multi_room.append(f"{num_rooms_needed}x {display_name} ({available_count} disponibles)")
                                logger.info(f"[ROOM_DEBUG] Multi-room compatible: {num_rooms_needed}x {room_type} (per-room {per_room:.1f})")
                                break
            
            logger.info(f"[ROOM_DEBUG] Available types: {available_types}, Occupancy: {total_occupancy}, Single-room: {compatible_single_room}, Multi-room: {compatible_multi_room}")
            
            # Build specific instruction based on available alternatives
            all_alternatives = compatible_single_room + compatible_multi_room
            if all_alternatives:
                if compatible_multi_room and not compatible_single_room:
                    instruction = f"El tipo {bungalow_type} no está disponible. Su grupo de {adults} adultos{' y ' + str(children_6_10) + ' niños' if children_6_10 > 0 else ''} NO cabe en una sola habitación, pero SÍ pueden usar MÚLTIPLES habitaciones: {', '.join(compatible_multi_room)}. El precio se calcula por persona. ¿Desea esta opción o buscar otras fechas?"
                else:
                    instruction = f"El tipo {bungalow_type} no está disponible. Alternativas para {adults} adultos{' y ' + str(children_6_10) + ' niños' if children_6_10 > 0 else ''}: {', '.join(all_alternatives)}. El precio puede variar. ¿Desea cambiar o buscar otras fechas?"
            else:
                instruction = f"🚨 El tipo {bungalow_type} no está disponible y NO HAY ALTERNATIVAS (ni single ni multi-room) para su grupo de {adults} adultos{' y ' + str(children_6_10) + ' niños' if children_6_10 > 0 else ''}. DEBES ofrecer: 1) Buscar otras fechas con check_smart_availability, o 2) Reembolso completo."
            
            return {
                "success": False,
                "error": "No suitable room available",
                "customer_message": _get_no_availability_message(check_in_date, check_out_date),
                "available_types": list(available_types),
                "compatible_single_room": compatible_single_room,
                "compatible_multi_room": compatible_multi_room,
                "requested_type": bungalow_type,
                "group_size": {"adults": adults, "children_6_10": children_6_10},
                "has_compatible_alternatives": len(all_alternatives) > 0,
                "assistant_instruction": instruction
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
        
        # DUPLICATE BOOKING PREVENTION: Mark auth as used ONLY after successful booking
        if payment_ref:
            _mark_authorization_used(payment_ref, wa_id)
        
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
        
        # Calculate nights
        check_in_dt = datetime.strptime(check_in_date, "%Y-%m-%d")
        check_out_dt = datetime.strptime(check_out_date, "%Y-%m-%d")
        nights = (check_out_dt - check_in_dt).days
        nights = max(1, nights)  # At least 1 day for Pasadía
        
        # Get pricing for EACH night and calculate average rates
        # For a stay from 01/12 to 01/14, we need rates for nights 01/12 and 01/13
        adult_rates_sum = 0.0
        child_rates_sum = 0.0
        rates_per_night = []  # For breakdown logging
        
        for night_offset in range(nights):
            current_night = check_in_dt + timedelta(days=night_offset)
            current_night_str = current_night.strftime("%Y-%m-%d")
            
            pricing_data = get_price_for_date(current_night_str)
            if "error" in pricing_data:
                return {
                    "success": False,
                    "error": f"Could not get pricing data for {current_night_str}: {pricing_data['error']}"
                }
            
            night_adult_rate = float(pricing_data.get(rate_fields["adult_field"], 0))
            night_child_rate = float(pricing_data.get(rate_fields["child_field"], 0))
            
            if night_adult_rate == 0:
                return {
                    "success": False,
                    "error": f"No adult rate found for package {package_type} on {current_night_str}"
                }
            
            adult_rates_sum += night_adult_rate
            child_rates_sum += night_child_rate
            rates_per_night.append({
                "date": current_night_str,
                "adult_rate": night_adult_rate,
                "child_rate": night_child_rate
            })
        
        # Calculate average rates across all nights
        adult_rate = adult_rates_sum / nights
        child_rate = child_rates_sum / nights
        
        logger.info(f"[RATE_CALC] Multi-night average rates for {nights} nights: adult=${adult_rate:.2f}, child=${child_rate:.2f} (per-night: {rates_per_night})")
        
        # Apply promotion logic before calculating totals
        adults_paying = adults  # Start with all adults paying
        promotion_details = {"type": "none", "adults_free": 0, "adults_paying": adults}
        
        # Apply 5x4 Pasadía promotion: For every 5 adults, 1 is free
        # CRITICAL: Only applies when adult rate is ABOVE $24.00
        # RULES: Groups < 20: max 2 free | Groups >= 20: unlimited free
        if package_type == "Pasadía" and adults >= 5 and adult_rate > 24.00:
            potential_free = adults // 5  # How many could be free
            # Apply cap based on group size
            if adults < 20:
                actual_free = min(potential_free, 2)  # Max 2 free for groups < 20
                cap_note = "(máximo 2)"
            else:
                actual_free = potential_free  # Unlimited for groups >= 20
                cap_note = "(sin límite para grupos grandes)"
            adults_paying = adults - actual_free
            promotion_details = {
                "type": "5x4_pasadia",
                "adults_free": actual_free,
                "adults_paying": adults_paying,
                "note": f"Promoción 5x4: {actual_free} pase(s) gratis {cap_note}"
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
            "rates_per_night": rates_per_night,
            "adult_total": adult_total,
            "children_0_5_total": children_0_5_total,
            "children_6_10_total": children_6_10_total,
            "subtotal_before_surcharges": subtotal,
            "single_occupancy_surcharge": single_occupancy_surcharge,
            "total_amount": total_amount,
            "promotion_applied": promotion_details,
            "note": "Extra bed costs excluded - charged at reception; rates are averages across all nights"
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
    elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower or "doble" in bungalow_type_lower:
        bungalow_type = "Habitación"

    
    # Helper to detect placeholder values that assistant should NOT use
    def _is_placeholder(value: str) -> bool:
        """Check if a value is a placeholder that should be rejected."""
        if not value:
            return True
        placeholder_values = [
            "pendiente", "por_definir", "por definir", "tbd", "auto", 
            "n/a", "na", "ninguno", "none", "unknown", "desconocido",
            "huésped", "huesped", "cliente", "usuario", "guest"
        ]
        return value.strip().lower() in placeholder_values
    
    # Check required fields and identify which are missing (including placeholder detection)
    missing_fields = []
    if not customer_name or len(customer_name.strip().split()) < 2 or _is_placeholder(customer_name):
        missing_fields.append("nombre completo (nombre y apellido)")
    if not email or "@" not in email or _is_placeholder(email):
        missing_fields.append("correo electrónico válido")
    if not city or _is_placeholder(city):
        missing_fields.append("ciudad de residencia")
    if not dui_passport or _is_placeholder(dui_passport):
        missing_fields.append("número de DUI o pasaporte")
    if not nationality or _is_placeholder(nationality):
        missing_fields.append("nacionalidad")
    if not payment_maker_name or _is_placeholder(payment_maker_name):
        missing_fields.append("nombre del titular del pago")
    
    if missing_fields:
        missing_list = ", ".join(missing_fields)
        return {
            "valid": False,
            "error": "missing_customer_data",
            "missing_fields": missing_fields,
            "assistant_action": "ASK_CUSTOMER_FOR_MISSING_DATA_NATURALLY",
            "customer_message": f"Para completar su reserva, necesitamos los siguientes datos: {missing_list}. ¿Podría proporcionárnoslos?"
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
        elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower or "doble" in bungalow_type_lower:
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
    
    # Validate room capacity against occupancy rules
    capacity_result = _validate_room_capacity(bungalow_type, adults, children_0_5, children_6_10)
    if not capacity_result["valid"]:
        # Build appropriate customer message based on error type
        occupancy = capacity_result.get("occupancy_score", adults)
        if occupancy < capacity_result.get("min_occupancy", 0):
            # Too few people
            customer_msg = f"El {bungalow_type} requiere un mínimo de {capacity_result['min_occupancy']} personas (ocupación actual: {occupancy}). {capacity_result.get('suggestion', '')}"
        else:
            # Too many people
            customer_msg = f"El {bungalow_type} tiene capacidad máxima de {capacity_result['max_occupancy']} personas (ocupación actual: {occupancy}). {capacity_result.get('suggestion', '')}"
        
        return {
            "valid": False,
            "error": capacity_result["error"],
            "customer_message": customer_msg
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


def _select_room(
    available_rooms: dict, 
    bungalow_type: str, 
    package_type: str,
    excluded_rooms: List[str] = None
) -> Optional[str]:
    """
    Internal Chain of Thought: Select appropriate room based on type and availability.
    NEVER expose room selection logic to customers.
    
    Args:
        available_rooms: Dict of room index -> room number from API
        bungalow_type: Type of room to select
        package_type: Package type (Las Hojas, Escapadita, Pasadía, Romántico)
        excluded_rooms: List of room numbers already selected (for multi-room booking)
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
    
    # CRITICAL FIX: API response format is {"api_index": "room_number"}
    # Keys are API indices, values are ACTUAL room numbers
    room_number_to_index = {}  # Maps actual room number to API index (for booking API call)
    available_room_numbers = []  # List of actual room numbers available
    
    for api_index, room_number in available_rooms.items():
        try:
            # Parse room number (the VALUE in the API response)
            if room_number == "Pasadía":
                # Special case: Pasadía
                room_number_to_index["Pasadía"] = api_index
                available_room_numbers.append("Pasadía")
            elif isinstance(room_number, str) and room_number.endswith('A'):
                # Room with 'A' suffix (like "10A", "7A")
                room_number_to_index[room_number] = api_index
                available_room_numbers.append(room_number)
            else:
                # Regular numeric room - room_number is the VALUE (e.g., "5" means room 5)
                room_num = int(room_number)
                room_number_to_index[room_num] = api_index
                available_room_numbers.append(room_num)
        except (ValueError, TypeError):
            logger.warning(f"[ROOM_DEBUG] Could not parse room number: {room_number} (api_index: {api_index})")
            continue
    
    logger.info(f"[ROOM_DEBUG] Available room numbers: {available_room_numbers}")
    logger.info(f"[ROOM_DEBUG] Room number to index mapping: {room_number_to_index}")
    
    # Filter out already-selected rooms to prevent duplicates in multi-room booking
    if excluded_rooms:
        # Handle mixed types: available_room_numbers contains int (24) and str ("10A")
        excluded_set = set(str(x) for x in excluded_rooms)
        available_room_numbers = [
            r for r in available_room_numbers 
            if str(r) not in excluded_set
        ]
        logger.info(f"[ROOM_DEBUG] After excluding {excluded_rooms}: {available_room_numbers}")
    
    # Special case: Pasadía package can only use Pasadía
    if package_type == "Pasadía":
        if "Pasadía" in available_room_numbers:
            logger.info(f"[ROOM_DEBUG] Selected Pasadía for Pasadía package")
            return "Pasadía"  # Return room name, not API index, for consistency
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
        # Habitación: rooms with 'A' suffix (1A-14A) - the room_number itself has 'A'
        suitable_room_numbers = []
        for room_number in available_room_numbers:
            if isinstance(room_number, str) and room_number.endswith('A'):
                try:
                    # Extract number from room_number like "10A"
                    num = int(room_number[:-1])
                    if 1 <= num <= 14:
                        suitable_room_numbers.append(room_number)
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
        # selected_room is a room number (VALUE), so check if it's in values, not keys
        available_room_numbers = [str(v) for v in current_available_rooms.values()]
        if str(selected_room) not in available_room_numbers:
            logger.warning(f"[ROOM_DEBUG] Room {selected_room} no longer available. Available rooms: {available_room_numbers}")
            
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
                # selected_room is a room number (VALUE), so compare to v, not k
                filtered_rooms = {k: v for k, v in current_available_rooms.items() if str(v) != str(selected_room)}
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
    
    # ═══════════════════════════════════════════════════════════════════════
    # VALIDATION: Check for required fields BEFORE attempting booking
    # If any required field is missing, return error so assistant can ask customer
    # ═══════════════════════════════════════════════════════════════════════
    
    # Helper to detect placeholder values that assistant should NOT use
    placeholder_values = [
        "pendiente", "por_definir", "por definir", "tbd", "auto", 
        "n/a", "na", "ninguno", "none", "unknown", "desconocido",
        "huésped", "huesped", "cliente", "usuario", "guest"
    ]
    def _is_placeholder(value: str) -> bool:
        if not value:
            return True
        return value.strip().lower() in placeholder_values
    
    missing_fields = []
    
    # Validate customer name (must have first AND last name, no placeholders)
    name_parts = customer_name.strip().split() if customer_name else []
    if len(name_parts) < 2 or _is_placeholder(customer_name):
        missing_fields.append("nombre completo (nombre y apellido)")
    
    if not email or not email.strip() or '@' not in email or _is_placeholder(email):
        missing_fields.append("correo electrónico válido")
    
    if not city or not city.strip() or _is_placeholder(city):
        missing_fields.append("ciudad de residencia")
    
    if not nationality or not nationality.strip() or _is_placeholder(nationality):
        missing_fields.append("nacionalidad")
    
    if not dui_passport or not dui_passport.strip() or _is_placeholder(dui_passport):
        missing_fields.append("número de DUI o pasaporte")
    
    if not payment_maker_name or not payment_maker_name.strip() or _is_placeholder(payment_maker_name):
        missing_fields.append("nombre del titular de la tarjeta/cuenta")
    
    # Return error with missing fields so assistant can ask customer
    if missing_fields:
        missing_list = ", ".join(missing_fields)
        logger.warning(f"[BOOKING_VALIDATION] Missing required fields for {wa_id}: {missing_list}")
        return {
            "success": False,
            "error": "missing_customer_data",
            "missing_fields": missing_fields,
            "assistant_action": "ASK_CUSTOMER_FOR_MISSING_DATA_NATURALLY",
            "customer_message": f"Para completar su reserva, necesitamos los siguientes datos: {missing_list}. ¿Podría proporcionárnoslos?"
            # NOTE: Assistant should ask for missing fields in a warm, conversational way
            # Do NOT use robotic lists - weave the request into natural conversation
        }
    
    # Parse validated customer name
    firstname = name_parts[0]
    lastname = " ".join(name_parts[1:])
    
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
    
    # Check if Paquete Romántico is booked in Habitación Doble (rooms 1A-14A)
    # and add suggestion to move to Bungalow Matrimonial for guest comfort
    if package_type == "Romántico" and isinstance(selected_room, str) and selected_room.endswith('A'):
        try:
            # Extract room number from format like "10A"
            room_num = int(selected_room[:-1])
            if 1 <= room_num <= 14:
                comment_parts.append("SUGERENCIA: Intentar mover esta reserva a un Bungalow Matrimonial para mayor comodidad del huésped, ya que Habitación Doble no es ideal para Paquete Romántico")
        except (ValueError, IndexError):
            # If parsing fails, skip this check
            pass
    
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


async def _make_multi_room_api_call(
    customer_name: str,
    email: str,
    phone_number: str,
    city: str,
    dui_passport: str,
    nationality: str,
    check_in_date: str,
    check_out_date: str,
    reserverooms: str,
    adultcount: str,
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
    extra_beds: int = 0,
    extra_beds_cost: float = 0.0,
    customer_instructions: str = None,
    room_count: int = 1
) -> dict:
    """
    Make multi-room booking API call with native + delimiter format.
    
    The API expects:
    - reserverooms: "24+25+26+27" (rooms joined by +)
    - adultcount: "2+3+2+4" (PAX per room joined by +)
    
    Args:
        reserverooms: Room numbers joined by + (e.g., "24+25+26")
        adultcount: Adults per room joined by + (e.g., "2+3+2")
        room_count: Number of rooms being booked
        ... other standard booking parameters ...
    
    Returns:
        {"success": True, "reserva": "HR12345"} or {"success": False, "error": "..."}
    """
    # Parse name
    name_parts = customer_name.strip().split()
    firstname = name_parts[0] if name_parts else customer_name
    lastname = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""
    
    # Determine title
    titulo = "Sra." if any(x in firstname.lower() for x in ["maria", "ana", "rosa", "carmen"]) else "Sr."
    if room_count > 1:
        titulo = "Sres."  # Plural for multi-room
    
    # Format dates
    check_in_formatted = datetime.strptime(check_in_date, "%Y-%m-%d").strftime("%m/%d/%Y")
    check_out_formatted = datetime.strptime(check_out_date, "%Y-%m-%d").strftime("%m/%d/%Y")
    
    # Get accommodation description
    accommodation_map = {
        "Familiar": "Bungalow Familiar: 2 cuartos, 2 baños, sala y terraza para hamacas.",
        "Junior": "Bungalow Junior: 1 ambiente, 2 camas, 1 baño, terraza para hamacas.",
        "Matrimonial": "Bungalow Matrimonial: 1 ambiente, 1 cama matrimonial, 1 baño, terraza para hamacas.",
        "Habitación": "Habitación: 1 ambiente, 2 camas, 1 baño.",
        "Pasadía": "Pasadía"
    }
    
    # Service mapping
    service_map = {
        "Las Hojas": "Paquete Las Hojas",
        "Escapadita": "Paquete Escapadita",
        "Pasadía": "Pasadía",
        "Romántico": "Paquete Romántico"
    }
    
    # Payment method mapping
    payway = "Tarjeta de crédito" if payment_method == "CompraClick" else "Depósito a cuenta BAC"
    
    # Build comment
    comment_parts = [
        f"Reserva de {room_count} habitaciones por Valeria Mendoza"
    ]
    
    # Add extra bed information if applicable
    if extra_beds > 0:
        if extra_beds_cost > 0:
            comment_parts.append(f"Cama(s) extra: {extra_beds} (${extra_beds_cost:.2f} a cobrar en recepción)")
        else:
            comment_parts.append(f"Cama(s) extra: {extra_beds} (sin costo)")
    
    if customer_instructions:
        comment_parts.append(f"Instrucciones: {customer_instructions}")
    
    commenthotel = " | ".join(comment_parts)
    
    # Calculate rates (simplified for multi-room)
    total_adults = sum(int(x) for x in adultcount.split("+"))
    nights = (datetime.strptime(check_out_date, "%Y-%m-%d") - 
              datetime.strptime(check_in_date, "%Y-%m-%d")).days
    nights = max(nights, 1)
    
    adult_rate = payment_amount / (total_adults * nights) if total_adults > 0 else 0
    child_rate = adult_rate * 0.5
    
    # Build accommodation description (handle mixed types)
    if bungalow_type in accommodation_map:
        acomodacion_desc = accommodation_map[bungalow_type]
    else:
        # Mixed types like "2x Junior, 1x Familiar"
        acomodacion_desc = f"Reserva múltiple: {bungalow_type}"
    
    # Build payload (matches _make_booking_api_call structure)
    payload = {
        "titulo": titulo,
        "firstname": firstname,
        "lastname": lastname,
        "commenthotel": commenthotel,
        "phone": phone_number.replace("+503", ""),
        "reserverooms": reserverooms,
        "ciudad": city,
        "checkIn": check_in_formatted,
        "checkOut": check_out_formatted,
        "acomodacion": acomodacion_desc,
        "adultcount": adultcount,
        "childcount": str(children_0_5),
        "childcount1": str(children_6_10),
        "payway": payway,
        "loadamount": f"{payment_amount:.2f}",
        "email": email,
        "dui": dui_passport,
        "national": nationality,
        "adultrate": f"{adult_rate:.2f}",
        "childrate": f"{child_rate:.2f}",
        "cardusername": payment_maker_name,
        "reseramount": f"{payment_amount:.2f}",
        "cardnumer": "0",
        "duedate": "0",
        "comment": commenthotel,
        "compraclick": authorization_number if payment_method == "CompraClick" else None,
        "username": "VM",
        "cancel_flag": "no",
        "service": service_map.get(package_type, "Estadía")
    }
    
    logger.info(f"[MULTI_ROOM_API] Booking {room_count} rooms: {reserverooms}")
    logger.info(f"[MULTI_ROOM_API] PAX distribution: {adultcount}")
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://booking.lashojasresort.club/api/addBookingUserRest",
                data=payload,
                headers={"content-type": "application/x-www-form-urlencoded"},
                timeout=300
            )
            
            if response.status_code != 200:
                logger.error(f"[MULTI_ROOM_API] Failed: {response.status_code} - {response.text[:200]}")
                return {
                    "success": False,
                    "error": f"API returned {response.status_code}"
                }
            
            # Parse response
            response_data = {}
            reserva = "unknown"
            try:
                response_data = response.json()
                reserva = response_data.get("reserva", "unknown")
            except Exception as parse_error:
                logger.warning(f"[MULTI_ROOM_API] Could not parse JSON response: {parse_error}")
                # Try to extract reserva from text response
                if "HR" in response.text:
                    import re
                    match = re.search(r'HR\d+', response.text)
                    if match:
                        reserva = match.group(0)
            
            logger.info(f"[MULTI_ROOM_API] Success! Reserva: {reserva}")
            return {
                "success": True,
                "reserva": reserva,
                "response": response_data
            }
            
    except Exception as e:
        logger.exception(f"[MULTI_ROOM_API] Exception: {e}")
        return {
            "success": False,
            "error": str(e)
        }


async def _update_payment_record(
    payment_method: str, authorization_number: str, transfer_id: str, reserva: str
) -> None:
    """
    Internal Chain of Thought: Update payment record with booking reference.
    NEVER expose database operations to customers.
    Uses infinite retry for database operations.
    """
    def _execute_update():
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            codreser = f"HR{reserva}"
            dateused = datetime.now(EL_SALVADOR_TZ).strftime("%Y-%m-%d %H:%M:%S")
            
            if payment_method == "CompraClick" and authorization_number:
                query = """
                    UPDATE compraclick 
                    SET codreser = %s, dateused = %s
                    WHERE autorizacion = %s
                """
                cursor.execute(query, (codreser, dateused, authorization_number))
            elif payment_method == "Depósito BAC" and transfer_id:
                query = """
                    UPDATE bac 
                    SET codreser = %s, dateused = %s
                    WHERE id = %s
                """
                cursor.execute(query, (codreser, dateused, transfer_id))
            
            conn.commit()
            logger.info(f"Payment record updated: {payment_method}, reserva: {reserva}")
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    execute_with_retry(_execute_update, f"_update_payment_record({payment_method}, {reserva})")


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
    Uses infinite retry for database operations.
    
    Args:
        payment_method: "CompraClick" or "Depósito BAC"
        authorization_number: CompraClick authorization number (if CompraClick)
        transfer_id: Bank transfer ID (if bank transfer)
        
    Returns:
        True if payment already has a booking reference (codereser is not empty)
        False if payment is unused (codereser is NULL/empty)
    """
    def _execute_check():
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            
            if payment_method == "CompraClick" and authorization_number:
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
        finally:
            if conn and conn.is_connected():
                try:
                    cursor.close()
                except:
                    pass
                conn.close()
    
    return execute_with_retry(_execute_check, f"_is_payment_already_used({payment_method})")


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
