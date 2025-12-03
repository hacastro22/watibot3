# Multi-Room Booking Implementation Plan V2
## Native API Integration Strategy for Las Hojas Resort Booking System

## ⚠️ PLAN VERSION 2.0 - COMPLETE REWRITE ⚠️

**As of November 28, 2025:**
- 🆕 **NEW APPROACH**: API now supports multi-room booking natively
- ❌ **OLD PLAN DEPRECATED**: Wrapper pattern no longer needed
- ✅ **SIMPLER**: Single API call handles all rooms atomically
- ✅ **FEWER CHANGES**: ~70% less code than old plan

---

## Executive Summary

The external booking API (`addBookingUserRest`) now supports multi-room booking natively via:
- `reserverooms` field: `"24+25+26+27"` (rooms joined by `+`)
- `adultcount` field: `"2+3+2+4"` (PAX per room joined by `+`)

This eliminates the need for the complex wrapper pattern. Instead, we modify the existing booking flow to format these fields correctly for multi-room requests.

---

## Key Differences: Old vs New Approach

| Aspect | Old Plan (Wrapper) | New Plan (Native API) |
|--------|-------------------|----------------------|
| **API Calls** | N calls for N rooms | 1 call for N rooms |
| **Atomicity** | Manual ALL-OR-NOTHING | API handles natively |
| **Room Tracking** | `excluded_rooms` parameter | API-level room selection |
| **Payment Update** | `skip_payment_update` flag | Single transaction |
| **New Functions** | 9 | 3 |
| **Files Modified** | 7 | 4 |
| **Implementation Time** | 11 days | 6-7 days |
| **Complexity** | HIGH | LOW |

---

## API Reference (from API_GUIDE_MULTI_ROOM_BOOKING.md)

### Multi-Room Format

```
reserverooms = "24+25+26+27"    # 4 rooms: 24, 25, 26, 27
adultcount   = "2+3+2+4"        # Room 24: 2 adults, Room 25: 3 adults, etc.
```

### Validation Rules
1. Number of PAX values MUST equal number of rooms (if using `+` delimiter)
2. Single PAX value applies to ALL rooms (backward compatible)
3. Rooms joined by `+` are booked in ONE atomic transaction

---

## ⚠️ CRITICAL GAP IDENTIFIED: Room Capacity Validation

### Current State Analysis

**Finding:** The current codebase has **NO room capacity validation**:

| Component | Validates Capacity? | Status |
|-----------|---------------------|--------|
| `_validate_booking_info()` | ❌ NO | Only validates email, dates, required fields |
| `_select_room()` | ❌ NO | Only maps room numbers to types |
| `make_booking()` | ❌ NO | Accepts any number of adults |
| `smart_availability.py` | ❌ NO | No capacity checks |
| System Instructions | ⚠️ REFERENCED | `occupancy_violation` mentioned but not implemented |

**Risk:** Assistant could attempt to book 6 adults in a Matrimonial (max 2) and the API would accept it, causing overbooking.

### Room Capacity Constraints (to be enforced)

**⚠️ ALIGNED WITH `system_instructions_new.txt` (line 1300-1302)**

| Room Type | Min Occupancy | Max Occupancy | Notes |
|-----------|---------------|---------------|-------|
| **Familiar** | 5 | 8 | Rooms 1-17. Groups <5 people → NOT allowed |
| **Junior** | 2 | 8 | Rooms 18-59. Default for most bookings |
| **Habitación** (Doble) | 2 | 4 | Rooms 1A-14A. Couples/small groups |
| **Matrimonial** | 2 | 2 | Junior subset: rooms 22,42,47,48,53. Couples only |
| **Pasadía** | - | - | Day pass, no room capacity |

**Formula (from line 1300):** `occupancy_score = (adults × 1) + (children_6_10 × 0.5)`
- ⚠️ Children 0-5 count as **0** (not 0.5)
- Children 6-10 count as 0.5

**Auto-filter rules (from line 1302):**
- `occupancy < 5` → NO Familiar (too few people)
- `occupancy > 8` → NO Junior (too many people)
- `occupancy > 4` → NO Habitación/Doble (too many people)

**Naming Note:** System instructions uses "Doble", `booking_tool.py` uses "Habitación" - same room type!

---

## Implementation Plan

### Phase 0: Room Capacity Validation (NEW - CRITICAL)

**Goal:** Add capacity validation to prevent overbooking.

**Location:** `app/booking_tool.py`

#### 0.1 Add `ROOM_CAPACITY` Constant

**Placement:** After `EL_SALVADOR_HOLIDAYS` (line 39), before "Shared Normalization" comment (line 85)

```python
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
```

#### 0.2 Update `_normalize_bungalow_type()` to Handle "Doble"

**Location:** `booking_tool.py` line 190

The existing function doesn't handle "Doble" as an alias for "Habitación". Add 'doble':

```python
# BEFORE (line 190):
elif any(keyword in bungalow_type_lower for keyword in ['habitacion', 'habitación', 'room', 'hab']):

# AFTER:
elif any(keyword in bungalow_type_lower for keyword in ['habitacion', 'habitación', 'room', 'hab', 'doble']):
```

#### ⚠️ 0.2.1 CRITICAL: Also Update Inline Normalization in `_validate_booking_info()`

**BUG IDENTIFIED:** `_validate_booking_info()` has INLINE normalization that runs BEFORE our capacity validation. If we only update `_normalize_bungalow_type()`, "Doble" will be REJECTED at line 751 before reaching capacity validation!

**Location 1:** `booking_tool.py` line 739 (first inline normalization)

```python
# BEFORE (line 739):
elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower:

# AFTER:
elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower or "doble" in bungalow_type_lower:
```

**Location 2:** `booking_tool.py` line 812 (second inline normalization)

```python
# BEFORE (line 812):
elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower:

# AFTER:
elif "habitación" in bungalow_type_lower or "habitacion" in bungalow_type_lower or "doble" in bungalow_type_lower:
```

**Why Both Locations?**
- Line 739: First normalization pass (for all bookings)
- Line 812: Second normalization pass (inside else block after Pasadía check)
- Both must handle "doble" or bookings with `bungalow_type="Doble"` will fail!

#### 0.3 Create `_validate_room_capacity()` Function

**Placement:** After `_normalize_bungalow_type()` function (around line 197)

**⚠️ IMPORTANT:** Uses existing `_normalize_bungalow_type()` for robustness - handles "bungalow familiar", typos, etc.

```python
def _validate_room_capacity(
    bungalow_type: str,
    adults: int,
    children_0_5: int,  # Count as 0 for occupancy
    children_6_10: int  # Count as 0.5 for occupancy
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
```

#### 0.4 Integrate Capacity Validation into `_validate_booking_info()`

**Location:** `booking_tool.py` line ~836 (AFTER guest count validation at line 831-836, BEFORE payment method validation at line 838)

**⚠️ CRITICAL:** Must be AFTER bungalow type normalization (lines 801-819) so we have the normalized type!

**Note:** Since `_validate_room_capacity()` now uses `_normalize_bungalow_type()` internally, it will handle any bungalow_type format passed to it.

```python
# ADD after guest count validation (line 836), before payment method validation (line 838):

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
```

#### 0.5 Note: Leveraging Existing Code

**Existing `_normalize_bungalow_type()` function:** `booking_tool.py` lines 169-196

This function is now used by `_validate_room_capacity()` for robustness.

**⚠️ Code Duplication Found (3 places):**
1. Lines 169-196: Shared `_normalize_bungalow_type()` ← **The one we use**
2. Lines 727-740: Inline normalization in `_validate_booking_info()` - DUPLICATE
3. Lines 801-819: Second inline normalization in `_validate_booking_info()` - DUPLICATE

**Optional cleanup (not required for Phase 0):** Refactor `_validate_booking_info()` to use the shared function instead of duplicate inline normalization. This would reduce ~40 lines of redundant code.

#### 0.6 Test Cases for Phase 0

