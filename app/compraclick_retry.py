"""
Staged Retry/Escalation System for CompraClick Payment Validation

This module provides automatic retry logic for CompraClick payment validation and booking
with escalation to human agents when needed.
"""
import os
import json
import logging
import asyncio
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
# Delayed imports to avoid circular dependency
# from .compraclick_tool import sync_compraclick_payments, validate_compraclick_payment
# from .booking_tool import make_booking
from .wati_client import update_chat_status, send_wati_message

logger = logging.getLogger(__name__)

# File to store retry state for each customer
RETRY_STATE_FILE = "/tmp/compraclick_retry_state.json"


def _load_retry_state() -> Dict[str, Any]:
    """Load retry state from file."""
    try:
        if os.path.exists(RETRY_STATE_FILE):
            with open(RETRY_STATE_FILE, 'r') as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load CompraClick retry state: {e}")
    return {}


def _save_retry_state(state: Dict[str, Any]) -> None:
    """Save retry state to file."""
    try:
        with open(RETRY_STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save CompraClick retry state: {e}")


async def start_compraclick_retry_process(phone_number: str, payment_data: Dict[str, Any]) -> None:
    """
    Start the staged retry process for CompraClick payment validation and booking.
    
    Schedule:
    - Every 5 minutes x 6 times (30 minutes total)
    - Every 30 minutes x 4 times (2 hours total) 
    - Every 1 hour x 6 times (6 hours total)
    - Total: 8.5 hours of automatic retries
    - If still fails: escalate to human agent and mark as PENDING
    
    Args:
        phone_number: Customer's phone number
        payment_data: Dictionary containing authorization_number, booking_total, and booking_data
    """
    logger.info(f"Starting CompraClick retry process for {phone_number}")
    
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
            logger.exception(f"Error in CompraClick retry thread for {phone_number}: {e}")
        finally:
            loop.close()
    
    thread = threading.Thread(target=run_retry_process, daemon=True)
    thread.start()
    
    logger.info(f"CompraClick retry process thread started for {phone_number}")


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
                logger.info(f"CompraClick retry process ended for {phone_number} - no state or escalated")
                return
                
            payment_data = customer_state["payment_data"]
            stage = customer_state["stage"]
            attempt_count = customer_state["attempt_count"]
            
            # Determine retry interval and max attempts based on stage
            if stage == 1:
                interval_minutes = 5
                max_attempts = customer_state["max_attempts_stage_1"]
                stage_name = "Stage 1 (5-minute intervals)"
            elif stage == 2:
                interval_minutes = 30
                max_attempts = customer_state["max_attempts_stage_2"]
                stage_name = "Stage 2 (30-minute intervals)"
            elif stage == 3:
                interval_minutes = 60
                max_attempts = customer_state["max_attempts_stage_3"]
                stage_name = "Stage 3 (1-hour intervals)"
            else:
                # All stages complete - escalate to human
                await _escalate_to_human(phone_number, "All retry attempts exhausted")
                return
            
            logger.info(f"CompraClick retry {stage_name} - Attempt {attempt_count + 1}/{max_attempts} for {phone_number}")
            
            # Attempt sync and validation
            success = await _attempt_sync_and_validation(phone_number, payment_data)
            
            if success:
                logger.info(f"CompraClick payment validation and booking successful for {phone_number}")
                # Remove from retry state
                retry_state = _load_retry_state()
                if phone_number in retry_state:
                    del retry_state[phone_number]
                    _save_retry_state(retry_state)
                return
            
            # Update attempt count
            retry_state = _load_retry_state()
            if phone_number in retry_state and not retry_state[phone_number].get("escalated"):
                retry_state[phone_number]["attempt_count"] += 1
                
                # Check if we need to move to next stage
                if retry_state[phone_number]["attempt_count"] >= max_attempts:
                    if stage < 3:
                        retry_state[phone_number]["stage"] += 1
                        retry_state[phone_number]["attempt_count"] = 0
                        logger.info(f"Moving {phone_number} to next CompraClick retry stage: {retry_state[phone_number]['stage']}")
                    else:
                        # All stages exhausted
                        await _escalate_to_human(phone_number, "All retry attempts exhausted")
                        return
                
                _save_retry_state(retry_state)
                
                # Wait for next attempt
                await asyncio.sleep(interval_minutes * 60)
            else:
                # State changed or escalated
                return
                
    except Exception as e:
        logger.exception(f"Error in CompraClick retry process for {phone_number}: {e}")
        await _escalate_to_human(phone_number, f"Error in retry process: {e}")


async def _attempt_sync_and_validation(phone_number: str, payment_data: Dict[str, Any]) -> bool:
    """
    Attempt to sync CompraClick payments and validate/complete booking.
    
    Returns:
        True if successful, False if failed
    """
    try:
        # Delayed imports to avoid circular dependency
        from .compraclick_tool import sync_compraclick_payments, validate_compraclick_payment
        from .booking_tool import make_booking
        authorization_number = payment_data["authorization_number"]
        booking_total = payment_data["booking_total"]
        booking_data = payment_data["booking_data"]
        
        logger.info(f"Attempting CompraClick sync and validation for {phone_number}, auth: {authorization_number}")
        
        # Step 1: Sync CompraClick payments
        sync_result = await sync_compraclick_payments()
        if not sync_result.get("success"):
            logger.warning(f"CompraClick sync failed for {phone_number}: {sync_result.get('error')}")
            return False
        
        logger.info(f"CompraClick sync completed for {phone_number}. Inserted: {sync_result.get('data', {}).get('rows_inserted', 0)}")
        
        # Step 2: Validate CompraClick payment
        validation_result = await validate_compraclick_payment(authorization_number, booking_total)
        if not validation_result.get("success"):
            logger.info(f"CompraClick payment still not found for {phone_number}, auth: {authorization_number}")
            return False
        
        logger.info(f"CompraClick payment validated for {phone_number}, auth: {authorization_number}")
        
        # Step 3: Complete booking
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
            payment_method="CompraClick",
            payment_amount=validation_result["data"]["remaining_amount"],
            payment_maker_name=booking_data["customer_name"],
            transfer_id=authorization_number,
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
            logger.warning(f"Booking failed for {phone_number}: {booking_result.get('error')}")
            return False
            
    except Exception as e:
        logger.exception(f"Error during CompraClick sync and validation attempt for {phone_number}: {e}")
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
            "Disculpe la demora en procesar su pago CompraClick. Hemos escalado su caso a uno de nuestros agentes "
            "para revisar manualmente su pago. Un agente se comunicará con usted "
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
        logger.info(f"Marking customer {phone_number} as frustrated, halting CompraClick retry process")
        
        retry_state = _load_retry_state()
        if phone_number in retry_state:
            retry_state[phone_number]["customer_frustrated"] = True
            retry_state[phone_number]["frustration_time"] = datetime.now().isoformat()
            _save_retry_state(retry_state)
            
        # Immediately escalate to human
        await _escalate_to_human(phone_number, "Customer expressed frustration or requested refund")
        
    except Exception as e:
        logger.exception(f"Failed to mark customer {phone_number} as frustrated: {e}")
