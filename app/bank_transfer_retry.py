"""
Staged Retry/Escalation System for Bank Transfer Validation

This module provides automatic retry logic for bank transfer validation and booking
with escalation to human agents when needed.
"""
import os
import json
import logging
import asyncio
import threading
import time
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from .bank_transfer_tool import sync_bank_transfers, validate_bank_transfer
from .database_client import check_room_availability
from .wati_client import update_chat_status, send_wati_message

logger = logging.getLogger(__name__)

# File to store retry state for each customer
RETRY_STATE_FILE = "/tmp/bank_transfer_retry_state.json"


def _load_retry_state() -> Dict[str, Any]:
    """Load retry state from file."""
    try:
        if os.path.exists(RETRY_STATE_FILE):
            with open(RETRY_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load retry state: {e}")
    return {}


def _save_retry_state(state: Dict[str, Any]) -> None:
    """Save retry state to file."""
    try:
        with open(RETRY_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save retry state: {e}")


async def start_bank_transfer_retry_process(phone_number: str, payment_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Start the staged retry process for bank transfer validation and booking.
    
    Schedule:
    - Every 5 minutes x 6 times (30 minutes total)
    - Every 30 minutes x 4 times (2 hours total) 
    - Every 1 hour x 6 times (6 hours total)
    - Total: 8.5 hours of automatic retries
    - If still fails: escalate to human agent and mark as PENDING
    
    Args:
        phone_number: Customer's phone number
        payment_data: Dictionary containing slip_date, slip_amount, booking_amount, and booking_data
    
    Returns:
        Dict with success status and any errors
    """
    # 🚨 VALIDATION: Reject empty phone numbers
    if not phone_number or not phone_number.strip():
        logger.error("CRITICAL: start_bank_transfer_retry_process called with empty phone_number!")
        return {"success": False, "error": "phone_number is required and cannot be empty"}
    
    # 🚨 VALIDATION: Check for illogical future dates
    slip_date = payment_data.get("slip_date", "")
    if slip_date:
        try:
            slip_date_obj = datetime.strptime(slip_date, "%Y-%m-%d")
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if slip_date_obj > today:
                logger.error(f"ILLOGICAL DATE: slip_date {slip_date} is in the future! OCR likely misread the year.")
                return {
                    "success": False, 
                    "error": "future_date",
                    "message": f"La fecha del comprobante ({slip_date}) está en el futuro. Esto es imposible. Por favor verifique la fecha real de la transferencia con el cliente."
                }
        except ValueError as e:
            logger.warning(f"Could not parse slip_date {slip_date}: {e}")
    
    # 🚨 AVAILABILITY GATE: Check room availability BEFORE starting retry process
    # If no rooms available, customer already paid but we can't book - escalate immediately
    booking_data = payment_data.get("booking_data", {})
    check_in_date = booking_data.get("check_in_date")
    check_out_date = booking_data.get("check_out_date")
    package_type = booking_data.get("package_type", "").lower()
    
    # Only check availability for lodging (not pasadía)
    if check_in_date and check_out_date and package_type not in ["pasadía", "pasadia", "day pass"]:
        logger.info(f"[AVAILABILITY_GATE] Checking availability before bank transfer retry for {check_in_date} to {check_out_date}")
        availability = await check_room_availability(check_in_date, check_out_date)
        
        # availability returns: {'bungalow_familiar': 'Available'/'Not Available', 'bungalow_junior': '...', 'habitacion': '...'}
        has_availability = any(status == 'Available' for status in availability.values())
        available_types = [room_type for room_type, status in availability.items() if status == 'Available']
        
        if not has_availability:
            logger.error(f"[AVAILABILITY_GATE] NO ROOMS AVAILABLE for {phone_number} dates {check_in_date} to {check_out_date} - CANNOT process bank transfer")
            # Escalate immediately - customer paid but no rooms!
            await update_chat_status(phone_number, "PENDING", "Reservas1")
            await send_wati_message(
                phone_number,
                f"🚨 Estimado cliente, hemos detectado un problema con su reservación.\n\n"
                f"Lamentablemente, mientras procesábamos su pago, otra persona reservó la última habitación disponible para las fechas {check_in_date} al {check_out_date}.\n\n"
                f"Un agente de nuestro equipo de reservas se comunicará con usted en breve para ofrecerle fechas alternativas o procesar un reembolso.\n\n"
                f"Le pedimos disculpas por este inconveniente. 🙏"
            )
            return {
                "success": False, 
                "error": "no_availability",
                "message": f"No hay habitaciones disponibles para {check_in_date} al {check_out_date}. Cliente notificado y caso escalado.",
                "availability_blocked": True
            }
        
        logger.info(f"[AVAILABILITY_GATE] Availability confirmed: {available_types} available for bank transfer retry")
    
    logger.info(f"Starting staged retry process for {phone_number}")
    
    # Load current retry state
    retry_state = _load_retry_state()
    
    # Initialize retry state for this customer
    retry_state[phone_number] = {
        "start_time": datetime.now().isoformat(),
        "payment_data": payment_data,
        "stage": 1,  # Stage 1: 5-min intervals, Stage 2: 30-min intervals, Stage 3: 1-hour intervals
        "attempt_count": 0,
        "max_attempts_stage_1": 6,
        "max_attempts_stage_2": 4, 
        "max_attempts_stage_3": 6,
        "escalated": False,
        "customer_frustrated": False
    }
    
    _save_retry_state(retry_state)
    
    # Start the retry process in a separate thread to avoid blocking
    def run_retry_process():
        try:
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_execute_retry_process(phone_number))
        except Exception as e:
            logger.exception(f"Error in retry thread for {phone_number}: {e}")
        finally:
            loop.close()
    
    thread = threading.Thread(target=run_retry_process, daemon=True)
    thread.start()
    
    logger.info(f"Retry process thread started for {phone_number}")
    
    return {"success": True, "message": "Retry process started"}


async def _execute_retry_process(phone_number: str) -> None:
    """
    Execute the staged retry process for a customer.
    This runs in a continuous loop and handles all retry attempts.
    """
    try:
        while True:
            retry_state = _load_retry_state()
            customer_state = retry_state.get(phone_number)
            
            if not customer_state or customer_state.get("escalated"):
                logger.info(f"Retry process ended for {phone_number} - no state or escalated")
                return
                
            payment_data = customer_state["payment_data"]
            stage = customer_state["stage"]
            attempt_count = customer_state["attempt_count"]
            
            # Determine retry interval and max attempts based on stage
            if stage == 1:
                interval_minutes = 5
                max_attempts = customer_state["max_attempts_stage_1"]
                stage_name = "Stage 1 (5-min intervals)"
            elif stage == 2:
                interval_minutes = 30
                max_attempts = customer_state["max_attempts_stage_2"]
                stage_name = "Stage 2 (30-min intervals)"
            elif stage == 3:
                interval_minutes = 60
                max_attempts = customer_state["max_attempts_stage_3"]
                stage_name = "Stage 3 (1-hour intervals)"
            else:
                # All stages completed, escalate
                await _escalate_to_human(phone_number, "All retry stages completed without success")
                return
                
            logger.info(f"Executing {stage_name} attempt #{attempt_count + 1} for {phone_number}")
            
            # Check if customer has become frustrated or requested refund
            if customer_state.get("customer_frustrated"):
                logger.info(f"Retry process halted for {phone_number} due to customer frustration")
                await _escalate_to_human(phone_number, "Customer expressed frustration")
                return
                
            # Attempt validation and booking
            success = await _attempt_validation_and_booking(phone_number, payment_data)
            
            if success:
                logger.info(f"Validation and booking successful for {phone_number} on {stage_name} attempt #{attempt_count + 1}")
                # Remove from retry state
                retry_state = _load_retry_state()
                if phone_number in retry_state:
                    del retry_state[phone_number]
                    _save_retry_state(retry_state)
                return
                
            # Update attempt count
            retry_state = _load_retry_state()
            customer_state = retry_state.get(phone_number, {})
            customer_state["attempt_count"] = attempt_count + 1
            
            # Check if we've reached max attempts for current stage
            if customer_state["attempt_count"] >= max_attempts:
                if stage < 3:
                    # Special handling when transitioning from Stage 1 to Stage 2 (around 60 minutes)
                    if stage == 1:
                        logger.info(f"Stage 1 completed for {phone_number}, checking for Transferencia UNI vs 365")
                        # Check if customer used Transferencia UNI instead of recommended Transferencia 365
                        uni_check_result = await _check_transferencia_uni_vs_365(phone_number, payment_data)
                        if uni_check_result == "escalated":
                            # Customer used UNI, case escalated
                            return
                        elif uni_check_result == "continue":
                            # Customer used 365, continue retrying
                            pass
                        # If no response or other result, continue with Stage 2 anyway
                    
                    # Move to next stage
                    customer_state["stage"] = stage + 1
                    customer_state["attempt_count"] = 0
                    logger.info(f"Moving {phone_number} to stage {stage + 1}")
                    retry_state[phone_number] = customer_state
                    _save_retry_state(retry_state)
                    # Continue immediately to next stage
                    continue
                else:
                    # All stages completed, escalate
                    await _escalate_to_human(phone_number, "All retry attempts exhausted")
                    return
            else:
                # Continue with current stage - save state and wait for next attempt
                retry_state[phone_number] = customer_state
                _save_retry_state(retry_state)
                
                # Wait for the retry interval before next attempt
                logger.info(f"Waiting {interval_minutes} minutes before next attempt for {phone_number}")
                await asyncio.sleep(interval_minutes * 60)
                
    except Exception as e:
        logger.exception(f"Error in retry process for {phone_number}: {e}")
        await _escalate_to_human(phone_number, f"Retry process error: {e}")


async def _attempt_validation_and_booking(phone_number: str, payment_data: Dict[str, Any]) -> bool:
    """
    Attempt to validate bank transfer and complete booking.
    
    Returns:
        True if successful, False if failed
    """
    try:
        logger.info(f"Attempting validation and booking for {phone_number}")
        
        # First sync bank transfers
        sync_result = await sync_bank_transfers()
        if not sync_result.get("success"):
            logger.warning(f"Bank transfer sync failed for {phone_number}: {sync_result.get('error')}")
            return False
            
        # Validate the payment
        validation_result = validate_bank_transfer(
            slip_date=payment_data["slip_date"],
            slip_amount=payment_data["slip_amount"],
            booking_amount=payment_data["booking_amount"]
        )
        
        if not validation_result.get("success"):
            logger.warning(f"Payment validation failed for {phone_number}: {validation_result.get('message')}")
            return False
            
        # Import booking function to complete the booking
        from .booking_tool import make_booking
        
        # Complete the booking - FORCE PROCESS to bypass time validation in retry mechanism
        booking_data = payment_data["booking_data"]
        booking_result = await make_booking(
            customer_name=booking_data["customer_name"],
            email=booking_data["email"],
            phone_number=phone_number,
            city=booking_data["city"],
            dui_passport=booking_data["dui_passport"],
            nationality=booking_data["nationality"],
            check_in_date=booking_data["check_in_date"],
            check_out_date=booking_data["check_out_date"],
            adults=booking_data["adults"],
            children_0_5=booking_data["children_0_5"],
            children_6_10=booking_data["children_6_10"],
            bungalow_type=booking_data["bungalow_type"],
            package_type=booking_data["package_type"],
            payment_method="Depósito BAC",
            payment_amount=payment_data["slip_amount"],
            payment_maker_name=booking_data["customer_name"],
            wa_id=phone_number,  # Added missing wa_id parameter
            transfer_id=validation_result["transfer_id"],
            force_process=True,  # BYPASS time validation in retry mechanism
            extra_beds=booking_data.get("extra_beds", 0),
            extra_beds_cost=booking_data.get("extra_beds_cost", 0.0),
            customer_instructions=booking_data.get("customer_instructions", None)
        )
        
        if booking_result.get("success"):
            # Send success message to customer
            success_message = f"""¡Excelente! Su pago ha sido validado y su reserva ha sido confirmada exitosamente. 🎉

Código de reserva: {booking_result.get('reserva', 'N/A')}

Los detalles de su reserva han sido enviados a su correo electrónico. Si tiene alguna pregunta o necesita asistencia adicional, no dude en contactarnos por este medio o llamándonos al 2505-2800.

¡Gracias por confiar en nosotros y por elegirnos para pasar momentos de calidad con su familia y amigos! Su confianza es muy importante para nosotros.

¡Esperamos verle pronto en Las Hojas Resort! 🌴"""
            await send_wati_message(phone_number, success_message)
            logger.info(f"Booking completed successfully for {phone_number}: {booking_result.get('reserva')}")
            return True
        else:
            error_type = booking_result.get("error", "")
            logger.warning(f"Booking failed for {phone_number}: {error_type}")
            
            # Handle missing customer data - ask customer instead of silently failing
            if error_type == "missing_customer_data":
                missing_fields = booking_result.get("missing_fields", [])
                logger.info(f"[MISSING_DATA] Asking customer {phone_number} for: {missing_fields}")
                
                # Build a warm, conversational message asking for missing data
                if len(missing_fields) == 1:
                    ask_message = f"¡Hola! 🌴 Para completar su reserva, me ayuda con su {missing_fields[0]}? 😊"
                else:
                    fields_text = ", ".join(missing_fields[:-1]) + f" y {missing_fields[-1]}"
                    ask_message = f"¡Hola! 🌴 Para completar su reserva, solo me faltan algunos datos: {fields_text}. ¿Me los proporciona? 😊"
                
                await send_wati_message(phone_number, ask_message)
                
                # Mark state so we know we're waiting for customer data
                retry_state = _load_retry_state()
                if phone_number in retry_state:
                    retry_state[phone_number]["waiting_for_data"] = True
                    retry_state[phone_number]["missing_fields"] = missing_fields
                    _save_retry_state(retry_state)
                
                return False  # Don't continue retrying with bad data
            
            return False
            
    except Exception as e:
        logger.exception(f"Error during validation and booking attempt for {phone_number}: {e}")
        return False


async def _escalate_to_human(phone_number: str, reason: str) -> None:
    """
    Escalate conversation to human agent.
    """
    try:
        logger.info(f"Escalating {phone_number} to human agent. Reason: {reason}")
        
        # Mark conversation as PENDING
        await update_chat_status(phone_number, "PENDING")
        
        # Send message to customer
        escalation_message = (
            "Disculpe la demora en procesar su pago. Hemos escalado su caso a uno de nuestros agentes "
            "para revisar manualmente su transferencia bancaria. Un agente se comunicará con usted "
            "a la brevedad para resolver su reserva. Gracias por su paciencia. 🙏"
        )
        await send_wati_message(phone_number, escalation_message)
        
        # Update retry state to mark as escalated
        retry_state = _load_retry_state()
        if phone_number in retry_state:
            retry_state[phone_number]["escalated"] = True
            retry_state[phone_number]["escalation_reason"] = reason
            retry_state[phone_number]["escalation_time"] = datetime.now().isoformat()
            _save_retry_state(retry_state)
            
        logger.info(f"Successfully escalated {phone_number} to human agent")
        
    except Exception as e:
        logger.exception(f"Failed to escalate {phone_number} to human agent: {e}")


async def mark_customer_frustrated(phone_number: str) -> None:
    """
    Mark a customer as frustrated to halt the retry process and escalate immediately.
    This should be called when the customer expresses annoyance or requests a refund.
    """
    try:
        logger.info(f"Marking customer {phone_number} as frustrated, halting retry process")
        
        retry_state = _load_retry_state()
        if phone_number in retry_state:
            retry_state[phone_number]["customer_frustrated"] = True
            retry_state[phone_number]["frustration_time"] = datetime.now().isoformat()
            _save_retry_state(retry_state)
            
        # Immediately escalate to human
        await _escalate_to_human(phone_number, "Customer expressed frustration or requested refund")
        
    except Exception as e:
        logger.exception(f"Failed to mark customer {phone_number} as frustrated: {e}")


async def _check_transferencia_uni_vs_365(phone_number: str, payment_data: Dict[str, Any]) -> str:
    """
    Check if customer used Transferencia UNI vs recommended Transferencia 365.
    Called after 60 minutes (Stage 1 completion) if transfer still not found.
    
    Returns:
        "escalated" - Customer used UNI, case escalated to human
        "continue" - Customer used 365, continue retrying
        "no_response" - No response from customer, continue anyway
    """
    try:
        from pytz import timezone
        from .wati_client import send_wati_message
        
        logger.info(f"Checking Transferencia UNI vs 365 for {phone_number} after 60 minutes")
        
        # Get El Salvador timezone for UNI time window validation
        el_salvador_tz = timezone("America/El_Salvador")
        now_sv = datetime.now(el_salvador_tz)
        
        # Construct clarification message
        uni_clarification_message = (
            f"Hemos estado verificando su transferencia bancaria durante los últimos 60 minutos, "
            f"pero aún no aparece reflejada en nuestro sistema bancario.\n\n"
            f"Para poder ayudarle mejor, necesitamos confirmar qué tipo de transferencia utilizó:\n\n"
            f"🔹 **¿Utilizó 'Transferencia 365'** (la que recomendamos, disponible 24/7)?\n"
            f"🔹 **¿O utilizó 'Transferencia UNI'** (que tiene horarios limitados)?\n\n"
            f"⏰ **IMPORTANTE:** Transferencia UNI solo procesa transferencias de lunes a viernes "
            f"de 9:00 AM a 5:00 PM. Si hizo la transferencia fuera de este horario o cerca del cierre, "
            f"podría aparecer hasta el siguiente día hábil.\n\n"
            f"Por favor, responda con:\n"
            f"- '365' si usó Transferencia 365\n"
            f"- 'UNI' si usó Transferencia UNI\n\n"
            f"Esto nos ayudará a darle el mejor seguimiento a su caso. 🏦"
        )
        
        # Send clarification message based on channel type
        if phone_number.isdigit() and len(phone_number) >= 10:
            # WATI customer
            await send_wati_message(phone_number, uni_clarification_message)
            logger.info(f"Sent WATI UNI vs 365 clarification to {phone_number}")
        else:
            # ManyChat customer
            from app.clients import manychat_client
            
            # Try both FB and IG versions since we don't know which channel
            message_sent = False
            try:
                await manychat_client.send_text_message(phone_number, uni_clarification_message)
                logger.info(f"Sent ManyChat FB UNI vs 365 clarification to {phone_number}")
                message_sent = True
            except Exception as fb_error:
                try:
                    await manychat_client.send_ig_text_message(phone_number, uni_clarification_message)
                    logger.info(f"Sent ManyChat IG UNI vs 365 clarification to {phone_number}")
                    message_sent = True
                except Exception as ig_error:
                    logger.error(f"Failed to send ManyChat clarification to {phone_number}: FB={fb_error}, IG={ig_error}")
            
            if not message_sent:
                logger.warning(f"Could not send UNI vs 365 clarification to ManyChat customer {phone_number}")
        
        # Message sent successfully - the customer will respond through normal chat
        # The OpenAI agent will handle the response using handle_customer_transferencia_type_response
        # For now, continue with Stage 2 while waiting for response
        logger.info(f"Transferencia UNI vs 365 clarification sent to {phone_number}, continuing with Stage 2")
        return "no_response"
        
    except Exception as e:
        logger.exception(f"Error checking Transferencia UNI vs 365 for {phone_number}: {e}")
        return "no_response"


async def _handle_transferencia_uni_escalation(phone_number: str, payment_data: Dict[str, Any]) -> None:
    """
    Handle escalation for customers who used Transferencia UNI.
    Sends explanation and escalates to human agent.
    """
    try:
        from .wati_client import send_wati_message, transfer_to_agent
        
        logger.info(f"Escalating Transferencia UNI case for {phone_number}")
        
        # Explain UNI limitations and escalation
        uni_escalation_message = (
            f"Entendemos que utilizó **Transferencia UNI** para realizar su pago. 🏦\n\n"
            f"📋 **Información importante sobre Transferencia UNI:**\n"
            f"• Solo procesa transferencias de **lunes a viernes, 9:00 AM - 5:00 PM** (hora de El Salvador)\n"
            f"• Si transfirió cerca del cierre o fuera del horario, aparecerá hasta el siguiente día hábil\n"
            f"• En caso de feriados, puede retrasarse aún más\n\n"
            f"✅ **Para brindarle el mejor servicio, hemos escalado su caso a un agente humano** "
            f"quien hará seguimiento personalizado a su transferencia y finalizará su reserva tan pronto "
            f"como aparezca en el sistema.\n\n"
            f"Un agente se comunicará con usted para coordinar el proceso. "
            f"Gracias por su comprensión y paciencia. 🙏"
        )
        
        # Send escalation message based on channel type
        if phone_number.isdigit() and len(phone_number) >= 10:
            # WATI customer
            await send_wati_message(phone_number, uni_escalation_message)
            logger.info(f"Sent WATI escalation message to {phone_number}")
        else:
            # ManyChat customer - use ManyChat client
            from app.clients import manychat_client
            
            # Try both FB and IG versions since we don't know which channel
            message_sent = False
            try:
                await manychat_client.send_text_message(phone_number, uni_escalation_message)
                logger.info(f"Sent ManyChat FB escalation message to {phone_number}")
                message_sent = True
            except Exception as fb_error:
                try:
                    await manychat_client.send_ig_text_message(phone_number, uni_escalation_message)
                    logger.info(f"Sent ManyChat IG escalation message to {phone_number}")
                    message_sent = True
                except Exception as ig_error:
                    logger.error(f"Failed to send ManyChat escalation message to {phone_number}: FB={fb_error}, IG={ig_error}")
            
            if not message_sent:
                logger.warning(f"Could not send escalation message to ManyChat customer {phone_number}")
        
        # Transfer to human agent based on channel type
        try:
            # Detect channel type based on phone_number format
            # WATI uses phone numbers like "50378308239"
            # ManyChat uses subscriber IDs (different format)
            if phone_number.isdigit() and len(phone_number) >= 10:
                # WATI customer - use WATI-specific transfer
                from .wati_client import assign_operator, update_chat_status
                
                await update_chat_status(phone_number, "PENDING")
                await assign_operator(phone_number, "reservasoficina@lashojasresort.com")
                logger.info(f"Transferred WATI Transferencia UNI case {phone_number} to reservasoficina@lashojasresort.com")
            else:
                # ManyChat customer - use ManyChat transfer (mark as open + tag)
                from app.clients import manychat_client
                
                # Mark conversation as open for human agent attention
                # Try both FB and IG versions since we don't know which channel
                try:
                    await manychat_client.mark_conversation_as_open(phone_number)
                    logger.info(f"Marked ManyChat FB conversation {phone_number} as open for Transferencia UNI case")
                except Exception as fb_error:
                    try:
                        await manychat_client.mark_ig_conversation_as_open(phone_number)
                        logger.info(f"Marked ManyChat IG conversation {phone_number} as open for Transferencia UNI case")
                    except Exception as ig_error:
                        logger.error(f"Failed to mark ManyChat conversation {phone_number} as open: FB={fb_error}, IG={ig_error}")
                
                # Add tag for escalation tracking
                try:
                    await manychat_client.add_tag_to_subscriber(phone_number, "transferencia_uni_escalation")
                    logger.info(f"Tagged ManyChat customer {phone_number} for Transferencia UNI escalation")
                except Exception as tag_error:
                    logger.error(f"Failed to tag ManyChat customer {phone_number}: {tag_error}")
                
                logger.info(f"Transferred ManyChat Transferencia UNI case {phone_number} using standard ManyChat transfer")
                
        except Exception as transfer_error:
            logger.error(f"Failed to transfer {phone_number} to agent: {transfer_error}")
            # Fallback: escalate using existing method
            await _escalate_to_human(phone_number, "Transferencia UNI - requires human tracking")
        
        # Remove from retry state
        retry_state = _load_retry_state()
        if phone_number in retry_state:
            del retry_state[phone_number]
            _save_retry_state(retry_state)
            logger.info(f"Removed {phone_number} from retry state due to UNI escalation")
            
    except Exception as e:
        logger.exception(f"Error handling Transferencia UNI escalation for {phone_number}: {e}")


async def _handle_transferencia_365_continue(phone_number: str) -> None:
    """
    Handle customer who confirmed they used Transferencia 365.
    Send reassurance and continue retrying.
    """
    try:
        from .wati_client import send_wati_message
        
        logger.info(f"Customer {phone_number} confirmed using Transferencia 365, continuing retries")
        
        # Send reassurance message
        reassurance_message = (
            f"Perfecto, gracias por confirmar que utilizó **Transferencia 365**. 🌟\n\n"
            f"Como usó el método recomendado (disponible 24/7), continuaremos verificando "
            f"automáticamente su transferencia hasta que aparezca en el sistema.\n\n"
            f"✅ **Seguiremos intentando cada 30 minutos** para finalizar su reserva "
            f"tan pronto como el banco actualice la información.\n\n"
            f"Le notificaremos inmediatamente cuando su pago sea confirmado. "
            f"No necesita hacer nada más por su parte. 🙏"
        )
        
        # Send reassurance message based on channel type
        if phone_number.isdigit() and len(phone_number) >= 10:
            # WATI customer
            await send_wati_message(phone_number, reassurance_message)
            logger.info(f"Sent WATI Transferencia 365 reassurance to {phone_number}")
        else:
            # ManyChat customer
            from app.clients import manychat_client
            
            # Try both FB and IG versions since we don't know which channel
            message_sent = False
            try:
                await manychat_client.send_text_message(phone_number, reassurance_message)
                logger.info(f"Sent ManyChat FB Transferencia 365 reassurance to {phone_number}")
                message_sent = True
            except Exception as fb_error:
                try:
                    await manychat_client.send_ig_text_message(phone_number, reassurance_message)
                    logger.info(f"Sent ManyChat IG Transferencia 365 reassurance to {phone_number}")
                    message_sent = True
                except Exception as ig_error:
                    logger.error(f"Failed to send ManyChat reassurance to {phone_number}: FB={fb_error}, IG={ig_error}")
            
            if not message_sent:
                logger.warning(f"Could not send Transferencia 365 reassurance to ManyChat customer {phone_number}")
        
    except Exception as e:
        logger.exception(f"Error sending Transferencia 365 reassurance to {phone_number}: {e}")


async def handle_customer_transferencia_type_response(phone_number: str, response_text: str) -> str:
    """
    Handle customer response about which transferencia type they used.
    This should be called by the OpenAI agent when customer responds to the UNI vs 365 question.
    
    Args:
        phone_number: Customer's phone number
        response_text: Customer's response message
    
    Returns:
        "uni_escalated" - Customer used UNI, case escalated
        "365_continue" - Customer used 365, retries continue  
        "unclear" - Response unclear, continue with retries
    """
    try:
        logger.info(f"Processing transferencia type response from {phone_number}: {response_text}")
        
        response_lower = response_text.lower().strip()
        
        # Check for UNI keywords
        uni_keywords = ["uni", "transferencia uni", "usé uni", "use uni", "fue uni"]
        if any(keyword in response_lower for keyword in uni_keywords):
            logger.info(f"Customer {phone_number} confirmed using Transferencia UNI")
            
            # Get payment data from retry state
            retry_state = _load_retry_state()
            customer_state = retry_state.get(phone_number, {})
            payment_data = customer_state.get("payment_data", {})
            
            # Escalate UNI case
            await _handle_transferencia_uni_escalation(phone_number, payment_data)
            return "uni_escalated"
        
        # Check for 365 keywords
        three65_keywords = ["365", "transferencia 365", "usé 365", "use 365", "fue 365", "trescientos sesenta cinco"]
        if any(keyword in response_lower for keyword in three65_keywords):
            logger.info(f"Customer {phone_number} confirmed using Transferencia 365")
            
            # Send reassurance and continue retries
            await _handle_transferencia_365_continue(phone_number)
            return "365_continue"
        
        # Unclear response - log and continue with retries anyway
        logger.info(f"Unclear transferencia type response from {phone_number}, continuing with retries")
        return "unclear"
        
    except Exception as e:
        logger.exception(f"Error handling transferencia type response from {phone_number}: {e}")
        return "unclear"
