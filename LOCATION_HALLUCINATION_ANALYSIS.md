# CRITICAL BUG: Assistant Hallucinating Hotel Location

## Incident Summary
**Date**: November 15, 2025, 19:24:17 UTC  
**Customer**: 50372952818  
**Request**: "Hola puede mandarme el link de su dirección para llegar"

## What The Assistant Said (FABRICATED):
```
Aquí está la ubicación de Las Hojas Resort & Beach Club:

Cantón Las Hojas, Km 43 1/2, carretera a Playa Las Hojas, San Pedro Masahuat, La Paz, El Salvador

Puede encontrarlo en Google Maps aquí: https://www.google.com/maps/search/?api=1&query=13.3437,-89.0054 🌴
```

## Correct Information (from system_instructions_new.txt line 1800-1802):
```json
"location": {
  "hotel_address": "Playa Las Hojas, municipio de San Pedro Masahuat, departamento de La Paz",
  "hotel_pinlocation": "13.36374943855788, -89.05427073252258"
}
```

**CORRECT:**
- Address: "Playa Las Hojas, municipio de San Pedro Masahuat, departamento de La Paz"
- Latitude: 13.36374943855788
- Longitude: -89.05427073252258

**FABRICATED:**
- Address: "Cantón Las Hojas, Km 43 1/2, carretera a Playa Las Hojas, San Pedro Masahuat, La Paz, El Salvador"
- Latitude: 13.3437 (wrong!)
- Longitude: -89.0054 (wrong!)

---

## Root Cause Analysis

### 1. Tool Called with Fabricated Data

**Log Evidence**:
```
INFO:app.openai_agent:[Tool] Round 1 - Requested: send_location_pin 
with call_id: call_yxDo91t5WIhdAcKVz0mniIBe 
args={
  "latitude": 13.3437,          ❌ WRONG (should be 13.36374943855788)
  "longitude": -89.0054,        ❌ WRONG (should be -89.05427073252258)
  "name": "Las Hojas Resort & Beach Club",
  "address": "Cantón Las Hojas, Km 43 1/2, carretera a Playa Las Hojas, San Pedro Masahuat, La Paz, El Salvador"  ❌ FABRICATED
}
```

### 2. Module Not Loaded

**Log Evidence**:
```
INFO:app.openai_agent:[MODULE_OPTIMIZATION] Skipping base_modules at message 10 - using cached from previous_response_id
```

**Problem**: 
- Assistant used CACHED modules from a previous response
- Did NOT call `load_additional_modules(["MODULE_4_INFORMATION"])`
- MODULE_4_INFORMATION contains the correct location data
- Without loading it, the assistant had NO access to the correct information
- So it HALLUCINATED the coordinates and address

### 3. No Intent Mapping for Location Request

**Current mapping** (line 218):
```json
"location_directions_inquiry": {
  "intent": "Location/directions/how to get there/access routes",
  "module": "MODULE_4_INFORMATION.hotel_information.location.access_route_protocol"
}
```

**Problem**: The mapping points to a NESTED path that doesn't trigger module loading correctly.

**Priority 5 mapping** (line 289, 298):
```json
"wants_location_info": {"load": ["MODULE_4_INFORMATION.contact_info", "MODULE_4_INFORMATION.location"]},
"location_directions_inquiry": {"load": "MODULE_4_INFORMATION"}
```

But the assistant didn't recognize the customer's message as matching either intent!

---

## Why Coordinates Are Wrong

**Customer's actual coordinates**: 13.3437, -89.0054 (fabricated)
**Correct coordinates**: 13.36374943855788, -89.05427073252258

**Distance difference**: ~3.3 km (~2 miles) away!

This could send customers to the WRONG location, causing:
- Lost customers
- Frustration and complaints
- Negative reviews
- Lost bookings

---

## The `send_location_pin` Tool

**Location**: `openai_agent.py` lines 974-977

```python
def format_location_as_text(latitude: float, longitude: float, name: str, address: str) -> str:
    """Formats location data into a user-friendly text message with a Google Maps link."""
    maps_url = f"https://www.google.com/maps/search/?api=1&query={latitude},{longitude}"
    return f"Aquí está la ubicación de {name}:\n\n{address}\n\nPuede encontrarlo en Google Maps aquí: {maps_url}"
```