| Test | Input | Expected Result |
|------|-------|-----------------|
| Familiar min valid | 5 adults, 0 children | ✅ Valid (occupancy=5) |
| Familiar min invalid | 4 adults, 0 children | ❌ Invalid - min 5 required |
| Familiar max valid | 6 adults, 4 children_6_10 | ✅ Valid (occupancy=8) |
| Familiar max invalid | 7 adults, 4 children_6_10 | ❌ Invalid - max 8 exceeded (occupancy=9) |
| Junior valid | 2 adults, 0 children | ✅ Valid (occupancy=2) |
| Junior max invalid | 9 adults, 0 children | ❌ Invalid - max 8 exceeded |
| Habitación valid | 2 adults, 2 children_6_10 | ✅ Valid (occupancy=3) |
| Habitación max invalid | 4 adults, 2 children_6_10 | ❌ Invalid - max 4 exceeded (occupancy=5) |
| Matrimonial valid | 2 adults, 0 children | ✅ Valid (occupancy=2) |
| Matrimonial invalid | 3 adults, 0 children | ❌ Invalid - max 2 exceeded |
| Children 0-5 don't count | 2 adults, 5 children_0_5, 0 children_6_10 | ✅ Valid for Junior (occupancy=2) |
| Doble alias works | "Doble", 2 adults | ✅ Valid - alias for Habitación |
| Normalization works | "bungalow familiar", 5 adults | ✅ Valid - normalizes to Familiar |
| Typo handling | "junor", 2 adults | ✅ Valid - normalizes to Junior |

#### 0.7 Files Affected by Phase 0

| File | Change | Lines |
|------|--------|-------|
| `app/booking_tool.py` | Add `ROOM_CAPACITY` constant | After line 39 (after `EL_SALVADOR_HOLIDAYS`) |
| `app/booking_tool.py` | Update `_normalize_bungalow_type()` to add 'doble' | Line 190 |
| `app/booking_tool.py` | ⚠️ Update inline normalization #1 to add 'doble' | Line 739 (first inline normalization) |
| `app/booking_tool.py` | ⚠️ Update inline normalization #2 to add 'doble' | Line 812 (second inline normalization) |
| `app/booking_tool.py` | Add `_validate_room_capacity()` function | After line 197 (after `_normalize_bungalow_type()`) |
| `app/booking_tool.py` | Add `_suggest_room_for_group()` function | After `_validate_room_capacity()` |
| `app/booking_tool.py` | Integrate into `_validate_booking_info()` | Between lines 836-838 (after guest count, before payment) |

**⚠️ CRITICAL:** All 3 normalization locations (lines 190, 739, 812) must be updated for 'doble' or "Doble" bookings will fail!

#### 0.8 Impact on Existing Functionality

- **`make_booking()`**: Will now fail early if capacity violated (better UX)
- **No breaking changes**: Adds validation, doesn't change existing behavior for valid bookings
- **Multi-room booking**: Phase 1's `_get_multiple_rooms()` will use this validation per room

**Estimated Time:** 0.5 days

---

### Phase 1: Room Selection Enhancement (PREREQUISITE)

**Goal:** Get multiple available rooms for a multi-room request WITH capacity validation.

**Location:** `app/booking_tool.py`

#### 1.0 Placement Overview

| Function | Placement | After |
|----------|-----------|-------|
| `_get_multiple_rooms()` | ~line 200 | `_validate_room_capacity()` and `_suggest_room_for_group()` |
| `_revalidate_multi_room_availability()` | ~line 250 | `_get_multiple_rooms()` |
| `_select_room()` modification | line 892 (signature) + 939-941 (filter) | After building `available_room_numbers` list |

#### ⚠️ CRITICAL: Two Different Availability Functions Exist!

| Function | Location | Returns | Use For Multi-Room? |
|----------|----------|---------|---------------------|
| `_check_room_availability()` | `booking_tool.py` | `{"success": True, "rooms": {"1": "24", "2": "25"}}` | ✅ YES - returns actual room numbers |
| `check_room_availability()` | `database_client.py` | `{'bungalow_familiar': 'Available'}` | ❌ NO - only category-level |

**BUG HISTORY**: In Nov 2025, `compraclick_tool.py` incorrectly checked for `availability.get("available_rooms", [])` which doesn't exist in either function's return format, causing false "no availability" errors.

**RULE**: Always use `_check_room_availability()` from `booking_tool.py` for booking operations. Access rooms via `result["rooms"].values()`.

#### 1.1 Create `_get_multiple_rooms()` Function

```python
async def _get_multiple_rooms(
    check_in_date: str,
    check_out_date: str,
    room_requests: List[Dict[str, Any]],  # [{bungalow_type: "Junior", adults: 2, children_0_5: 0, children_6_10: 1}, ...]
    package_type: str = "Las Hojas"  # NEW: Package type for all rooms (passed from make_multi_room_booking)
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
            normalized_type,  # Use normalized type
            package_type,  # Use the package_type parameter (NOT from request!)
            excluded_rooms=excluded
        )
        
        if not selected:
            return {
                "success": False,
                "error": f"No available {bungalow_type} rooms (need {len(room_requests) - i} more)",
                "partial_rooms": selected_rooms,
                "customer_message": f"No hay suficientes habitaciones {bungalow_type} disponibles."
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
```

#### 1.2 Create `_revalidate_multi_room_availability()` Function

```python
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
```

#### 1.3 Modify `_select_room()` to Accept `excluded_rooms`

**Location:** `booking_tool.py`
- Function signature: line 892
- Add filter: between lines 939-941 (after logger statements, before Pasadía check at line 941)

```python
# STEP 1: Modify function signature (line 892)
# BEFORE:
def _select_room(available_rooms: dict, bungalow_type: str, package_type: str) -> Optional[str]:

# AFTER:
def _select_room(
    available_rooms: dict, 
    bungalow_type: str, 
    package_type: str,
    excluded_rooms: List[str] = None  # NEW parameter
) -> Optional[str]:


# STEP 2: Add filter AFTER line 939 (after both logger statements)
# and BEFORE line 941 ("# Special case: Pasadía package...")
#
# Context:
# Line 938: logger.info(f"[ROOM_DEBUG] Available room numbers: {available_room_numbers}")
# Line 939: logger.info(f"[ROOM_DEBUG] Room number to index mapping: {room_number_to_index}")
# >>> INSERT HERE <<<
# Line 941: # Special case: Pasadía package can only use Pasadía

    # NEW: Filter out already-selected rooms to prevent duplicates in multi-room booking
    if excluded_rooms:
        # Handle mixed types: available_room_numbers contains int (24) and str ("10A")
        excluded_set = set(str(x) for x in excluded_rooms)
        available_room_numbers = [
            r for r in available_room_numbers 
            if str(r) not in excluded_set
        ]
        logger.info(f"[ROOM_DEBUG] After excluding {excluded_rooms}: {available_room_numbers}")
```

#### 1.4 Test Cases for Phase 1

| Test | Input | Expected Result |
|------|-------|-----------------|
| Single room available | 1 Junior request, rooms [24,25] available | ✅ Returns room 24 or 25 |
| Multiple rooms same type | 3 Junior requests, rooms [24,25,26,27] available | ✅ Returns 3 different rooms |
| Not enough rooms | 3 Junior requests, rooms [24,25] available | ❌ Fails on 3rd request |
| Mixed types | 1 Junior + 1 Familiar, both available | ✅ Returns 1 Junior + 1 Familiar |
| Capacity validation first | 10 adults in Junior (max 8) | ❌ Fails before availability check |
| Re-validation race | Room 24 selected, then becomes unavailable | ❌ Re-validation fails, retry |
| Excluded rooms work | excluded=[24], available=[24,25] | ✅ Returns 25 (not 24) |

#### 1.5 Leveraging Existing Code Patterns

**Pattern from `_make_booking_with_validation_and_retry()` (lines 999-1122):**
- Re-validates availability before booking (line 1028)
- Retry loop with max 3 attempts
- Selects alternative room if current unavailable

**How Phase 1 leverages this:**
- `_revalidate_multi_room_availability()` follows same pattern but for multiple rooms
- Uses same `_check_room_availability()` function
- Uses same `_select_room()` function (with new `excluded_rooms` param)

**Note:** The existing function has a potential bug at line 1040 - checks `selected_room not in current_available_rooms` but the dict has indices as keys, not room numbers. Our new `_revalidate_multi_room_availability()` correctly uses `.values()`.

#### 1.6 Files Affected by Phase 1

| File | Change | Lines |
|------|--------|-------|
| `app/booking_tool.py` | Add `_get_multiple_rooms()` function | After Phase 0 functions (~200 post-Phase 0) |
| `app/booking_tool.py` | Add `_revalidate_multi_room_availability()` function | After `_get_multiple_rooms()` |
| `app/booking_tool.py` | Modify `_select_room()` signature | Line 892 (add `excluded_rooms` param) |
| `app/booking_tool.py` | Add excluded_rooms filter in `_select_room()` | Between lines 939-941 (before Pasadía check) |

**Note:** Line numbers for new functions are approximate because Phase 0 adds ~80 lines before them.

**Estimated Time:** 0.5 days

---

### Phase 2: Multi-Room Booking Function

**Goal:** Create the main multi-room booking function.

**Location:** `app/booking_tool.py`

**Placement:** After `_revalidate_multi_room_availability()` (around line 300)

