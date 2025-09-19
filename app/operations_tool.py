"""
Operations notification tool for sending urgent guest issues to the Operations Department.

This module provides functionality to notify the Operations Department immediately
when guests report issues with rooms, service, or facilities while they are on-site.
"""

import logging
from . import wati_client

logger = logging.getLogger(__name__)

# Operations Department WhatsApp number
OPERATIONS_PHONE_NUMBER = "50377976000"

async def notify_operations_department(
    issue_type: str,
    issue_description: str,
    guest_name: str = "Huésped",
    guest_phone: str = "Contacto vía asistente virtual", 
    guest_location: str = "Instalaciones del hotel"
) -> dict:
    """
    Send an urgent notification to the Operations Department about a guest issue.
    
    This function is used when guests report problems while they are at the hotel
    and need immediate assistance that cannot wait for email resolution.
    
    Args:
        issue_type (str): Type of issue (room, service, facilities, etc.) - REQUIRED
        issue_description (str): Detailed description of the issue - REQUIRED
        guest_name (str): Name of the guest reporting the issue (default: "Huésped")
        guest_phone (str): Guest's phone number for contact (default: "Contacto vía asistente virtual")
        guest_location (str): Guest's current location (default: "Instalaciones del hotel")
    
    Returns:
        dict: Response from WATI API
    """
    
    # Format the urgent notification message
    urgent_message = f"""👤 Nombre del huésped: {guest_name}
📱 Teléfono: {guest_phone}
📍 Ubicación: {guest_location}
⚠️ Tipo de problema: {issue_type}

📝 Descripción del problema:
{issue_description}"""

    try:
        logger.info(f"[OPERATIONS] Sending urgent notification to Operations Department for guest {guest_name} (phone: {guest_phone})")
        logger.info(f"[OPERATIONS] Issue type: {issue_type}")
        logger.info(f"[OPERATIONS] Issue description: {issue_description[:100]}...")
        
        # Send the message to Operations Department
        response = await wati_client.send_wati_message(OPERATIONS_PHONE_NUMBER, urgent_message)
        
        logger.info(f"[OPERATIONS] Successfully sent notification to Operations Department")
        return {
            "success": True,
            "message": "Notificación enviada exitosamente al Departamento de Operaciones",
            "operations_phone": OPERATIONS_PHONE_NUMBER,
            "guest_info": {
                "name": guest_name,
                "phone": guest_phone,
                "issue_type": issue_type
            }
        }
        
    except Exception as e:
        logger.error(f"[OPERATIONS] Failed to send notification to Operations Department: {str(e)}")
        return {
            "success": False,
            "error": f"Error al enviar notificación: {str(e)}",
            "message": "No se pudo enviar la notificación al Departamento de Operaciones"
        }
