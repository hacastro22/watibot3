import httpx
import datetime
import pytz
import holidays
import os
import logging
import mimetypes

def is_within_business_hours():
    # El Salvador timezone
    tz = pytz.timezone('America/El_Salvador')
    now = datetime.datetime.now(tz)
    
    # El Salvador holidays
    sv_holidays = holidays.SV()
    
    if now.date() in sv_holidays:
        return False
        
    # Business hours
    weekday = now.weekday()
    current_time = now.time()
    
    # Monday to Friday (0-4)
    if 0 <= weekday <= 4:
        if datetime.time(8, 0) <= current_time <= datetime.time(16, 55):
            return True
    # Saturday (5)
    elif weekday == 5:
        if datetime.time(9, 0) <= current_time <= datetime.time(12, 55):
            return True
            
    return False

from . import config

import logging

async def start_chatbot(phone_number: str, chatbot_id: str) -> dict:
    """Start a specific chatbot flow for a WhatsApp conversation."""
    url = f"{config.WATI_API_URL}/api/v1/chatbots/start"
    logging.info(f"[DEBUG] Complete start_chatbot URL: {url}")
    headers = {
        "Authorization": f"Bearer {config.WATI_API_KEY}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    payload = {
        "whatsappNumber": phone_number,
        "chatbotId": chatbot_id
    }
    logging.info(f"[DEBUG] Starting chatbot for {phone_number}: {chatbot_id}")
    logging.info(f"[DEBUG] start_chatbot payload: {payload}")
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=payload, headers=headers)
        logging.info(f"[DEBUG] WATI API start_chatbot response: {response.status_code} {response.text}")
        response.raise_for_status()
        return {"result": response.text}

async def update_chat_status(phone_number: str, status: str) -> dict:
    """Update the status of a chat conversation."""
    url = f"{config.WATI_API_URL}/api/v1/updateChatStatus"
    logging.info(f"[DEBUG] Complete update_chat_status URL: {url}")
    headers = {
        "Authorization": f"Bearer {config.WATI_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "whatsappNumber": phone_number,
        "ticketStatus": status
    }
    logging.info(f"[DEBUG] Updating chat status for {phone_number} to {status}")
    logging.info(f"[DEBUG] update_chat_status payload: {payload}")
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=headers)
        logging.info(f"[DEBUG] WATI API update_chat_status response: {response.status_code} {response.text}")
        response.raise_for_status()
        return response.json()

async def assign_operator(phone_number: str, operator_email: str) -> dict:
    """Assign a chat conversation to a specific operator."""
    url = f"{config.WATI_API_URL}/api/v1/assignOperator"
    logging.info(f"[DEBUG] Complete assign_operator URL: {url}")
    headers = {
        "Authorization": f"Bearer {config.WATI_API_KEY}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "whatsappNumber": phone_number,
        "email": operator_email
    }
    logging.info(f"[DEBUG] Assigning conversation {phone_number} to operator {operator_email}")
    logging.info(f"[DEBUG] assign_operator data: {data}")
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=data, headers=headers)
        logging.info(f"[DEBUG] WATI API assign_operator response: {response.status_code} {response.text}")
        response.raise_for_status()
        return {"result": response.text}

async def handle_handover(phone_number: str) -> None:
    """Handle the handover process when triggered:
    1. Change conversation status to SOLVED
    2. Change conversation status to OPEN  
    3. Change conversation status to PENDING
    4. Assign conversation to specific operator
    """
    # Predefined email for the operator to assign to
    operator_email = "reservasoficina@lashojasresort.com"  # Update this to the correct operator email
    
    # Better error handling - attempt each step independently
    success = True
    
    # Step 1: Change conversation status to SOLVED
    try:
        await update_chat_status(phone_number, "SOLVED")
        logging.info(f"[DEBUG] Successfully changed status to SOLVED for {phone_number}")
    except Exception as e:
        logging.error(f"[ERROR] Failed to change status to SOLVED for {phone_number}: {str(e)}")
        success = False
        # Continue with next steps even if this fails
    
    # Step 2: Change conversation status to OPEN
    try:
        await update_chat_status(phone_number, "OPEN")
        logging.info(f"[DEBUG] Successfully changed status to OPEN for {phone_number}")
    except Exception as e:
        logging.error(f"[ERROR] Failed to change status to OPEN for {phone_number}: {str(e)}")
        success = False
        # Continue with next steps even if this fails
    
    # Step 3: Change conversation status to PENDING
    try:
        await update_chat_status(phone_number, "PENDING")
        logging.info(f"[DEBUG] Successfully changed status to PENDING for {phone_number}")
    except Exception as e:
        logging.error(f"[ERROR] Failed to change status to PENDING for {phone_number}: {str(e)}")
        success = False
        # Continue with next step even if this fails
    
    # Step 4: Assign the conversation to the specified operator
    try:
        await assign_operator(phone_number, operator_email)
        logging.info(f"[DEBUG] Successfully assigned operator for {phone_number}")
    except Exception as e:
        logging.error(f"[ERROR] Failed to assign operator for {phone_number}: {str(e)}")
        success = False
    
    if success:
        logging.info(f"[DEBUG] Successfully completed all handover steps for {phone_number}")
    else:
        logging.warning(f"[WARNING] Handover process completed with some errors for {phone_number}")
        # We don't raise the exception so the process can complete as much as possible



