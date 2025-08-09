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
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
from .bank_transfer_tool import sync_bank_transfers, validate_bank_transfer
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


async def start_bank_transfer_retry_process(phone_number: str, payment_data: Dict[str, Any]) -> None:
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
    """
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
            logger.warning(f"Booking failed for {phone_number}: {booking_result.get('error')}")
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
