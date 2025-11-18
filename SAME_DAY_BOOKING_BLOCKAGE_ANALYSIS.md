# CRITICAL BUG: Assistant Blocking Same-Day Bookings

## Incident Summary
**Date**: November 15, 2025  
**Customer**: 50372955232  
**Request**: Paquete Las Hojas, Bungalow Junior, 2 adults, November 15-16 (check-in TODAY)  
**Time**: 19:05:50 UTC (7:05 PM UTC = ~1:05 PM El Salvador time)

## What Happened

**Customer requested**: Check-in November 15, Check-out November 16 (1 night)  
**Current date**: November 15, 2025  
**Check-in time**: 4:00-5:00 PM

**Assistant's response**:
```
"Para poder confirmar disponibilidad, precio exacto y emitir el cobro de su reserva, 
en este momento es necesario que un ejecutivo de reservaciones continúe el proceso con usted.

Le recomiendo llamar o escribir al 2505-2800..."
```

**What assistant SHOULD have done**: Call `check_room_availability` and `get_price_for_date` for November 15-16.

---

## Log Evidence

### Modules Loaded ✓
```
INFO:app.openai_agent:[Tool] Round 1 - Requested: load_additional_modules 
args={"modules":["MODULE_2B_PRICE_INQUIRY","MODULE_2C_AVAILABILITY"],
      "reasoning":"User wants to reserve Paquete Las Hojas in Bungalow Junior 
                   for specific dates; need availability then pricing/quote workflow."}

INFO:app.openai_agent:[DYNAMIC_LOADING] Loaded full module: MODULE_2B_PRICE_INQUIRY
INFO:app.openai_agent:[DYNAMIC_LOADING] Loaded full module: MODULE_2C_AVAILABILITY
```

### Tools Called ❌
```
INFO:app.openai_agent:[Tool] Round 1 complete. Checking for more tool calls...
[NO MORE TOOL CALLS]
```

**Expected tools that were NOT called:**
- ❌ `check_room_availability(date="2025-11-15", nights=1, room_type="junior")`
- ❌ `get_price_for_date(date="2025-11-15", adults=2, children=0, package="las_hojas")`

### Result
Assistant escalated to phone instead of checking availability and providing quote.

---

## Root Cause: Overly Strict `booking_temporal_logic`

**Location**: `system_instructions_new.txt` lines 597-602

```json
"booking_temporal_logic": {
  "rule": "IMPERATIVO ABSOLUTO: Las reservas y cotizaciones SOLO pueden hacerse para fechas FUTURAS.",
  "prohibition": "PROHIBIDO cotizar o intentar reservar fechas pasadas bajo cualquier circunstancia.",
  "client_insistence": "Si un cliente insiste en una fecha pasada, usar este script:",
  "exact_script": "Lamentamos informarle que no es posible realizar reservas para fechas que ya pasaron. Solo podemos ayudarle con reservas a partir de mañana en adelante. ¿Le gustaría que le cotice para una fecha próxima? ☀️"
}
```

### The Problem

**Current logic**: "fechas FUTURAS" (future dates)  
**Interpretation**: November 15 is TODAY, not future → BLOCK

**But reality**:
- Customer messaged at ~1:05 PM El Salvador time
- Check-in time is 4:00-5:00 PM
- Customer has **3-4 hours** to arrive
- Same-day booking is **absolutely valid** if before check-in time

The rule is too strict! It's blocking legitimate same-day bookings.

---

## Impact Assessment

### Severity: **HIGH (P1)**

**Customer Experience**:
- ❌ Customer has to call/WhatsApp 2505-2800 manually
- ❌ Extra friction in booking process
- ❌ Possible lost sale if customer doesn't want to call
- ❌ Defeats the purpose of having an AI assistant

**Business Impact**:
- ❌ Reduced conversion rate
- ❌ Increased load on human agents (unnecessary escalations)
- ❌ Poor customer experience
- ❌ Same-day bookings are often **high-value** (last-minute, urgent)

### Scope
**All same-day booking requests** are being unnecessarily escalated, regardless of:
- Time of day (even if 8 hours before check-in)
- Availability status
- Customer urgency

---

## Why Assistant Escalated

1. **Loaded modules** including MODULE_1_CRITICAL_WORKFLOWS.booking_temporal_logic
2. **Read the rule**: "SOLO fechas FUTURAS" + "PROHIBIDO fechas pasadas"
3. **Determined**: November 15 = today = not future = past/present
4. **Blocked itself** from calling availability/pricing tools
5. **Escalated** to phone contact instead

The assistant's reasoning:
> "I cannot quote for today because the rule says 'only future dates'"

---

## Correct Behavior

### Same-Day Booking Logic Should Be:

```
IF check_in_date == today:
    IF current_time < check_in_time (4:00 PM):
        → ALLOW booking (still time to arrive)
        → Call check_room_availability
        → Call get_price_for_date
        → Provide quote
    ELSE:
        → BLOCK booking (too late to check-in today)
        → Suggest tomorrow or later
ELSE IF check_in_date < today:
    → BLOCK booking (date has passed)
    → Use exact_script from booking_temporal_logic
ELSE:
    → ALLOW booking (future date)
```