async def send_wati_file(phone_number: str, caption: str, file_path: str) -> dict:
    """Sends a file to a user via the WATI API using a multipart/form-data request."""
    headers = {"Authorization": f"Bearer {config.WATI_API_KEY}"}
    url = f"{config.WATI_API_URL}/api/v1/sendSessionFile/{phone_number}"

    if not os.path.exists(file_path):
        logging.error(f"File not found at path: {file_path}")
        raise FileNotFoundError(f"File not found at path: {file_path}")

    content_type, _ = mimetypes.guess_type(file_path)
    if content_type is None:
        content_type = "application/octet-stream"  # Default content type

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            with open(file_path, "rb") as f:
                files = {"file": (os.path.basename(file_path), f, content_type)}
                data = {"caption": caption}
                response = await client.post(url, headers=headers, files=files, data=data)

            response.raise_for_status()
            logging.info(f"Successfully sent file '{file_path}' to {phone_number}. Response: {response.json()}")
            return response.json()
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error sending file to {phone_number}: {e.response.text}")
            raise
        except Exception as e:
            logging.error(f"An unexpected error occurred while sending file '{file_path}': {e}")
            raise


async def send_wati_message(phone_number: str, message: str) -> dict:
    """Send a WhatsApp message via WATI API."""
    # Check for keywords
    message_lower = message.lower()
    
    # Common headers for sending messages
    headers = {
        "Authorization": f"Bearer {config.WATI_API_KEY}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    if "handover" in message_lower:
        # Determine the correct handover message
        if is_within_business_hours():
            handover_message = "En unos momentos uno de nuestros ejecutivos se pondrá en contacto con usted para ayudarle con su pregunta."
        else:
            handover_message = "Gracias por su consulta. En este momento nos encontramos fuera de nuestro horario de atención, que es de lunes a viernes de 8:00 a.m. a 5:00 p.m. y sábados de 9:00 a.m. a 1:00 p.m.\n\nNo se preocupe, he guardado su consulta y se pasará a uno de nuestros ejecutivos para que se ponga en contacto con usted tan pronto como retomemos nuestras actividades.\n\nAgradecemos su paciencia."
        
        # Send the handover message
        url = f"{config.WATI_API_URL}/api/v1/sendSessionMessage/{phone_number}"
        payload = {"messageText": handover_message}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=payload, headers=headers)
            logging.info(f"[DEBUG] WATI API handover message response: {response.status_code} {response.text}")
            response.raise_for_status()
            
        # Start the handover process
        await handle_handover(phone_number)
        return response.json()

    elif "friendly_goodbye" in message_lower:
        goodbye_message = "Con mucho gusto, estamos para servirle. Si tiene alguna otra consulta o necesita ayuda en el futuro, no dude en contactarnos. ¡Que tenga un excelente día!"
        
        # Send the goodbye message
        url = f"{config.WATI_API_URL}/api/v1/sendSessionMessage/{phone_number}"
        payload = {"messageText": goodbye_message}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=payload, headers=headers)
            logging.info(f"[DEBUG] WATI API friendly_goodbye message response: {response.status_code} {response.text}")
            response.raise_for_status()

        # Change status to SOLVED
        await update_chat_status(phone_number, "SOLVED")
        logging.info(f"[DEBUG] Successfully changed status to SOLVED for {phone_number} after friendly goodbye.")
        return response.json()

    else:
        url = f"{config.WATI_API_URL}/api/v1/sendSessionMessage/{phone_number}"
        payload = {"messageText": message}
        async with httpx.AsyncClient() as client:
            response = await client.post(url, data=payload, headers=headers)
            logging.info(f"[DEBUG] WATI API response: {response.status_code} {response.text}")
            response.raise_for_status()
            return response.json()