#### 2.1 Create `make_multi_room_booking()` Function

```python
async def make_multi_room_booking(
    customer_name: str,
    email: str,
    phone_number: str,
    city: str,
    dui_passport: str,
    nationality: str,
    check_in_date: str,
    check_out_date: str,
    room_bookings: List[Dict[str, Any]],  # [{bungalow_type, adults, children_0_5, children_6_10}, ...]
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
        room_bookings: List of room configurations, each with:
            - bungalow_type: "Junior", "Familiar", "Matrimonial", "Habitación"
            - adults: Number of adults
            - children_0_5: Children 0-5 years
            - children_6_10: Children 6-10 years
        ... other standard booking parameters ...
    
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
    
    # Extract phone number from wa_id if not provided (same as make_booking)
    if not phone_number or phone_number.strip() == '':
        phone_number = _extract_phone_from_wa_id(wa_id)
        logger.info(f"[MULTI_ROOM] Phone number extracted from wa_id {wa_id}: {phone_number}")
    
    try:
        # NOTE: Time validation is handled by assistant via check_office_status()
        # before calling this function. This matches the pattern in make_booking().
        current_time = datetime.now(EL_SALVADOR_TZ)
        logger.debug(f"[MULTI_ROOM] Current El Salvador time: {current_time}")
        
        # Step 0: Validate customer info (reuse logic from _validate_booking_info)
        # For multi-room, we validate common info + each room's capacity (via _get_multiple_rooms)
        if not all([customer_name, email, city, dui_passport, nationality, 
                    check_in_date, check_out_date, package_type, payment_method, payment_maker_name]):
            return {
                "success": False,
                "error": "Missing required booking information",
                "customer_message": "Falta información requerida para completar la reserva."
            }
        
        if "@" not in email or "." not in email:
            return {
                "success": False,
                "error": "Invalid email format",
                "customer_message": "El formato del correo electrónico no es válido."
            }
        
        # Validate dates (same logic as _validate_booking_info)
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
        
        selected_rooms = room_result["rooms"]  # ["24", "25", "26", "27"]
        room_details = room_result["room_details"]
        
        # Step 2: Reserve payment (full amount for all rooms)
        # Must handle BOTH payment methods like make_booking does
        if payment_method == "Depósito BAC" and transfer_id:
            from .bank_transfer_tool import reserve_bank_transfer
            reserve_result = reserve_bank_transfer(int(transfer_id), payment_amount)
            if not reserve_result.get("success"):
                return {
                    "success": False,
                    "error": "Payment reservation failed",
                    "customer_message": reserve_result.get("message", "Error al reservar el pago.")
                }
        elif payment_method == "CompraClick" and authorization_number:
            from .compraclick_tool import reserve_compraclick_payment
            reserve_result = await reserve_compraclick_payment(authorization_number, payment_amount)
            if not reserve_result.get("success"):
                return {
                    "success": False,
                    "error": "CompraClick reservation failed",
                    "customer_message": "Error al procesar el pago CompraClick."
                }
        
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
        reserverooms = "+".join(selected_rooms)
        adult_counts = [str(r["adults"]) for r in room_details]
        adultcount = "+".join(adult_counts)
        total_children_0_5 = sum(r["children_0_5"] for r in room_details)
        total_children_6_10 = sum(r["children_6_10"] for r in room_details)
        
        # Step 5: Build accommodation description for mixed-type bookings
        # Group room types for display
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
            bungalow_type=primary_bungalow_type,  # Now handles mixed types
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
        except Exception as e:
            logger.error(f"[MULTI_ROOM] Payment record update failed: {e}")
        
        # Step 8: Success response
        total_adults = sum(r["adults"] for r in room_details)
        total_children = total_children_0_5 + total_children_6_10
        
        logger.info(f"[MULTI_ROOM] Booking successful, reserva: {reserva}")
        return {
            "success": True,
            "reserva": reserva,
            "rooms_booked": selected_rooms,
            "total_rooms": len(selected_rooms),
            "customer_message": f"""¡Excelente! Su reserva de {len(selected_rooms)} habitaciones ha sido confirmada exitosamente. 🎉

📋 **Detalles de la Reserva:**
• Código: {reserva}
• Habitaciones: {', '.join(selected_rooms)}
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
```

#### 2.2 Leveraging Existing Code from `make_booking()`

**Pattern reuse from `make_booking()` (lines 333-550):**

| Pattern | `make_booking()` Line | `make_multi_room_booking()` |
|---------|----------------------|----------------------------|
| Phone extraction | 408-411 | ✅ Added (same pattern) |
| Try-except wrapper | 413, 544 | ✅ Added (same pattern) |
| Email validation | Via `_validate_booking_info()` | ✅ Added inline |
| Payment reservation | 462-495 | ✅ Reused same logic |
| Payment record update | 517-523 | ✅ Reuses `_update_payment_record()` |
| Success logging | 529 | ✅ Added |

**Functions reused directly:**
- `_extract_phone_from_wa_id()` - Phone extraction
- `_get_multiple_rooms()` - Uses `_check_room_availability()` + `_select_room()`
- `_revalidate_multi_room_availability()` - Uses `_check_room_availability()`
- `_update_payment_record()` - Same as single booking
- `reserve_bank_transfer()` / `reserve_compraclick_payment()` - Same as single booking

#### 2.3 Placement Overview for Phase 2

| Function | Placement | After |
|----------|-----------|-------|
| `make_multi_room_booking()` | After Phase 1 functions (~300 post-Phase 0/1) | `_revalidate_multi_room_availability()` |
| `_make_multi_room_api_call()` | Between lines 1463-1466 | `_make_booking_api_call()`, before `_update_payment_record()` |

**Note:** Line numbers for `make_multi_room_booking()` are approximate because Phases 0-1 add ~130 lines.

**Estimated Time:** 1 day

---

#### 2.4 Create `_make_multi_room_api_call()` Function

**Placement:** After `_make_booking_api_call()` (ends at line 1463), before `_update_payment_record()` (starts at line 1466)

```python
async def _make_multi_room_api_call(
    customer_name: str,
    email: str,
    phone_number: str,
    city: str,
    dui_passport: str,
    nationality: str,
    check_in_date: str,
    check_out_date: str,
    reserverooms: str,  # "24+25+26+27"
    adultcount: str,    # "2+3+2+4"
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
        "reserverooms": reserverooms,  # MULTI-ROOM FORMAT: "24+25+26+27"
        "ciudad": city,
        "checkIn": check_in_formatted,
        "checkOut": check_out_formatted,
        "acomodacion": acomodacion_desc,
        "adultcount": adultcount,  # MULTI-ROOM FORMAT: "2+3+2+4"
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
        "cardnumer": "0",  # Added: matches _make_booking_api_call
        "duedate": "0",    # Added: matches _make_booking_api_call
        "comment": commenthotel,  # Added: matches _make_booking_api_call
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
```

#### 2.5 Test Cases for Phase 2

| Test | Input | Expected Result |
|------|-------|-----------------|
| 2-room booking success | 2 Junior rooms, valid payment | ✅ Returns reserva code, 2 rooms booked |
| 4-room booking success | 4 mixed room types | ✅ Returns reserva, "24+25+1+2" format |
| Bank transfer reservation | Depósito BAC, valid transfer_id | ✅ Payment reserved before API call |
| CompraClick reservation | CompraClick, valid auth | ✅ Payment reserved before API call |
| Re-validation fails once | Room 24 taken after selection | ✅ Retry with different room |
| All rooms unavailable | No rooms left after re-validation | ❌ Fails with "no disponibles" message |
| API returns error | API status 500 | ❌ Fails with error message |
| Payment reservation fails | Invalid transfer_id | ❌ Fails before API call |
| Phone extraction | phone_number='', wa_id='50378001234' | ✅ Extracts '78001234' |
| Missing email | email='' | ❌ Fails validation |
| Invalid email format | email='notanemail' | ❌ Fails validation |
| Unexpected exception | DB connection error | ❌ Caught by try-except, returns error |

#### 2.6 Files Affected by Phase 2

| File | Change | Lines |
|------|--------|-------|
| `app/booking_tool.py` | Add `make_multi_room_booking()` | After Phase 1 functions (~300 post-Phase 0/1) |
| `app/booking_tool.py` | Add `_make_multi_room_api_call()` | Between lines 1463-1466 (before `_update_payment_record`) |

**Note:** 
- `make_multi_room_booking()`: ~220 lines of code
- `_make_multi_room_api_call()`: ~180 lines of code
- Total Phase 2 additions: ~400 lines

**Estimated Time:** 1.5 days total (2.1 + 2.4)

