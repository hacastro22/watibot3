"""
Smart Availability Checker for Las Hojas Resort
Implements partial availability checking to maximize booking opportunities.

When a multi-night stay is unavailable for the full period, this module:
1. Checks availability for the full requested period
2. If unavailable, checks each possible sub-period combination  
3. Returns structured data about available partial periods
4. Enables the assistant to offer alternative stay options
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from app.database_client import check_room_availability

logger = logging.getLogger(__name__)

async def check_smart_availability(check_in_date: str, check_out_date: str) -> Dict:
    """
    Smart availability checking that offers partial stay options when full period is unavailable.
    
    Args:
        check_in_date: Check-in date in YYYY-MM-DD format
        check_out_date: Check-out date in YYYY-MM-DD format
        
    Returns:
        Dictionary containing:
        - full_period_available: bool - if any rooms available for full period
        - full_period_availability: dict - availability by room type for full period
        - partial_options: list - available partial stay periods if full period unavailable
        - recommendation_message: str - Spanish message for customer about options
    """
    logger.info(f"[SMART_AVAILABILITY] Checking availability for {check_in_date} to {check_out_date}")
    
    try:
        # Step 1: Check full period availability (current behavior)
        full_availability = await check_room_availability(check_in_date, check_out_date)
        
        if "error" in full_availability:
            return {
                "success": False,
                "error": full_availability["error"],
                "customer_message": "Error al verificar disponibilidad."
            }
        
        # Check if any room type is available for the full period
        any_available = any(status == "Available" for status in full_availability.values())
        
        logger.info(f"[SMART_AVAILABILITY] Full period availability: {full_availability}")
        logger.info(f"[SMART_AVAILABILITY] Any rooms available for full period: {any_available}")
        
        result = {
            "success": True,
            "full_period_available": any_available,
            "full_period_availability": full_availability,
            "partial_options": [],
            "recommendation_message": ""
        }
        
        # If full period is available, return success
        if any_available:
            available_types = [room_type for room_type, status in full_availability.items() if status == "Available"]
            result["recommendation_message"] = f"¡Perfecto! Tenemos disponibilidad completa del {_format_date_spanish(check_in_date)} al {_format_date_spanish(check_out_date)} en: {', '.join(available_types).replace('_', ' ').title()}."
            return result
        
        # Step 2: If no rooms available for full period, check partial periods
        logger.info(f"[SMART_AVAILABILITY] No rooms available for full period. Checking partial options...")
        
        partial_options = await _find_partial_availability_options(check_in_date, check_out_date)
        result["partial_options"] = partial_options
        
        # Step 3: Generate customer recommendation message
        if partial_options:
            result["recommendation_message"] = _generate_partial_availability_message(
                check_in_date, check_out_date, partial_options
            )
        else:
            result["recommendation_message"] = f"Lamentablemente no tenemos disponibilidad para el período del {_format_date_spanish(check_in_date)} al {_format_date_spanish(check_out_date)}. ¿Le gustaría que revisemos otras fechas cercanas?"
        
        logger.info(f"[SMART_AVAILABILITY] Found {len(partial_options)} partial options")
        return result
        
    except Exception as e:
        logger.error(f"[SMART_AVAILABILITY] Error in smart availability check: {e}")
        return {
            "success": False,
            "error": f"Smart availability check failed: {e}",
            "customer_message": "Error al verificar opciones de disponibilidad."
        }

async def _find_partial_availability_options(original_checkin: str, original_checkout: str) -> List[Dict]:
    """
    Find all possible partial stay periods within the requested date range.
    
    Returns list of available partial stays with format:
    [
        {
            "check_in": "2025-07-27",
            "check_out": "2025-07-28", 
            "nights": 1,
            "available_room_types": ["bungalow_junior", "habitacion"]
        },
        ...
    ]
    """
    partial_options = []
    
    # Convert date strings to datetime objects
    start_date = datetime.strptime(original_checkin, "%Y-%m-%d")
    end_date = datetime.strptime(original_checkout, "%Y-%m-%d")
    total_nights = (end_date - start_date).days
    
    logger.info(f"[SMART_AVAILABILITY] Checking partial options for {total_nights} nights total")
    
    # Check all possible sub-periods (minimum 1 night)
    for nights in range(1, total_nights + 1):
        for start_offset in range(total_nights - nights + 1):
            # Calculate the sub-period dates
            sub_checkin = start_date + timedelta(days=start_offset)
            sub_checkout = sub_checkin + timedelta(days=nights)
            
            sub_checkin_str = sub_checkin.strftime("%Y-%m-%d")
            sub_checkout_str = sub_checkout.strftime("%Y-%m-%d")
            
            logger.info(f"[SMART_AVAILABILITY] Checking sub-period: {sub_checkin_str} to {sub_checkout_str} ({nights} nights)")
            
            # Check availability for this sub-period
            sub_availability = await check_room_availability(sub_checkin_str, sub_checkout_str)
            
            if "error" not in sub_availability:
                available_types = [room_type for room_type, status in sub_availability.items() if status == "Available"]
                
                if available_types:
                    partial_options.append({
                        "check_in": sub_checkin_str,
                        "check_out": sub_checkout_str,
                        "nights": nights,
                        "available_room_types": available_types,
                        "availability_detail": sub_availability
                    })
                    logger.info(f"[SMART_AVAILABILITY] Found partial availability: {sub_checkin_str} to {sub_checkout_str}, types: {available_types}")
    
    # Sort by preference: longer stays first, then by proximity to original dates
    partial_options.sort(key=lambda x: (-x["nights"], x["check_in"]))
    
    return partial_options

def _generate_partial_availability_message(original_checkin: str, original_checkout: str, partial_options: List[Dict]) -> str:
    """Generate a customer-friendly message about partial availability options."""
    
    if not partial_options:
        return f"Lamentablemente no tenemos disponibilidad para el período del {_format_date_spanish(original_checkin)} al {_format_date_spanish(original_checkout)}."
    
    message = f"No tenemos disponibilidad completa del {_format_date_spanish(original_checkin)} al {_format_date_spanish(original_checkout)}, "
    message += "pero sí tenemos las siguientes opciones parciales disponibles:\n\n"
    
    # Show top 3 best options to avoid overwhelming the customer
    top_options = partial_options[:3]
    
    for i, option in enumerate(top_options, 1):
        nights_text = "noche" if option["nights"] == 1 else "noches"
        room_types = [rt.replace("_", " ").title() for rt in option["available_room_types"]]
        room_types_str = ", ".join(room_types)
        
        message += f"{i}. **{_format_date_spanish(option['check_in'])} al {_format_date_spanish(option['check_out'])}** "
        message += f"({option['nights']} {nights_text}) - Disponible en: {room_types_str}\n"
    
    if len(partial_options) > 3:
        message += f"\n¡Y tenemos {len(partial_options) - 3} opciones más disponibles!"
    
    message += "\n¿Alguna de estas opciones le interesa? ¡Estaremos encantados de ayudarle a reservar! 🌴"
    
    return message

def _format_date_spanish(date_str: str) -> str:
    """Convert YYYY-MM-DD to Spanish date format."""
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d")
        months = {
            1: "enero", 2: "febrero", 3: "marzo", 4: "abril", 5: "mayo", 6: "junio",
            7: "julio", 8: "agosto", 9: "septiembre", 10: "octubre", 11: "noviembre", 12: "diciembre"
        }
        day = date_obj.day
        month = months[date_obj.month]
        return f"{day} de {month}"
    except:
        return date_str
