# Multi-Room Booking Implementation Plan

## Executive Summary
Enable the booking system to handle multiple room bookings in a single transaction, with proper guest distribution across rooms and payment tracking for multiple reservations.

## Current State Analysis

### Limitations
- `booking_tool.py`: Books only 1 room per call
- Payment tools: Update 1 database row per booking
- Assistant: No logic for distributing guests across multiple rooms
- Database: Single reservation code storage per payment record

### Affected Components
1. **booking_tool.py** - Core booking logic
2. **compraclick_tool.py** - CompraClick payment validation
3. **compraclick_retry.py** - CompraClick retry mechanism
4. **bank_transfer.py** - Bank transfer validation
5. **bank_transfer_retry.py** - Bank transfer retry mechanism
6. **openai_agent.py** - Assistant instructions
7. **Database tables** - compraclick and bac tables

## Implementation Plan

### Phase 1: Data Structure Changes

#### 1.1 Booking Tool Parameters
```python
# Current (from app/booking_tool.py line 287)
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
) -> dict

# Proposed - Add support for multiple bookings
async def make_booking(
    customer_name: str,
    email: str,
    phone_number: str,
    city: str,
    dui_passport: str,
    nationality: str,
    check_in_date: str,
    check_out_date: str,
    bookings: List[dict],  # NEW: List of room bookings
    # Each booking dict contains:
    # {
    #   "bungalow_type": str,
    #   "adults": int,
    #   "children_0_5": int,
    #   "children_6_10": int,
    #   "extra_beds": int,
    #   "extra_beds_cost": float
    # }
    package_type: str,
    payment_method: str,
    payment_amount: float,  # Total for all rooms
    payment_maker_name: str,
    wa_id: str,
    authorization_number: str = None,
    transfer_id: str = None,
    force_process: bool = False,
    customer_instructions: str = None
) -> dict
```

#### 1.2 Return Structure
```python
# Current return
{
    "success": bool,
    "reserva": str,
    "customer_message": str
}

# Proposed return
{
    "success": bool,
    "bookings": [
        {
            "reserva": str,
            "room": str,
            "bungalow_type": str,
            "adults": int,
            "children_0_5": int,
            "children_6_10": int,
            "amount": float
        }
    ],
    "total_amount": float,
    "customer_message": str,
    "failed_bookings": []  # Any rooms that couldn't be booked
}
```

### Phase 2: Booking Tool Implementation