---

### Phase 3: OpenAI Assistant Integration

**Goal:** Add tool definition for the assistant to use.

**Location:** `app/openai_agent.py`

#### ⚠️ CRITICAL: Tool Definition Format

This codebase uses the **OpenAI Responses API** format (FLAT), NOT Chat Completions API format (nested).

| Format | Structure | Used In |
|--------|-----------|---------|
| ❌ Chat Completions | `{"type": "function", "function": {"name": ...}}` | NOT this codebase |
| ✅ Responses API | `{"type": "function", "name": ...}` | This codebase |

#### 3.1 Add Tool Definition

**Placement:** After `make_booking` tool (ends at line 739), before `send_email` (starts at line 740)

**NOTE:** Uses FLAT format matching existing tools. Mirrors `make_booking` anti-placeholder warnings.

**Comparison with `make_booking` tool:**
| Aspect | `make_booking` | `make_multi_room_booking` |
|--------|---------------|---------------------------|
| Single room params | `adults`, `children_0_5`, `children_6_10`, `bungalow_type` | Replaced by `room_bookings` array |
| Package types | Includes "Pasadía" | Excludes "Pasadía" (day pass is per-person) |
| Required fields | 17 | 14 (room_bookings replaces 4 single-room fields) |

```python
    {
        "type": "function",
        "name": "make_multi_room_booking",
        "description": "Book multiple rooms in a single transaction. Use when customer needs 2+ rooms. All rooms are booked atomically - all succeed or all fail. CRITICAL: This tool can ONLY be used AFTER payment proof has been verified. All booking information must be explicitly provided by the customer - NEVER use placeholders.",
        "parameters": {
            "type": "object",
            "properties": {
                "customer_name": {
                    "type": "string",
                    "description": "Full name of the primary guest (must be explicitly provided, not inferred)"
                },
                "email": {
                    "type": "string",
                    "description": "Customer's email address. NEVER use placeholders like 'NO_PROVIDED', 'N/A', etc. If missing, DO NOT call make_multi_room_booking - ask customer first."
                },
                "phone_number": {
                    "type": "string",
                    "description": "Customer's phone number. For WhatsApp (WATI) users, use 'AUTO' and it will be extracted from waId. For Facebook/Instagram users, this MUST be explicitly asked from the customer and provided here (cannot be inferred)."
                },
                "city": {
                    "type": "string",
                    "description": "Customer's city of origin. NEVER use placeholders like 'SIN_DATO', 'NO_PROVIDED', 'N/A', 'Unknown', etc. If missing, DO NOT call make_multi_room_booking - ask customer first."
                },
                "dui_passport": {
                    "type": "string",
                    "description": "Customer's DUI or passport number. NEVER use placeholders like 'SIN_DATO', 'NO_PROVIDED', 'N/A', 'Unknown', etc. If missing, DO NOT call make_multi_room_booking - ask customer first."
                },
                "nationality": {
                    "type": "string",
                    "description": "Customer's nationality. NEVER use placeholders like 'SIN_DATO', 'NO_PROVIDED', 'N/A', 'Unknown', etc. If missing, DO NOT call make_multi_room_booking - ask customer first."
                },
                "check_in_date": {
                    "type": "string",
                    "description": "Check-in date in YYYY-MM-DD format (must be explicitly provided)"
                },
                "check_out_date": {
                    "type": "string",
                    "description": "Check-out date in YYYY-MM-DD format (must be explicitly provided)"
                },
                "room_bookings": {
                    "type": "array",
                    "description": "Array of room configurations. Each room must have explicit guest counts.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "bungalow_type": {
                                "type": "string",
                                "enum": ["Junior", "Familiar", "Matrimonial", "Habitación"],
                                "description": "Type of room (must be explicitly provided). NOTE: Pasadía not supported for multi-room."
                            },
                            "adults": {
                                "type": "integer",
                                "description": "Number of adults for this room (must be explicitly provided)"
                            },
                            "children_0_5": {
                                "type": "integer",
                                "description": "Number of children aged 0-5 years old (FREE - no package needed). Use 0 if none. CRITICAL: This field is for infants/toddlers who are FREE. Do NOT put 'paquete niño' purchases here."
                            },
                            "children_6_10": {
                                "type": "integer",
                                "description": "Number of children aged 6-10 years old who have PAID child packages ('paquete niño'). Use 0 if none. CRITICAL: When customer purchases 'paquete niño' or 'paquetes de niño', put the count HERE (not in children_0_5). This is the field that charges the child rate."
                            }
                        },
                        "required": ["bungalow_type", "adults", "children_0_5", "children_6_10"]
                    }
                },
                "package_type": {
                    "type": "string",
                    "enum": ["Las Hojas", "Escapadita", "Romántico"],
                    "description": "Package type for all rooms (must be explicitly provided). NOTE: Pasadía not supported for multi-room."
                },
                "payment_method": {
                    "type": "string",
                    "enum": ["CompraClick", "Depósito BAC"],
                    "description": "Payment method: CompraClick or Depósito BAC (determined from payment verification)"
                },
                "payment_amount": {
                    "type": "number",
                    "description": "Actual total amount paid by customer for ALL rooms (from payment verification). May be partial payment, deposit, or full payment."
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
                    "description": "Total number of extra beds requested across all rooms (0 if none). CRITICAL: Follow extra bed policy from system instructions."
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
            "required": [
                "customer_name", "email", "phone_number", "city", "dui_passport", "nationality",
                "check_in_date", "check_out_date", "room_bookings",
                "package_type", "payment_method", "payment_amount", "payment_maker_name", "wa_id"
            ]
        }
    },
```

#### 3.2 Add Function Mapping

**Location:** `app/openai_agent.py` in the `available_functions` dict

```python
# In available_functions dict:
"make_multi_room_booking": booking_tool.make_multi_room_booking
```

#### 3.3 Files Affected by Phase 3

| File | Change | Lines |
|------|--------|-------|
| `app/openai_agent.py` | Add tool definition to `tools` list | Between lines 739-740 (`make_booking` end at 739, `send_email` start at 740) |
| `app/openai_agent.py` | Add to `available_functions` dict | After line 1410 (`make_booking` entry), before line 1411 (`send_email`) |

#### 3.4 Leveraging Existing Code

**Pattern reuse from `make_booking` tool:**
- Same anti-placeholder warnings for email, city, dui_passport, nationality
- Same phone_number "AUTO" instruction for WATI users
- Same children field explanations (0-5 free, 6-10 charged)
- Same extra_beds and extra_beds_cost descriptions
- Same customer_instructions description

**No code duplication:** The tool definition just maps to the new `booking_tool.make_multi_room_booking` function.

#### 3.5 Verification Notes

- ✅ `booking_tool` already imported at line 20: `from app import booking_tool, email_service`
- ✅ Tool format uses FLAT structure (Responses API), not nested (Chat Completions)
- ✅ Placement after `make_booking` keeps similar tools together
- ✅ `available_functions` already has pattern: `"make_booking": booking_tool.make_booking`
- ⚠️ Pasadía excluded from multi-room (day pass is per-person, not per-room)

**Estimated Time:** 0.5 days

---

### Phase 4: System Instructions Update

**Location:** `app/resources/system_instructions_new.txt`

The assistant first loads DECISION_TREE, MODULE_DEPENDENCIES, and CORE_CONFIG to decide which modules to load. We need to add multi-room booking entries to all three.

---

#### 4.1 Add to DECISION_TREE (PRIORITY_3_SALES_INTENTS section)

**Location:** After `wants_to_book` intent (~line 168)

```json
"multi_room_booking": {
    "intent": "Customer EXPLICITLY requests multiple rooms OR group size exceeds capacity of ALL CURRENTLY AVAILABLE room types",
    "semantic_triggers": [
        "Customer explicitly says 'dos habitaciones', 'multiple rooms', '2 bungalows', etc.",
        "After checking availability: group size exceeds capacity of ALL available room types",
        "Customer asks to split group across separate rooms"
    ],
    "⚠️ BUSINESS_RULE": "We charge per PACKAGE (per person), NOT per room. NEVER proactively suggest multi-room - only offer when: (1) customer explicitly requests it, OR (2) after checking availability, group exceeds capacity of ALL available room types.",
    "⚠️ AVAILABILITY_FIRST": "MUST check room availability BEFORE suggesting multi-room. If only Habitación (max 4) is available, a group of 5 needs multi-room even though Junior (max 8) could fit them.",
    "⚠️ CAPACITY_RULES": "Familiar (min 5, max 8), Junior (min 2, max 8), Habitación (min 2, max 4), Matrimonial (min 2, max 2). Occupancy = adults + (children_6_10 × 0.5).",
    "action": "Check availability → if group fits in ANY available room type, use single room → if not, ask distribution → validate each room → calculate total → single payment → make_multi_room_booking",
    "module": "MODULE_2B_PRICE_INQUIRY.multi_room_booking_protocol",
    "🚨 CRITICAL": "NEVER call make_booking multiple times. MUST use make_multi_room_booking for atomic transaction."
}
```