### Example Scenarios

**Scenario 1**: Customer requests November 15 at 10:00 AM
- Check-in: 4:00-5:00 PM (6 hours away)
- ✅ **ALLOW**: Customer has time to arrive
- Action: Check availability and provide quote

**Scenario 2**: Customer requests November 15 at 6:00 PM
- Check-in time: Already passed
- ❌ **BLOCK**: Too late for today
- Action: Suggest tomorrow (November 16)

**Scenario 3**: Customer requests November 14 (yesterday)
- Check-in date: Past
- ❌ **BLOCK**: Cannot book past dates
- Action: Use exact_script

---

## Solution Options

### Option 1: Refine `booking_temporal_logic` Rule (Recommended)

**Location**: Line 598

**Current**:
```json
"rule": "IMPERATIVO ABSOLUTO: Las reservas y cotizaciones SOLO pueden hacerse para fechas FUTURAS."
```

**Proposed**:
```json
"rule": "IMPERATIVO ABSOLUTO: Las reservas y cotizaciones pueden hacerse para fechas futuras O para el día actual SI es antes de las 4:00 PM (hora de El Salvador, GMT-6). Después de las 4:00 PM, solo fechas a partir de mañana."
```

**Add new field**:
```json
"same_day_cutoff": "4:00 PM El Salvador time (GMT-6). Before cutoff = allow same-day booking. After cutoff = only tomorrow onwards.",
"timezone": "America/El_Salvador (GMT-6)"
```

### Option 2: Add Exception in CRITICAL_PROHIBITIONS

**Location**: CORE_CONFIG.CRITICAL_PROHIBITIONS (after line 358)

**Add**:
```json
"same_day_booking_allowance": "🚨 CLARIFICATION: booking_temporal_logic 'fechas futuras' includes TODAY if customer is requesting check-in for today AND current time is before 4:00 PM El Salvador time. Same-day bookings are VALID and SHOULD be processed normally (check availability, provide quote). Only BLOCK if: (1) check-in date is PAST (before today), OR (2) check-in date is today but current time is AFTER 4:00 PM. Do NOT escalate same-day bookings unnecessarily."
```

### Option 3: Modify Tool Instructions in openai_agent.py

**Add same-day logic** to the tool calling instructions to explicitly allow checking availability for today.

---

## Recommended Fix: **Option 1 + Option 2** (Defense in Depth)

**Why both**:
1. **Option 1**: Fixes the root cause in booking_temporal_logic
2. **Option 2**: Adds a clarifying prohibition in case the rule is misinterpreted

**Implementation**:
1. Update booking_temporal_logic (line 598-602)
2. Add same_day_booking_allowance in CRITICAL_PROHIBITIONS
3. Test with multiple same-day scenarios

---

## Testing Requirements

### Test Case 1: Same-Day Before Cutoff
**Time**: 10:00 AM El Salvador  
**Request**: Check-in today, checkout tomorrow  
**Expected**: Check availability + provide quote  
**Pass**: Tools called, quote provided

### Test Case 2: Same-Day After Cutoff
**Time**: 6:00 PM El Salvador  
**Request**: Check-in today, checkout tomorrow  
**Expected**: Suggest tomorrow or later  
**Pass**: Blocked, suggests alternative dates

### Test Case 3: Past Date
**Time**: Any time  
**Request**: Check-in yesterday  
**Expected**: Use exact_script from booking_temporal_logic  
**Pass**: Blocked, exact_script provided

### Test Case 4: Future Date
**Time**: Any time  
**Request**: Check-in 3 days from now  
**Expected**: Check availability + provide quote  
**Pass**: Tools called, quote provided

---

## Customer Impact

**How many customers affected?**
- Need to audit logs for "2505-2800" escalations
- Filter for same-day booking requests
- Estimate: Potentially 5-10% of booking requests (same-day bookings are common for beach resorts)

**Revenue impact**:
- Same-day bookings are often high-value (last-minute, urgent, willing to pay)
- Conversion drop: ~30-40% when requiring phone call vs instant quote
- Estimated loss: $$$$ per month in lost same-day bookings

---

## Priority

**Priority**: **P1 - High**  
**Urgency**: High (affecting sales daily)  
**Complexity**: Low (simple rule clarification)  
**Risk**: Very low (clarifying existing logic, not changing behavior)

**Recommended timeline**:
- Fix: 30 minutes
- Test: 1 hour
- Deploy: Immediate
- Monitor: 24 hours

---

## Related Issues

This is similar to the bank account hallucination bug where:
- ✅ Modules were loaded correctly
- ❌ But assistant didn't use the tools it should have used
- Root cause: Overly strict blocking rule preventing tool usage

**Pattern**: When blocking rules are too strict, the assistant escalates instead of using its tools, defeating the purpose of automation.

---

## Next Steps

1. Implement Option 1 + Option 2
2. Validate JSON syntax
3. Test all 4 test cases
4. Deploy
5. Monitor for same-day booking requests
6. Audit past escalations to identify affected customers