#### 2.1 Core Booking Logic
```python
async def make_booking(customer_name: str, ..., bookings: List[dict], ...) -> dict:
    """
    Handle multiple room bookings in a single transaction.
    Modified version of existing make_booking function.
    """
    successful_bookings = []
    failed_bookings = []
    total_booked_amount = 0
    all_reservation_codes = []
    
    # Step 1: Validate all rooms are available using existing function
    availability_result = await _check_room_availability(check_in_date, check_out_date)
    if not availability_result["success"]:
        return {
            "success": False,
            "error": availability_result["error"],
            "customer_message": "Error al verificar disponibilidad."
        }
    
    available_rooms = availability_result["rooms"]
    
    # Step 2: Select rooms for each booking request
    selected_rooms = set()  # Track selected rooms to avoid duplicates
    room_selections = []
    
    for booking in bookings:
        # Use existing _select_room function with modification
        room = _select_room_excluding(
            available_rooms, 
            booking["bungalow_type"],
            package_type,
            exclude=selected_rooms
        )
        if room:
            selected_rooms.add(room)
            room_selections.append({
                "room": room,
                "booking_data": booking
            })
        else:
            failed_bookings.append({
                "booking": booking,
                "reason": f"No hay {booking['bungalow_type']} disponible"
            })
    
    # Step 3: Process each booking using existing _make_booking_api_call
    for selection in room_selections:
        booking_data = selection["booking_data"]
        selected_room = selection["room"]
        
        # Calculate individual booking cost using existing pricing function
        # get_price_for_date returns rates based on date and room type
        price_data = get_price_for_date(check_in_date, booking_data["bungalow_type"])
        adult_rate = price_data.get("adult_rate", 86.25)  # Default Junior rate
        child_rate = price_data.get("child_rate", 43.125)  # Default 50% of adult
        booking_cost = (
            booking_data["adults"] * adult_rate + 
            booking_data["children_0_5"] * 0 +  # Free
            booking_data["children_6_10"] * child_rate +
            booking_data.get("extra_beds_cost", 0)
        )
        
        # Make the API call using existing function structure with retry logic
        # Based on production code (lines 857-918), the booking API call includes:
        # 1. Retry mechanism with up to 3 attempts
        # 2. Re-validation of room availability on each retry
        # 3. Room reselection if original room becomes unavailable
        api_result = await _make_booking_api_call_with_retry(
            selected_room=selected_room,
            customer_name=customer_name,
            email=email,
            phone_number=phone_number,  # Extracted from wa_id
            city=city,
            dui_passport=dui_passport,
            nationality=nationality,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            adults=booking_data["adults"],
            children_0_5=booking_data["children_0_5"],
            children_6_10=booking_data["children_6_10"],
            bungalow_type=booking_data["bungalow_type"],
            package_type=package_type,
            payment_method=payment_method,
            payment_amount=booking_cost,  # Individual room cost
            payment_maker_name=payment_maker_name,
            wa_id=wa_id,
            authorization_number=authorization_number,
            extra_beds=booking_data.get("extra_beds", 0),
            extra_beds_cost=booking_data.get("extra_beds_cost", 0),
            customer_instructions=customer_instructions,
            max_retries=3  # Production uses 3 retries
        )
        
        if api_result["success"]:
            successful_bookings.append({
                "reserva": api_result["reserva"],
                "room": selected_room,
                "bungalow_type": booking_data["bungalow_type"],
                "adults": booking_data["adults"],
                "children_0_5": booking_data["children_0_5"],
                "children_6_10": booking_data["children_6_10"],
                "amount": booking_cost
            })
            all_reservation_codes.append(api_result["reserva"])
            total_booked_amount += booking_cost
        else:
            failed_bookings.append({
                "booking": booking_data,
                "room": selected_room,
                "error": api_result.get("error", "Unknown error")
            })
    
    # Step 4: Update payment records with all reservation codes
    # NOTE: The 'used' amount was already updated during payment validation
    # We only need to update the reservation codes and dateused
    if successful_bookings:
        await _update_payment_record_multiple(
            payment_method=payment_method,
            authorization_number=authorization_number,
            transfer_id=transfer_id,
            reservation_codes=all_reservation_codes
            # total_amount removed - not needed as 'used' is updated during validation
        )
    
    return {
        "success": len(successful_bookings) > 0,
        "bookings": successful_bookings,
        "failed_bookings": failed_bookings,
        "total_amount": total_booked_amount,
        "customer_message": _generate_multi_booking_message(successful_bookings, failed_bookings)
    }
```

#### 2.2 Room Selection Enhancement
```python
# New function in booking_tool.py based on existing _select_room
def _select_room_excluding(available_rooms: dict, bungalow_type: str, package_type: str, exclude: set = None) -> Optional[str]:
    """
    Select a room, excluding already selected rooms.
    Based on existing _select_room function (line 821).
    """
    if exclude is None:
        exclude = set()
    
    # Filter out already selected rooms from available_rooms
    # Note: _select_room returns actual room numbers (e.g., "54", "56")
    # available_rooms is a list of room dictionaries from API
    remaining_rooms = []
    for room in available_rooms:
        room_number = room.get("room_number") or room.get("number")
        if room_number not in exclude:
            remaining_rooms.append(room)
    
    # Use existing _select_room logic with filtered rooms
    # This returns the actual room number, not an index
    return _select_room(remaining_rooms, bungalow_type, package_type)

# Helper function to generate customer messages
def _generate_multi_booking_message(successful_bookings: List[dict], failed_bookings: List[dict]) -> str:
    """
    Generate customer-friendly message for multi-room booking results.
    """
    if not successful_bookings:
        return "Lo sentimos, no pudimos completar ninguna de las reservaciones solicitadas."
    
    message_parts = []
    
    if len(successful_bookings) == 1:
        booking = successful_bookings[0]
        message_parts.append(
            f"✅ Reservación confirmada:\n"
            f"Habitación {booking['room']} - {booking['bungalow_type']}\n"
            f"Código de reserva: HR{booking['reserva']}\n"
            f"Adultos: {booking['adults']}, Niños 0-5: {booking['children_0_5']}, Niños 6-10: {booking['children_6_10']}"
        )
    else:
        message_parts.append(f"✅ Se confirmaron {len(successful_bookings)} habitaciones:")
        for i, booking in enumerate(successful_bookings, 1):
            message_parts.append(
                f"\n{i}. Habitación {booking['room']} - {booking['bungalow_type']}\n"
                f"   Código: HR{booking['reserva']}\n"
                f"   Ocupantes: {booking['adults']} adultos"
            )
            if booking['children_0_5'] > 0:
                message_parts.append(f", {booking['children_0_5']} niños (0-5)")
            if booking['children_6_10'] > 0:
                message_parts.append(f", {booking['children_6_10']} niños (6-10)")
    
    if failed_bookings:
        message_parts.append(f"\n\n⚠️ No se pudieron reservar {len(failed_bookings)} habitaciones por falta de disponibilidad.")
    
    return "".join(message_parts)
```