---

#### 4.2 Add to MODULE_DEPENDENCIES (PRIORITY_3_SALES section)

**Location:** After `wants_to_book` entry (~line 520)

```json
"multi_room_booking": {
    "load": [
        "MODULE_2B_PRICE_INQUIRY.multi_room_booking_protocol",
        "MODULE_2C_AVAILABILITY"
    ],
    "tools": [
        "get_price_for_date",
        "check_room_availability",
        "make_multi_room_booking"
    ],
    "⚠️ BUSINESS_RULE": "Charge is per PACKAGE (person), NOT per room. Only use multi-room when: (1) customer explicitly requests it, OR (2) after checking availability, group exceeds capacity of ALL available room types.",
    "⚠️ AVAILABILITY_FIRST": "Check availability BEFORE suggesting multi-room. Example: If only Habitación (max 4) available, group of 5 needs multi-room.",
    "⚠️ CAPACITY_VALIDATION": "Each room MUST meet min/max occupancy: Familiar (5-8), Junior (2-8), Habitación (2-4), Matrimonial (2-2).",
    "🚨 CRITICAL": "NEVER use make_booking multiple times. All rooms MUST be booked in single atomic transaction.",
    "🚨 BLOCKER": "Accommodation → '¿es socio?' FIRST. Check availability. Then ask guest distribution if multi-room needed."
}
```

---

#### 4.3 Add to CORE_CONFIG (🚨 UNIVERSAL_SAFETY_PROTOCOLS section)

**Location:** After existing safety protocols (~line 902)

```json
"MULTI_ROOM_BOOKING_SAFETY": {
    "rule": "When customer needs 2+ rooms, MUST use make_multi_room_booking tool. NEVER call make_booking multiple times.",
    "rationale": "API handles all rooms in single atomic transaction - all succeed or all fail.",
    "⚠️ BUSINESS_RULE": "We charge per PACKAGE (per person), NOT per room. NEVER proactively suggest multi-room. Only use when: (1) customer EXPLICITLY requests multiple rooms, OR (2) after checking availability, group exceeds capacity of ALL AVAILABLE room types.",
    "⚠️ AVAILABILITY_FIRST": "MUST check availability BEFORE suggesting multi-room. If only Habitación (max 4) available, a group of 5 needs multi-room even though Junior could fit them theoretically.",
    "workflow": [
        "1. Ask '¿es socio?' if accommodation",
        "2. Check room availability for requested dates",
        "3. Determine if group fits in ANY available room type",
        "4. IF customer explicitly asks for multi-room OR group exceeds ALL available room capacities → proceed with multi-room",
        "5. Ask guest distribution: '¿Cómo desean distribuirse?'",
        "6. Validate each room meets min/max occupancy rules",
        "7. Calculate TOTAL price for all rooms",
        "8. Process SINGLE payment for total amount",
        "9. Call make_multi_room_booking with room_bookings array",
        "10. Confirm ALL rooms in single message"
    ],
    "🚨 FORBIDDEN": [
        "make_booking called multiple times for same customer's multi-room request",
        "Proactively suggesting multi-room to customers (we charge per person, not per room!)",
        "Suggesting multi-room before checking what room types are actually available"
    ]
}
```

---

#### 4.4 Add Multi-Room Protocol to MODULE_2B_PRICE_INQUIRY

**Location:** After `quote_generation_protocol` section

```json
"multi_room_booking_protocol": {
    "⚠️ BUSINESS_RULE": "We charge per PACKAGE (per person), NOT per room. NEVER proactively suggest multi-room booking!",
    "trigger": "ONLY when: (1) Customer EXPLICITLY requests 2+ rooms, OR (2) After checking availability, group exceeds capacity of ALL AVAILABLE room types",
    "semantic_detection": {
        "explicit_only": "Customer directly says 'dos habitaciones', 'multiple rooms', '2 bungalows', etc.",
        "capacity_overflow_after_availability": "After check_room_availability: group exceeds max capacity of ALL available room types for those dates"
    },
    "⚠️ AVAILABILITY_FIRST": "MUST check availability BEFORE suggesting multi-room. Example: If only Habitación (max 4) available for those dates, a group of 5 needs multi-room.",
    "⚠️ NEVER_DO": "Do NOT suggest multi-room without first checking availability. A group of 5 fits in Junior (max 8) but needs multi-room if only Habitación (max 4) is available.",
    "capacity_rules": {
        "Familiar": {"min": 5, "max": 8, "note": "Rooms 1-17. Groups <5 NOT allowed"},
        "Junior": {"min": 2, "max": 8, "note": "Rooms 18-59. Default for most bookings"},
        "Habitación": {"min": 2, "max": 4, "note": "Rooms 1A-14A (also called Doble)"},
        "Matrimonial": {"min": 2, "max": 2, "note": "Rooms 22,42,47,48,53. Couples only"}
    },
    "occupancy_formula": "occupancy = adults + (children_6_10 × 0.5). Children 0-5 count as 0.",
    "workflow": {
        "step_1_socio_check": "If accommodation (not Pasadía), ask '¿es socio?' first",
        "step_2_check_availability": "Call check_room_availability to see what room types are available for those dates",
        "step_3_evaluate_fit": "Check if group fits in ANY available room type. If yes → single room booking. If no → proceed with multi-room",
        "step_4_distribution": "If multi-room needed: Ask '¿Cómo desean distribuirse en las habitaciones?'",
        "step_5_validate_capacity": "Ensure EACH room meets min/max occupancy rules before proceeding",
        "step_6_calculate": "Calculate TOTAL price for all rooms using get_price_for_date for each configuration",
        "step_7_quote": "Present single combined quote with breakdown per room",
        "step_8_payment": "Process SINGLE payment for TOTAL amount",
        "step_9_booking": "Use make_multi_room_booking with room_bookings array - NEVER make_booking multiple times",
        "step_10_confirm": "Confirm ALL rooms in single success message"
    },
    "room_bookings_format": {
        "description": "Array of room configurations for make_multi_room_booking",
        "example": [
            {"bungalow_type": "Junior", "adults": 2, "children_0_5": 0, "children_6_10": 0},
            {"bungalow_type": "Junior", "adults": 3, "children_0_5": 1, "children_6_10": 0},
            {"bungalow_type": "Familiar", "adults": 5, "children_0_5": 0, "children_6_10": 2}
        ]
    },
    "example_conversations": {
        "scenario_1_single_room_available": {
            "context": "5 adults, Junior/Familiar available - fits in one room, do NOT suggest multi-room",
            "customer": "Queremos hacer una reserva para el 15 de marzo, somos 5 adultos",
            "assistant_step_1": "¡Con gusto! Para brindarle la tarifa correcta, ¿es socio de nuestro club?",
            "customer_response": "No soy socio",
            "assistant_checks_availability": "[Calls check_room_availability - Junior and Familiar available]",
            "assistant_correct": "¡Perfecto! Para 5 adultos les recomiendo un Bungalow Familiar (capacidad 5-8 personas). [Quotes single room price]",
            "⚠️ WRONG_RESPONSE": "Do NOT suggest multi-room - they fit in available Familiar!"
        },
        "scenario_2_only_small_rooms_available": {
            "context": "5 adults, but ONLY Habitación (max 4) available - MUST suggest multi-room",
            "customer": "Queremos reservar para el 20 de marzo, somos 5 adultos",
            "assistant_step_1": "¡Con gusto! Para brindarle la tarifa correcta, ¿es socio de nuestro club?",
            "customer_response": "No soy socio",
            "assistant_checks_availability": "[Calls check_room_availability - only Habitación available (max 4)]",
            "assistant_correct": "Para esas fechas solo tenemos disponibles Habitaciones (capacidad máxima 4 personas). Para 5 adultos necesitarán 2 habitaciones. ¿Cómo prefieren distribuirse?",
            "customer_response_2": "3 y 2 está bien",
            "assistant_continues": "[Validates each room meets min/max (2-4), calculates total, quotes, payment, make_multi_room_booking]"
        },
        "scenario_3_exceeds_all_capacity": {
            "context": "10 adults exceeds ALL room types (max 8) - MUST suggest multi-room",
            "customer": "Somos 10 adultos, queremos reservar para el 25 de marzo",
            "assistant_step_1": "¡Con gusto! Para brindarle la tarifa correcta, ¿es socio de nuestro club?",
            "customer_response": "No soy socio",
            "assistant_checks_availability": "[Calls check_room_availability - Familiar and Junior available]",
            "assistant_correct": "Para 10 adultos necesitarán más de una habitación ya que la capacidad máxima por habitación es de 8 personas. ¿Cómo prefieren distribuirse? Por ejemplo: 2 Bungalows Familiar (5+5)",
            "customer_response_2": "5-5 está bien",
            "assistant_continues": "[Validates, calculates, quotes, payment, make_multi_room_booking]"
        },
        "scenario_4_explicit_request": {
            "context": "Customer explicitly asks for multiple rooms (even if they'd fit in one)",
            "customer": "Necesito 2 bungalows Junior para un viaje familiar, somos 4 adultos",
            "assistant": "¡Con gusto! Aunque 4 adultos caben en un solo Junior, entiendo que prefieren 2 habitaciones. [Asks socio → checks availability → asks distribution → validates capacity → calculates → quotes → payment → make_multi_room_booking]"
        }
    },
    "🚨 CRITICAL_RULES": {
        "single_transaction": "All rooms MUST be booked in ONE make_multi_room_booking call",
        "atomic_operation": "API handles all-or-nothing - all rooms succeed or all fail",
        "no_partial": "NEVER allow partial bookings - if one room fails, entire booking fails",
        "single_payment": "Customer pays TOTAL amount once, not per room"
    }
}
```

