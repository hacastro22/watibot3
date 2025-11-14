# Price Revalidation Fix - Expired Promotions

## Problem Analysis

**Customer Case Study:**
- **First conversation (earlier date):** Customer quoted $391.20 with "Beach Black Season" 20% discount (promotion active at time)
- **Second conversation (days later):** Customer returned to proceed with payment, promotion had EXPIRED
- **Critical Error:** Assistant quoted the OLD promotional price ($391.20) despite promotion no longer appearing in instructions
- **Root Cause:** Assistant relied on conversation history prices without revalidating against current pricing/promotions

## Core Issue Identified

The assistant had **temporal blindness** regarding pricing:
1. ✅ Could detect if dates/people changed → revalidate
2. ❌ Could NOT detect if promotions expired between conversations
3. ❌ Did not recognize conversation history prices as "stale data"
4. ❌ Assumed promotional prices from days ago were still valid

## Solution Implemented

### 1. **Enhanced `pricing_logic` (Lines 615-627)**

Added two new critical rules:

**`🚨 CRITICAL_TEMPORAL_REVALIDATION`:**
```
IMPERATIVO ABSOLUTO: Prices and promotions EXPIRE. Even if dates/people unchanged, 
MUST revalidate prices when:
(1) Customer returns after conversation gap
(2) Promotion mentioned in history is NOT in current instructions  
(3) Ready to proceed with payment
ALWAYS call get_price_for_date to get CURRENT pricing. 
Conversation history prices are STALE data.
```

**`🚨 PROMOTION_EXPIRATION_AWARENESS`:**
```
CRITICAL RULE: If conversation history mentions a promotion (discount, special offer, 
Beach Black Season, etc.) but current instructions DO NOT contain that promotion 
→ promotion HAS EXPIRED. NEVER quote expired promotion prices. 
MUST use get_price_for_date to get current non-promotional pricing.
```

### 2. **Enhanced `quote_generation_protocol` (Lines 921-933)**

Added three new validation layers:

**`🚨 CRITICAL_VALIDATION_STEP` (Line 923):**
- Conversation history is ONLY for context, NEVER for prices
- Prices ALWAYS obtained from `get_price_for_date`
- NEVER reuse prices from previous messages

**`validation_step` (Line 924):**
Expanded scenarios requiring price revalidation:
1. Modified dates/people (original rule)
2. **NEW:** Customer returns after conversation pause
3. **NEW:** Customer ready to proceed with payment
4. **NEW:** Promotion in history NOT in current instructions
5. **NEW:** ANY moment presenting a price

**`🚨 RETURNING_CUSTOMER_PROTOCOL` (Line 925):**
```
CRITICAL: Si la conversación tiene mensajes de días anteriores con precios citados, 
esos precios están DESACTUALIZADOS. Las promociones tienen fecha de expiración. 
Si una promoción aparece en conversation history pero NO está en las instrucciones 
actuales → la promoción YA EXPIRÓ.
```

### 3. **New Validation Step in `internal_thinking_process` (Lines 928-933)**

Added **Step 0_PRE** - executed BEFORE all other steps:

```
0_PRE. **🚨 VALIDACIÓN DE PRECIOS TEMPORALES (PASO CRÍTICO OBLIGATORIO):**
   a. ¿Esta conversación tiene mensajes de horas/días anteriores que mencionaron precios o promociones?
   b. SI ES ASÍ: ¿La promoción mencionada anteriormente aparece en mis instrucciones ACTUALES?
   c. SI LA PROMOCIÓN NO APARECE EN INSTRUCCIONES ACTUALES: La promoción HA EXPIRADO.
   d. ACCIÓN OBLIGATORIA: Debo llamar a get_price_for_date AHORA para obtener pricing ACTUAL.
   e. CRITICAL: Conversation history muestra lo que cotizamos ANTES, pero precios cambian con el tiempo.
```

### 4. **Enhanced CORE_CONFIG Prohibitions (Lines 349-350)**

Added two new blocking rules:

**`expired_promotions`:**
```
🚨 BLOCKING: If conversation history mentions a promotion/discount that is NOT in 
current instructions → promotion EXPIRED. NEVER quote old promotional prices. 
MUST call get_price_for_date to get current regular pricing. 
Using expired promotion prices = CRITICAL VIOLATION
```

**`temporal_price_staleness`:**
```
🚨 BLOCKING: Conversation history prices are STALE after time passes. 
When customer returns to proceed with booking (even with same dates/people), 
MUST revalidate prices with get_price_for_date. Prices change, promotions expire. 
NEVER assume old quotes are still valid. ALWAYS verify current pricing before 
presenting total or generating payment link.
```

### 5. **Updated Watchdog Patterns (Lines 829-830)**

Added to `forbidden_vague_phrases`:
- "🚨 CRITICAL: Usar precios de mensajes anteriores sin revalidar con get_price_for_date"
- "🚨 CRITICAL: Mencionar promociones que ya no aparecen en las instrucciones actuales (promotions expire)"

## How This Fixes the Reported Issue

**Scenario: Customer from conversation returns days later**

### Before Fix:
1. Assistant sees dates (Dec 23-25) and people (3 adults, 2 kids) unchanged ✓
2. Assistant sees historical quote: $391.20 with Beach Black Season discount
3. Assistant assumes historical price still valid ❌
4. **ERROR:** Quotes expired promotional price

### After Fix:
1. **Step 0_PRE triggers:** Detects conversation has old messages with pricing
2. **Promotion check:** "Beach Black Season" in history but NOT in current instructions
3. **Conclusion:** Promotion EXPIRED
4. **Mandatory action:** Call `get_price_for_date` to get CURRENT pricing
5. **Result:** Quotes correct non-promotional price ($489.00 instead of $391.20)

## Key Learning Points

### For the Assistant:

1. **Conversation history ≠ Source of truth for pricing**
   - History is context only
   - Prices come from `get_price_for_date` tool

2. **Promotions have TWO dates:**
   - Visit date (Dec 23-25) ← stays same
   - Promotion validity date (expires Nov 7) ← changes with calendar

3. **"Unchanged" has temporal dimension:**
   - Same dates/people but different BOOKING date = prices may change
   - Promotions expire, seasons change, hotel updates rates

4. **If promotion not in instructions → promotion expired**
   - Instructions reflect CURRENT offerings
   - Absence = expiration

## Testing Checklist

To verify fix works, test these scenarios:

- [ ] Customer returns after 24+ hours with same dates/people
- [ ] Conversation mentions promotion not in current instructions  
- [ ] Customer says "ready to pay" after multi-day conversation gap
- [ ] Promotion expires between quote and payment
- [ ] Price increase between initial inquiry and booking decision

Each should trigger `get_price_for_date` call for fresh pricing.

## Files Modified

- `/home/robin/watibot4/app/resources/system_instructions_new.txt`
  - Lines 349-350: New CORE_CONFIG prohibitions
  - Lines 615-627: Enhanced pricing_logic with temporal rules
  - Lines 829-830: Updated watchdog forbidden phrases
  - Lines 921-933: Enhanced quote_generation_protocol validation
  - Step 0_PRE added to internal_thinking_process

## Impact

- **Zero functional changes** to tools or backend
- **Pure instruction enhancement** - teaches assistant temporal awareness
- **Backward compatible** - existing logic still works, just adds validation layer
- **Prevention-focused** - blocks the error BEFORE it reaches customer

---

**Summary:** The assistant now understands that promotions expire over time, even if visit dates and guest counts remain unchanged. It will ALWAYS revalidate pricing when customers return or when promotions mentioned in history are absent from current instructions.