**Tool Description** (line 400):
```json
"description": "Formats the business location details into a text message with a Google Maps link. 
CRITICAL: You MUST include the exact output returned by this function in your response to the user 
- do not create your own location text."
```

**The tool expects 4 parameters**:
1. latitude (float)
2. longitude (float)
3. name (string)
4. address (string)

**But WHERE should these values come from?**
→ FROM MODULE_4_INFORMATION.location (which wasn't loaded!)

---

## Impact Assessment

### Severity: **CRITICAL (P0)**

**Customer Impact**:
1. **Wrong GPS coordinates** → Customer goes to wrong place
2. **Gets lost** → Frustration and delays
3. **Misses check-in time** → Possibly loses reservation
4. **Negative experience** → Bad reviews, complaints
5. **Lost booking** → Customer may cancel

**Business Impact**:
- Revenue loss from cancelled bookings
- Reputational damage
- Customer service escalations
- Potential liability if customer has accident driving to wrong location

### Scope
**All location requests** are at risk of receiving fabricated coordinates and addresses.

---

## Similar Pattern to Bank Account Hallucination

| **Issue** | **Bank Account** | **Location** |
|-----------|------------------|--------------|
| **Symptom** | Fabricated bank details | Fabricated address & coordinates |
| **Root Cause** | MODULE_2B_PRICE_INQUIRY not loaded | MODULE_4_INFORMATION not loaded |
| **Why** | Broken intent mapping | Module optimization skipped loading |
| **Tool Called** | None (just responded) | send_location_pin (with wrong data) |
| **Result** | Invented account details | Invented coordinates & address |

**Pattern**: When assistant doesn't load required modules, it HALLUCINATES instead of loading them first.

---

## Required Fixes

### Fix 1: Add Location Hallucination Prohibition

**Location**: CORE_CONFIG.CRITICAL_PROHIBITIONS (after line 359)

**Add**:
```json
"location_hallucination": "🚨 BLOCKING: Hotel location details (address, coordinates, Google Maps link) MUST come EXCLUSIVELY from MODULE_4_INFORMATION.location. The ONLY correct information is: Address: 'Playa Las Hojas, municipio de San Pedro Masahuat, departamento de La Paz', Coordinates: 13.36374943855788, -89.05427073252258. When customer asks for location/directions/address ('dónde están', 'dirección', 'ubicación', 'cómo llegar', 'link de ubicación'), you MUST: (1) Load MODULE_4_INFORMATION if not already loaded, (2) Call send_location_pin with EXACT values from MODULE_4_INFORMATION.location.hotel_address and hotel_pinlocation. NEVER invent, approximate, or remember different coordinates or addresses. Providing wrong location = customers get LOST and may miss check-in. This is NON-NEGOTIABLE."
```

### Fix 2: Add Specific Location Request Intent

**Location**: DECISION_TREE.PRIORITY_5_INFORMATION (after line 298)

**Enhance**:
```json
"location_directions_inquiry": {
  "intent": "Customer requests hotel location, address, GPS coordinates, or directions: 'dónde están', 'dirección', 'ubicación', 'cómo llegar', 'puede mandarme el link', 'link de su dirección', 'coordenadas'",
  "load": "MODULE_4_INFORMATION",
  "action": "Load MODULE_4_INFORMATION.location and call send_location_pin with exact hotel_address and hotel_pinlocation values",
  "🚨 CRITICAL": "NEVER call send_location_pin without loading MODULE_4_INFORMATION first. NEVER invent coordinates or address. Use ONLY hotel_address and hotel_pinlocation from loaded module."
}
```

### Fix 3: Add Dependency Chain for Location Requests

**Location**: DEPENDENCY_CHAINS (after line 318)

**Add**:
```json
"location_request": {
  "auto_load": ["MODULE_4_INFORMATION.location"],
  "required_fields": ["hotel_address", "hotel_pinlocation"],
  "tools": ["send_location_pin"],
  "🚨 VALIDATION": "Before calling send_location_pin, verify MODULE_4_INFORMATION.location loaded with hotel_address and hotel_pinlocation. Missing any field = BLOCK + load module immediately. Extract latitude and longitude from hotel_pinlocation (format: 'lat, lon'). NEVER use approximate or cached coordinates."
}
```

### Fix 4: Enhance Tool Description

**Location**: openai_agent.py line 400

**Current**:
```json
"description": "Formats the business location details into a text message with a Google Maps link. 
CRITICAL: You MUST include the exact output returned by this function in your response to the user 
- do not create your own location text."
```

**Enhanced**:
```json
"description": "Formats the business location details into a text message with a Google Maps link. 
CRITICAL PROTOCOL: (1) Load MODULE_4_INFORMATION if not already loaded. (2) Extract hotel_address from MODULE_4_INFORMATION.location.hotel_address. 
(3) Extract coordinates from MODULE_4_INFORMATION.location.hotel_pinlocation (format: 'latitude, longitude'). 
(4) Call this function with EXACT values from module - NEVER approximate, round, or invent coordinates. 
(5) Include the exact output in your response. 
Correct values: hotel_address='Playa Las Hojas, municipio de San Pedro Masahuat, departamento de La Paz', 
latitude=13.36374943855788, longitude=-89.05427073252258"
```

---

## Testing Requirements

### Test Case 1: Direct Location Request
**Input**: "Puede mandarme el link de su dirección para llegar"  
**Expected**:
1. Load MODULE_4_INFORMATION
2. Call send_location_pin with:
   - latitude: 13.36374943855788
   - longitude: -89.05427073252258
   - name: "Las Hojas Resort & Beach Club"
   - address: "Playa Las Hojas, municipio de San Pedro Masahuat, departamento de La Paz"
3. Provide Google Maps link: https://www.google.com/maps/search/?api=1&query=13.36374943855788,-89.05427073252258

**Fail**: Any other coordinates or address

### Test Case 2: Variations
**Inputs**:
- "Dónde están ubicados?"
- "Cuál es su dirección?"
- "Cómo llego al hotel?"
- "Ubicación del resort"

**Expected**: Same as Test Case 1 for all variations

### Test Case 3: Module Not Cached
**Simulate**: First message in conversation (no cached modules)  
**Expected**: Load MODULE_4_INFORMATION, then call tool with correct values

### Test Case 4: Access Route Question
**Input**: "Cuál es el mejor camino para llegar?"  
**Expected**: 
1. Load MODULE_4_INFORMATION
2. Provide access_route_protocol.script
3. Also send location pin with correct coordinates

---

## Monitoring

After fix deployment, monitor for:
1. All location requests → Verify coordinates are 13.36374943855788, -89.05427073252258
2. send_location_pin calls → Check parameters match MODULE_4_INFORMATION
3. Customer complaints about "can't find hotel"
4. MODULE_4_INFORMATION loading → Ensure it loads before send_location_pin

**Alert threshold**: ANY instance of wrong coordinates = P0 escalation

---

## Related Issues

1. **Bank Account Hallucination** → Same root cause (module not loaded)
2. **Same-Day Booking Blockage** → Module loading issue
3. **Large Group Accommodation** → Module optimization causing issues

**Common theme**: Module optimization/caching is causing the assistant to skip loading critical information, leading to hallucinations.

---

## Priority

**Priority**: **P0 - Critical**  
**Urgency**: Immediate (customers getting wrong directions)  
**Complexity**: Low (add prohibitions and enhance intent)  
**Risk**: Very low (adding safeguards, not changing behavior)

---

## Files to Modify

1. `/home/robin/watibot4/app/resources/system_instructions_new.txt`
   - Add location_hallucination prohibition
   - Enhance location_directions_inquiry intent
   - Add location_request dependency chain

2. `/home/robin/watibot4/app/openai_agent.py` (optional)
   - Enhance send_location_pin tool description

---

## Summary

The assistant fabricated hotel location details (wrong coordinates and made-up address) because it didn't load MODULE_4_INFORMATION which contains the correct information. This is the same pattern as the bank account hallucination bug.

**Solution**: Add prohibitions and dependency chains to ensure MODULE_4_INFORMATION is ALWAYS loaded before providing location details, and NEVER allow the assistant to invent coordinates or addresses.