---

**Estimated Time:** 1 day (increased due to multiple section updates)

---

## Summary of Changes

### Functions to Add (6 total)

| Function | Location | Purpose |
|----------|----------|---------|
| `_validate_room_capacity()` | booking_tool.py | Validate guest count vs room capacity |
| `_suggest_room_for_group()` | booking_tool.py | Suggest appropriate room type for group |
| `_get_multiple_rooms()` | booking_tool.py | Get available rooms for multi-room request |
| `_revalidate_multi_room_availability()` | booking_tool.py | Re-validate rooms before API call (prevents race conditions) |
| `make_multi_room_booking()` | booking_tool.py | Main multi-room booking function |
| `_make_multi_room_api_call()` | booking_tool.py | API call with + delimiter format |

### Functions to Modify (2 total)

| Function | Location | Change |
|----------|----------|--------|
| `_select_room()` | booking_tool.py | Add `excluded_rooms` parameter |
| `_validate_booking_info()` | booking_tool.py | Add capacity validation call |

### Files Modified (4 total)

| File | Changes |
|------|---------|
| `booking_tool.py` | Add 6 functions, modify 2 (capacity validation + multi-room + re-validation) |
| `openai_agent.py` | Add 1 tool definition + 1 mapping |
| `system_instructions_new.txt` | Add 4 entries: DECISION_TREE, MODULE_DEPENDENCIES, CORE_CONFIG, MODULE_2B |
| `smart_availability.py` | Optional: Add check_multi_room_availability |

---

## Implementation Timeline

| Day | Phase | Tasks |
|-----|-------|-------|
| 1 | Phase 0 | `_validate_room_capacity()`, `_suggest_room_for_group()`, integrate into `_validate_booking_info()` |
| 1-2 | Phase 1 | `_get_multiple_rooms()`, modify `_select_room()` |
| 2-3 | Phase 2 | `make_multi_room_booking()`, `_make_multi_room_api_call()` |
| 4 | Phase 3 | OpenAI tool definition and mapping |
| 4-5 | Phase 4 | System instructions (DECISION_TREE, MODULE_DEPENDENCIES, CORE_CONFIG, MODULE_2B) |
| 6-7 | Testing | End-to-end testing, capacity validation, edge cases |

**Total: 7 days** (vs 11 days in old plan)

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| API format change | API is documented and tested |
| Room selection conflicts | `excluded_rooms` prevents duplicates |
| Payment atomicity | API handles in single transaction |
| Capacity overbooking | `_validate_room_capacity()` blocks invalid requests |
| Rollback | Simply remove new tool from assistant |

**Overall Risk: LOW** ✅

---

## Benefits of New Approach

1. **Atomic Transactions**: API handles all-or-nothing natively
2. **Simpler Code**: 70% less code than wrapper pattern
3. **Faster Implementation**: 6-7 days vs 11 days
4. **No Complex State**: No tracking of partial bookings
5. **Single API Call**: Better performance and reliability
6. **Easier Testing**: One endpoint to test

---

## Test Cases

### Test 1: Basic Multi-Room (Same Type)
```python
result = await make_multi_room_booking(
    customer_name="Juan Pérez",
    room_bookings=[
        {"bungalow_type": "Junior", "adults": 2, "children_0_5": 0, "children_6_10": 0},
        {"bungalow_type": "Junior", "adults": 2, "children_0_5": 0, "children_6_10": 0}
    ],
    # ... other params
)
assert result["success"] == True
assert result["total_rooms"] == 2
assert len(result["rooms_booked"]) == 2
```

### Test 2: Mixed Room Types
```python
result = await make_multi_room_booking(
    room_bookings=[
        {"bungalow_type": "Junior", "adults": 2, "children_0_5": 0, "children_6_10": 0},
        {"bungalow_type": "Familiar", "adults": 4, "children_0_5": 1, "children_6_10": 1}
    ],
    # ... other params
)
assert result["success"] == True
```

### Test 3: Different PAX per Room
```python
result = await make_multi_room_booking(
    room_bookings=[
        {"bungalow_type": "Junior", "adults": 2, ...},
        {"bungalow_type": "Junior", "adults": 3, ...},
        {"bungalow_type": "Junior", "adults": 4, ...}
    ],
    # ...
)
# Verify adultcount = "2+3+4"
```

### Test 4: Insufficient Rooms
```python
# Request more rooms than available
result = await make_multi_room_booking(
    room_bookings=[...] * 50,  # 50 rooms
    # ...
)
assert result["success"] == False
assert "no hay suficientes" in result["customer_message"].lower()
```

---

## Revision History

- v2.0 (Nov 28, 2025): Complete rewrite based on native API multi-room support
  - Deprecated wrapper pattern approach
  - Simplified to 3 new functions (vs 9)
  - Implementation time reduced from 11 to 6-7 days
  - API handles atomicity natively
- v2.1 (Nov 28, 2025): Enhanced tool definition and Phase 3
  - Added anti-placeholder warnings to tool definition (matching make_booking)
  - Added phone_number, wa_id, extra_beds, extra_beds_cost parameters
  - Expanded Phase 3 to include all base module updates:
    - 3.1: DECISION_TREE entry for multi_room_booking intent
    - 3.2: MODULE_DEPENDENCIES entry with tools list
    - 3.3: CORE_CONFIG safety protocol (MULTI_ROOM_BOOKING_SAFETY)
    - 3.4: MODULE_2B_PRICE_INQUIRY.multi_room_booking_protocol
  - Changed detection from keyword-matching to semantic intent + proactive anticipation
- v2.2 (Nov 28, 2025): Added Room Capacity Validation (CRITICAL)
  - **Gap Identified**: Current codebase has NO capacity validation
  - Added new Phase 0: Room Capacity Validation
  - New functions: `_validate_room_capacity()`, `_suggest_room_for_group()`
  - Integration into `_validate_booking_info()` for single bookings
  - Integration into `_get_multiple_rooms()` for multi-room bookings
  - Room capacity constraints documented:
    - Matrimonial: max 2 adults
    - Junior/Habitación: max 4 adults, occupancy 5
    - Familiar: max 6 adults, occupancy 8
  - Updated function count: 5 new + 2 modified
  - Updated timeline: 7 days total
- v2.3 (Nov 28, 2025): Added Room Availability Re-validation
  - Added `_revalidate_multi_room_availability()` function
  - Mirrors single-booking `_make_booking_with_revalidation()` logic
  - Prevents race conditions between room selection and API call
  - If rooms become unavailable, automatically retries with different rooms
  - Updated function count: 6 new + 2 modified
- v2.4 (Nov 29, 2025): Added Critical Function Warning (Bug Prevention)
  - Documented TWO different availability functions exist:
    - `_check_room_availability()` in `booking_tool.py` → returns actual room numbers ✅
    - `check_room_availability()` in `database_client.py` → returns only category-level ❌
  - Added BUG HISTORY: Nov 2025 compraclick_tool.py bug where `availability.get("available_rooms", [])` was checked but key didn't exist
  - Added explicit RULE: Always use `_check_room_availability()` and access via `result["rooms"].values()`