### Phase 3: Payment Tools Update

#### 3.1 Database Schema Changes
```sql
-- Current: codreser column stores single reservation code
-- Proposed: Store comma-separated reservation codes

-- IMPORTANT: The 'used' amount is updated during payment validation,
-- NOT during booking record update. The booking tool only updates
-- codreser and dateused fields.

-- Example update for multiple bookings (booking_tool.py):
UPDATE compraclick 
SET 
    codreser = 'HR26181,HR26182,HR26183',  -- Multiple codes
    dateused = '2025-08-23 10:30:00'
WHERE autorizacion = '123456';

-- The 'used' amount is incremented separately in compraclick_tool.py
-- during payment validation (line 765):
UPDATE compraclick SET used = used + 258.75 WHERE autorizacion = '123456';
```

#### 3.2 Payment Record Update Function
```python
# New function in booking_tool.py to handle multiple reservations
async def _update_payment_record_multiple(
    payment_method: str, 
    authorization_number: str, 
    transfer_id: str, 
    reservation_codes: List[str]
) -> None:
    """
    Update payment record with multiple booking references.
    Based on current _update_payment_record (line 1492).
    NOTE: Does NOT update 'used' amount - that's done during payment validation.
    """
    conn = get_db_connection()
    if not conn:
        raise Exception("Database connection failed")
    
    try:
        cursor = conn.cursor()
        # Format reservation codes as comma-separated HR codes
        codreser_list = [f"HR{code}" for code in reservation_codes]
        codreser = ",".join(codreser_list)
        dateused = datetime.now(EL_SALVADOR_TZ).strftime("%Y-%m-%d %H:%M:%S")
        
        if payment_method == "CompraClick" and authorization_number:
            # Update CompraClick payment record with multiple codes
            # Based on current implementation (line 1497):
            # Only updates codreser and dateused, NOT the used amount
            query = "UPDATE compraclick SET codreser = %s, dateused = %s WHERE autorizacion = %s"
            cursor.execute(query, (codreser, dateused, authorization_number))
            
        elif payment_method == "Depósito BAC" and transfer_id:
            # Update bank transfer payment record with multiple codes
            # Based on current implementation (line 1500):
            # Only updates codreser and dateused, NOT the used amount
            query = "UPDATE bac SET codreser = %s, dateused = %s WHERE id = %s"
            cursor.execute(query, (codreser, dateused, transfer_id))
        
        conn.commit()
        logger.info(f"Payment record updated: {payment_method}, reservations: {codreser_list}")
        
    except Exception as e:
        logger.error(f"Failed to update payment record: {e}")
        raise
    finally:
        if conn and conn.is_connected():
            conn.close()
```

#### 3.3 Payment Validation Note
```python
# IMPORTANT FINDING: Payment validation already updates the 'used' amount
# in compraclick_tool.py (line 765) and bank_transfer_tool.py (line 491).
# These functions increment the 'used' amount during payment validation,
# NOT in the booking tool's payment record update.

# Current flow in compraclick_tool.py (line 765):
# new_used_amount = float(used_amount) + payment_amount
# update_query = "UPDATE compraclick SET used = %s WHERE autorizacion = %s"
# cursor.execute(update_query, (new_used_amount, authorization_number))

# For multi-room bookings, the payment validation should:
# 1. Continue to validate the total amount for all bookings
# 2. Update the 'used' amount with the total booking cost
# 3. The booking tool will later update the reservation codes

# No changes needed to payment validation functions!
# They already handle updating the 'used' amount correctly.
# The booking tool handles updating reservation codes separately.

# Key insight: Separation of concerns in production code:
# - Payment tools (compraclick_tool.py, bank_transfer_tool.py): Update 'used' amount
# - Booking tool (booking_tool.py): Updates 'codreser' and 'dateused' only
#
# This separation should be maintained for multi-room bookings:
# 1. Payment validation updates the total 'used' amount
# 2. Booking tool updates reservation codes after successful bookings
```

### Phase 4: Assistant Instructions Update

#### 4.1 Room Distribution Logic
Add to `openai_agent.py` system instructions:

```python
"""
### Multi-Room Booking Guidelines

When a customer requests multiple rooms:

1. **Ask for Guest Distribution Preference:**
   - "¿Cómo le gustaría distribuir a los huéspedes en las habitaciones?"
   - Allow customer to specify exact distribution
   - If not specified, suggest even distribution

2. **Room Allocation Examples:**
   - 8 adults for 3 Junior bungalows → 3+3+2 or as customer prefers
   - 6 adults + 2 children for 2 Familiar → 3+1 and 3+1 or as customer prefers
   
3. **Booking Structure:**
   When calling make_booking for multiple rooms, create a bookings list:
   ```
   bookings = [
       {"bungalow_type": "Junior", "adults": 3, "children_0_5": 0, "children_6_10": 0},
       {"bungalow_type": "Junior", "adults": 3, "children_0_5": 0, "children_6_10": 0},
       {"bungalow_type": "Junior", "adults": 2, "children_0_5": 0, "children_6_10": 0}
   ]
   ```

4. **Payment Handling:**
   - Single payment can cover multiple bookings
   - System will automatically distribute payment across reservations
   - All reservation codes will be linked to the payment

5. **Confirmation Message:**
   Provide clear confirmation with all reservation codes:
   "Sus 3 Bungalows Junior han sido reservados exitosamente:
   - Habitación 54 (3 adultos): Reserva #26181
   - Habitación 56 (3 adultos): Reserva #26182
   - Habitación 57 (2 adultos): Reserva #26183"
"""
```

### Phase 5: Backward Compatibility

#### 5.1 Support Both Single and Multi Booking
```python
# Wrapper function to maintain backward compatibility
async def make_booking(
    customer_name: str,
    email: str,
    phone_number: str,
    city: str,
    dui_passport: str,
    nationality: str,
    check_in_date: str,
    check_out_date: str,
    # These parameters become optional for backward compatibility
    adults: int = None,
    children_0_5: int = None,
    children_6_10: int = None,
    bungalow_type: str = None,
    # New parameter for multi-booking
    bookings: List[dict] = None,
    package_type: str = None,
    payment_method: str = None,
    payment_amount: float = None,
    payment_maker_name: str = None,
    wa_id: str = None,
    authorization_number: str = None,
    transfer_id: str = None,
    force_process: bool = False,
    extra_beds: int = 0,
    extra_beds_cost: float = 0.0,
    customer_instructions: str = None
) -> dict:
    """
    Enhanced booking function with backward compatibility.
    Accepts either single booking parameters OR bookings list.
    """
    
    # Detect format based on parameters
    if bookings is None:
        # Legacy single room booking - convert to list format
        if bungalow_type is None:
            return {
                "success": False,
                "error": "Missing booking information",
                "customer_message": "Información de reserva incompleta."
            }
        
        bookings = [{
            "bungalow_type": bungalow_type,
            "adults": adults,
            "children_0_5": children_0_5,
            "children_6_10": children_6_10,
            "extra_beds": extra_beds,
            "extra_beds_cost": extra_beds_cost
        }]
        
        # Call the original single-booking logic for true backward compatibility
        if len(bookings) == 1:
            # Use existing single booking flow
            return await _make_single_booking_legacy(
                customer_name=customer_name,
                email=email,
                phone_number=phone_number,
                city=city,
                dui_passport=dui_passport,
                nationality=nationality,
                check_in_date=check_in_date,
                check_out_date=check_out_date,
                adults=adults,
                children_0_5=children_0_5,
                children_6_10=children_6_10,
                bungalow_type=bungalow_type,
                package_type=package_type,
                payment_method=payment_method,
                payment_amount=payment_amount,
                payment_maker_name=payment_maker_name,
                wa_id=wa_id,
                authorization_number=authorization_number,
                transfer_id=transfer_id,
                force_process=force_process,
                extra_beds=extra_beds,
                extra_beds_cost=extra_beds_cost,
                customer_instructions=customer_instructions
            )
    
    # New multi-room booking logic
    return await _make_multi_booking(
        customer_name=customer_name,
        email=email,
        phone_number=phone_number,
        city=city,
        dui_passport=dui_passport,
        nationality=nationality,
        check_in_date=check_in_date,
        check_out_date=check_out_date,
        bookings=bookings,
        package_type=package_type,
        payment_method=payment_method,
        payment_amount=payment_amount,
        payment_maker_name=payment_maker_name,
        wa_id=wa_id,
        authorization_number=authorization_number,
        transfer_id=transfer_id,
        force_process=force_process,
        customer_instructions=customer_instructions
    )

# Rename current make_booking to preserve legacy functionality
async def _make_single_booking_legacy(...) -> dict:
    # This is the current make_booking implementation
    # Keeps all existing logic intact
    pass
```