- v2.5 (Nov 29, 2025): Aligned Room Capacity with system_instructions_new.txt
  - **MAJOR FIX**: Previous capacity constraints were WRONG
  - Corrected to match `system_instructions_new.txt` lines 1300-1302:
    - Familiar: min 5, max 8 (was: max 8 only, missing min)
    - Junior: min 2, max 8 (was: max 5 - WRONG!)
    - Habitación/Doble: min 2, max 4 (was: max 5 - WRONG!)
    - Matrimonial: min 2, max 2 (correct)
  - Fixed occupancy formula: `adults + (children_6_10 × 0.5)`
    - Children 0-5 count as **0** (not 0.5!)
  - Added minimum occupancy validation (Familiar requires 5+ people)
  - Documented naming: "Doble" in instructions = "Habitación" in booking_tool.py
- v2.6 (Nov 29, 2025): Deep Analysis & Fix of Phase 0
  - **Fixed line number references**:
    - Was: "line ~730 (after date validation)" ❌
    - Now: "line ~773 (after bungalow normalization)" ✅
  - Added "Doble" alias to ROOM_CAPACITY (same as Habitación)
  - Improved customer messages to differentiate min vs max violations
  - Added exact placement instructions for all new code
  - Added 0.4: Note about existing `_normalize_bungalow_type()` function
  - Added 0.5: Test cases table (12 test scenarios)
  - Added 0.6: Affected files summary
  - Added 0.7: Impact on existing functionality analysis
- v2.7 (Nov 29, 2025): Deep Analysis & Fix of Phase 1
  - **Fixed duplicate "Phase 1" header**: Renamed second Phase 1 to Phase 2
  - **Fixed wrong function reference**: 
    - Was: `_is_booking_time_allowed()` ❌
    - Now: Time validation handled by assistant (matches `make_booking()` pattern)
  - **Fixed step numbering**: Steps 1-5 (was 1-6 with wrong time validation)
  - Added 1.0: Placement overview table for all Phase 1 functions
  - Fixed `_select_room()` modification:
    - Signature change: line 829
    - Filter addition: line 875 (after building available_room_numbers)
    - Handle mixed types (int 24 and str "10A")
  - Added 1.4: Test cases (7 scenarios)
  - Added 1.5: Affected files summary
- v2.8 (Nov 29, 2025): Deep Analysis & Fix of Phase 2
  - **Fixed section numbering**: 1.2 → 2.3
  - **Fixed Phase numbering cascade**:
    - "Phase 2: OpenAI Assistant Integration" → Phase 3
    - "Phase 3: System Instructions Update" → Phase 4
    - All 3.x sections → 4.x
  - **Added missing CompraClick handling**: 
    - Was: Only bank transfer reservation
    - Now: Both `reserve_bank_transfer()` AND `reserve_compraclick_payment()`
  - **Fixed `reserve_bank_transfer` call**: Added `int(transfer_id)` conversion
  - **Fixed response parsing**: Replaced `if 'response_data' in dir()` with proper try/except
  - Added 2.2: Placement overview for Phase 2 functions
  - Added 2.4: Test cases (8 scenarios)
  - Added 2.5: Affected files summary
  - Added 3.3: Affected files for Phase 3
- v2.9 (Nov 29, 2025): Deep Analysis & Fix of Phase 3
  - **CRITICAL FIX: Tool definition format**:
    - Was: Nested `{"type": "function", "function": {...}}` (Chat Completions API) ❌
    - Now: Flat `{"type": "function", "name": ...}` (Responses API) ✅
  - **Fixed line number references**:
    - Tool definition: ~line 1700 → after line 738 (after `make_booking`)
    - `available_functions`: ~line 2400 → line 1408
  - Added format comparison table (Responses API vs Chat Completions)
  - Added 3.4: Verification notes
  - Verified `booking_tool` already imported at line 20
  - Added note: Pasadía excluded from multi-room (day pass is per-person)
- v2.10 (Nov 29, 2025): Deep Analysis & Fix of Phase 0 (Leveraging Existing Code)
  - **Added 0.2**: Update `_normalize_bungalow_type()` to handle "Doble" (add 'doble' keyword)
  - **Modified 0.3 `_validate_room_capacity()`**: Now uses existing `_normalize_bungalow_type()` for robustness
    - Handles "bungalow familiar", typos like "junor", etc.
  - **Documented triple normalization duplication**:
    - Lines 123-150: Shared function ← The one we use
    - Lines 669-682: Inline duplicate #1
    - Lines 738-756: Inline duplicate #2
  - Added 2 new test cases: Normalization robustness, typo handling
  - Renumbered sections: 0.1-0.8
  - Updated files affected to include `_normalize_bungalow_type()` update
- v2.11 (Nov 29, 2025): Deep Analysis & Fix of Phase 1 (Leveraging Existing Code)
  - **Added normalization in `_get_multiple_rooms()`**:
    - Now calls `_normalize_bungalow_type()` before `_select_room()`
    - Note that `_validate_room_capacity()` already normalizes internally
  - **Simplified revalidation room check**:
    - Was: Complex ternary with `isdigit()` check
    - Now: Simple `str(v)` conversion for consistent comparison
  - **Documented existing code pattern reuse**:
    - `_make_booking_with_validation_and_retry()` (lines 936-1059) as reference
    - Same `_check_room_availability()` and `_select_room()` functions
  - **Noted potential bug in existing code** (line 977):
    - Checks room number in dict keys instead of values
    - Our new function correctly uses `.values()`
  - Added section 1.5: Leveraging Existing Code Patterns
  - Renumbered 1.5 Files Affected → 1.6
- v2.12 (Nov 29, 2025): Deep Analysis & Fix of Phase 2 (Leveraging Existing Code)
  - **Added missing phone extraction** (like `make_booking` line 351-353):
    - `if not phone_number: phone_number = _extract_phone_from_wa_id(wa_id)`
  - **Added try-except wrapper** (like `make_booking` lines 355, 486):
    - All booking logic now wrapped in try block
    - Catches unexpected exceptions with customer-friendly message
  - **Added basic validation** (inline version of `_validate_booking_info` patterns):
    - Required fields check
    - Email format validation
  - **Fixed indentation** for entire function body
  - **Documented pattern reuse** from `make_booking()`:
    - Payment reservation, payment record update, phone extraction
    - Functions: `_extract_phone_from_wa_id`, `_update_payment_record`
  - Added section 2.2: Leveraging Existing Code from `make_booking()`
  - Added 4 new test cases: phone extraction, validation, exception handling
  - Renumbered sections: 2.2 → 2.3, 2.3 → 2.4, 2.4 → 2.5, 2.5 → 2.6
- v2.13 (Nov 29, 2025): Deep Analysis & Fix of Phase 3 (Leveraging Existing Code)
  - **Clarified line numbers**:
    - Tool: Between line 737 (end of `make_booking`) and 738 (start of `send_email`)
    - Functions: After line 1408 (`make_booking`), before 1409 (`send_email`)
  - **Added comparison table** with `make_booking` tool:
    - Documents what fields are replaced by `room_bookings` array
    - Notes Pasadía exclusion
  - **Added section 3.4**: Leveraging Existing Code
    - Pattern reuse from `make_booking` tool (anti-placeholder warnings, phone AUTO, children fields)
  - Renumbered 3.4 Verification Notes → 3.5
- v2.14 (Nov 29, 2025): Final Review of Phase 0 (Line Number Verification)
  - **Fixed ROOM_CAPACITY placement**:
    - Was: "around line 35 (after `logger`)"
    - Now: "After line 37 (after `EL_SALVADOR_HOLIDAYS`), before line 39"
  - **Removed redundant "Doble" entry** from ROOM_CAPACITY:
    - Since `_validate_room_capacity()` normalizes first, "Doble" → "Habitación"
    - Having "Doble" in ROOM_CAPACITY was unreachable code
    - Added comment explaining normalization handles Doble
  - **Updated Files Affected table** with precise line numbers:
    - Integration point: Between lines 773-775 (verified)
- v2.15 (Nov 29, 2025): Final Review of Phase 1 (Line Number Verification)
  - **Fixed excluded_rooms filter placement**:
    - Was: "line 875"
    - Now: "between lines 876-877 (after both logger statements, before Pasadía check at line 878)"
    - Added context showing surrounding code
  - **Updated Files Affected table**:
    - New functions: Noted as "post-Phase 0" since Phase 0 adds ~80 lines
    - Filter: Precisely "between lines 876-877"
  - **Verified `_select_room()` return value**:
    - Returns actual room number (e.g., "24", "10A"), not API index
    - Correct for multi-room `reserverooms` format ("24+25+26")
- v2.16 (Nov 29, 2025): Final Review of Phase 2 (Line Number Verification)
  - **Fixed `_make_multi_room_api_call()` placement**:
    - Was: "around line 1350"
    - Now: "Between lines 1366-1369 (after `_make_booking_api_call`, before `_update_payment_record`)"
  - **Updated Placement Overview table**:
    - Added note about ~130 lines added by Phases 0-1
  - **Added code size estimates**:
    - `make_multi_room_booking()`: ~220 lines
    - `_make_multi_room_api_call()`: ~180 lines
    - Total Phase 2: ~400 lines
  - **Verified rate calculation approach**:
    - Simplified calculation acceptable for multi-room (API just needs display values)
    - payment_amount already validated from payment verification
- v2.17 (Dec 1, 2025): Re-verification of Phase 2 (Line Number Shift)
  - **Updated `_make_multi_room_api_call()` placement** due to codebase changes:
    - Was: "Between lines 1366-1369"
    - Now: "Between lines 1400-1403"
    - `_make_booking_api_call()` now ends at line 1400
    - `_update_payment_record()` now starts at line 1403
  - **Updated Files Affected table** with correct line numbers
- v2.18 (Dec 1, 2025): Critical Business Rule Fix for Phase 4 (Multi-Room Triggers)
  - **⚠️ BUSINESS RULE ADDED**: We charge per PACKAGE (per person), NOT per room
  - **REMOVED proactive multi-room suggestion** - assistant should NEVER suggest multi-room unless:
    1. Customer EXPLICITLY requests multiple rooms, OR
    2. Group size exceeds capacity of ALL CURRENTLY AVAILABLE room types
  - **⚠️ AVAILABILITY_FIRST**: Must check room availability BEFORE suggesting multi-room
    - Example: Group of 5 fits in Junior (max 8), but needs multi-room if only Habitación (max 4) available
  - **Updated sections**:
    - 4.1 DECISION_TREE: Added AVAILABILITY_FIRST rule, triggers based on available rooms not theoretical max
    - 4.2 MODULE_DEPENDENCIES: Added AVAILABILITY_FIRST, blocker updated to check availability first
    - 4.3 CORE_CONFIG: Added AVAILABILITY_FIRST, workflow starts with availability check, new FORBIDDEN item
    - 4.4 multi_room_booking_protocol: Added steps for check_availability and evaluate_fit before suggesting multi-room
  - **Fixed example conversations** (4 scenarios):
    - scenario_1: 5 adults with Familiar available → single room (do NOT suggest multi)
    - scenario_2: 5 adults but ONLY Habitación (max 4) available → MUST suggest multi-room
    - scenario_3: 10 adults exceeds all capacities → MUST suggest multi-room
    - scenario_4: Customer explicitly requests multiple rooms
  - **Fixed capacity numbers**: Familiar/Junior max is 8, not 4-6
- v2.19 (Dec 1, 2025): Re-verification of Phase 3 (Line Number Shift)
  - **Updated tool definition placement** due to codebase changes:
    - Was: "Between lines 737-738"
    - Now: "Between lines 739-740" (`make_booking` ends at 739, `send_email` starts at 740)
  - **Updated function mapping placement**:
    - Was: "After line 1408, before line 1409"
    - Now: "After line 1410 (`make_booking`), before line 1411 (`send_email`)"
- v2.20 (Dec 1, 2025): Critical Bug Fix for Phase 0 ("Doble" Alias)
  - **⚠️ BUG IDENTIFIED**: Adding 'doble' only to `_normalize_bungalow_type()` would cause "Doble" bookings to FAIL
  - **Root Cause**: `_validate_booking_info()` has INLINE normalization at lines 681 and 749 that runs BEFORE capacity validation
  - **Failure path**: "Doble" → inline normalization doesn't recognize → rejected at line 751 as "Invalid bungalow type"
  - **Fix Added**: Section 0.2.1 requires updating inline normalization at lines 681 and 749
  - **All 3 locations must be updated**: Line 144 (`_normalize_bungalow_type`), Line 681, Line 749
  - **Updated Files Affected table** to include the two inline normalization fixes
- v2.21 (Dec 1, 2025): Bug Fix for Phase 1 (`package_type` Parameter)
  - **⚠️ BUG IDENTIFIED**: `_get_multiple_rooms()` was trying to get `package_type` from room request dict
  - **Root Cause**: `request.get("package_type", "Las Hojas")` always returned "Las Hojas" because `package_type` is a top-level parameter, not per-room
  - **Impact**: For non-"Las Hojas" packages, wrong package_type would be passed to `_select_room()`
  - **Fix Applied**:
    - Added `package_type` parameter to `_get_multiple_rooms()` function signature
    - Updated `_select_room()` call to use parameter instead of `request.get()`
    - Updated both calls to `_get_multiple_rooms()` in Phase 2 to pass `package_type`
  - **Also Discovered**: Pre-existing bug in `_make_booking_with_validation_and_retry()` at line 977 - checks room in dict keys instead of values (outside Phase 1 scope)
- v2.22 (Dec 1, 2025): Bug Fixes for Phase 2 (Validation & Payload)
  - **⚠️ BUG 1: Missing Date Validation**
    - `make_multi_room_booking()` only validated email, NOT dates
    - Added date validation (check_out > check_in, format validation) - same logic as `_validate_booking_info()`
  - **⚠️ BUG 2: Missing API Payload Fields**
    - `_make_multi_room_api_call()` was missing: `cardnumer`, `duedate`, `comment`
    - Added these fields to match `_make_booking_api_call()` structure
  - **⚠️ BUG 3: Mixed-Type Booking Display**
    - Was using only first room's type for `bungalow_type` parameter
    - For "1 Familiar + 2 Junior" booking, would only show "Familiar"
    - Fixed: Now generates "2x Junior, 1x Familiar" format for mixed bookings
    - Updated `_make_multi_room_api_call()` to handle mixed-type description in `acomodacion`
  - **Step numbering updated**: Steps 5-8 (was 5-7)
- v2.23 (Dec 1, 2025): Phase 0 Line Number Updates (User Changes to `_validate_booking_info`)
  - **User modified** `_validate_booking_info()` with granular missing fields detection (~10 lines added)
  - **Line shifts in Phase 0**:
    - Second inline normalization: 749 → 754
    - Bungalow type normalization block: 738-756 → 743-761
    - Capacity validation insertion: 773-775 → 778-780
  - **Updated sections**: 0.2.1, 0.4, 0.5, 0.7
  - **No functional changes**: Just line number corrections to match current codebase
- v2.24 (Dec 1, 2025): Phase 1 Line Number Updates (Same User Changes)
  - **Line shifts in Phase 1** (all +5 from user's changes):
    - `_select_room()` signature: 829 → 834
    - Logger statements: 875-876 → 880-881
    - Pasadía check: 878 → 883
    - Filter insertion: 876-877 → 881-883
    - `_make_booking_with_validation_and_retry()`: 936-1059 → 941-1064
    - Bug at line: 977 → 982
  - **Updated sections**: 1.0, 1.3, 1.5, 1.6
  - **No functional changes**: Just line number corrections
- v2.25 (Dec 1, 2025): Phase 2 Line Number Updates (Same User Changes)
  - **Line shifts in Phase 2** (all +5 from user's changes):
    - `_make_booking_api_call()` ends: 1400 → 1405
    - `_update_payment_record()` starts: 1403 → 1408
    - `_make_multi_room_api_call()` insertion: 1400-1403 → 1405-1408
  - **Updated sections**: 2.3, 2.4, 2.6
  - **`make_booking()` lines 287-492 unchanged** (changes were after this function)
- v2.26 (Dec 3, 2025): Major Line Number Updates (Duplicate Booking Prevention Added)
  - **User added ~46 lines** of duplicate booking prevention logic at start of booking_tool.py
  - **Phase 0 shifts**:
    - `EL_SALVADOR_HOLIDAYS`: 37 → 39
    - `_normalize_bungalow_type()`: 144 → 190
    - First inline normalization: 681 → 739
    - Second inline normalization: 754 → 812
    - Capacity integration: 778-780 → 836-838
  - **Phase 1 shifts**:
    - `_select_room()`: 834 → 892
    - Filter insertion: 881-883 → 939-941
    - `_make_booking_with_validation_and_retry()`: 941-1064 → 999-1122
  - **Phase 2 shifts**:
    - `_make_booking_api_call()` ends: 1405 → 1463
    - `_update_payment_record()` starts: 1408 → 1466
    - `make_booking()`: 287 → 333
  - **Updated sections**: 0.1, 0.2, 0.2.1, 0.3, 0.4, 0.5, 0.7, 1.0, 1.3, 1.5, 1.6, 2.2, 2.3, 2.4, 2.6