### Phase 6: Testing Strategy

#### 6.1 Test Scenarios
1. **Single Room Booking** - Ensure backward compatibility
2. **Multiple Same Type** - 3 Junior bungalows
3. **Multiple Mixed Types** - 2 Junior + 1 Familiar
4. **Partial Success** - 3 requested, only 2 available
5. **Payment Distribution** - Verify correct amount allocation
6. **Database Updates** - Check comma-separated reservation codes

#### 6.2 Test Implementation
```python
# test_multi_booking.py
async def test_multiple_junior_booking():
    """Test booking 3 Junior bungalows for 8 people."""
    result = await make_booking(
        bookings=[
            {"bungalow_type": "Junior", "adults": 3, "children_0_5": 0, "children_6_10": 0},
            {"bungalow_type": "Junior", "adults": 3, "children_0_5": 0, "children_6_10": 0},
            {"bungalow_type": "Junior", "adults": 2, "children_0_5": 0, "children_6_10": 0}
        ],
        customer_name="Test Customer",
        check_in_date="2025-08-23",
        check_out_date="2025-08-24",
        ...
    )
    
    assert result["success"] == True
    assert len(result["bookings"]) == 3
    assert result["total_amount"] == 258.75  # 86.25 * 3
```

### Phase 7: Implementation Timeline

1. **Week 1:** Data structure changes and booking tool core logic
2. **Week 2:** Payment tools update and database modifications
3. **Week 3:** Assistant instructions and testing
4. **Week 4:** Production deployment and monitoring

### Phase 8: Risk Mitigation

#### 8.1 Potential Issues
- **Race Conditions:** Multiple users booking same rooms simultaneously
- **Partial Failures:** Some rooms book, others fail
- **Payment Reconciliation:** Matching payments to multiple bookings
- **Rollback Complexity:** Undoing partial bookings on failure

#### 8.2 Solutions
- **Atomic Transactions:** Use database transactions for all-or-nothing booking
- **Room Locking:** Temporarily lock selected rooms during booking process
- **Detailed Logging:** Track each step for debugging and audit
- **Graceful Degradation:** Allow partial bookings with clear customer communication

### Phase 9: Monitoring & Metrics

#### 9.1 Key Metrics
- Multi-room booking success rate
- Average rooms per booking
- Payment reconciliation accuracy
- Failed booking reasons

#### 9.2 Logging Enhancements
```python
logger.info(f"[MULTI_BOOKING] Starting {len(bookings)} room bookings for {customer_name}")
logger.info(f"[MULTI_BOOKING] Successfully booked {len(successful_bookings)}/{len(bookings)} rooms")
logger.info(f"[MULTI_BOOKING] Reservation codes: {reservation_codes}")
```

## Important Production Code Findings

### Key Corrections from Code Analysis:

1. **Payment Record Updates:**
   - The `used` amount is updated during payment validation (in payment tools), NOT in booking tool
   - `_update_payment_record` only updates `codreser` and `dateused` fields
   - This separation of concerns must be maintained for multi-room bookings

2. **Pricing Logic:**
   - Use `get_price_for_date()` from `database_client.py` instead of non-existent helper functions
   - Function returns price data based on date and room type

3. **Room Selection:**
   - `_select_room()` returns actual room numbers (e.g., "54", "56"), not indices
   - Room availability comes from an external API
   - Selected rooms must be tracked to avoid duplicates

4. **Booking API Call:**
   - Production code includes retry logic with up to 3 attempts
   - Re-validates room availability on each retry
   - Can reselect room if original becomes unavailable

5. **Phone Number Extraction:**
   - Production code extracts phone from wa_id using `_extract_phone_from_wa_id()`
   - Handles multiple country codes with specific extraction rules

6. **Error Handling:**
   - Extensive logging at each step for debugging
   - Graceful handling of partial failures
   - Clear customer messaging for all scenarios

## Conclusion

This implementation plan provides a comprehensive approach to enabling multi-room bookings while maintaining backward compatibility and ensuring robust payment tracking. The phased approach allows for incremental testing and reduces deployment risk. All code examples have been aligned with production patterns and existing function signatures.

### Success Criteria
- ✅ Book multiple rooms in single transaction
- ✅ Flexible guest distribution across rooms
- ✅ Accurate payment tracking with multiple reservation codes  
- ✅ Clear customer communication
- ✅ Backward compatibility with single bookings
- ✅ Robust error handling and partial booking support
- ✅ Maintain separation of payment validation and booking record updates
- ✅ Preserve existing retry mechanisms and room re-validation logic
- ✅ Align with production code patterns and database schema
