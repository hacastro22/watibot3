# CRITICAL BUG: Assistant Hallucinating Bank Account Details

## Incident Summary
**Date**: November 15, 2025, 17:16:17  
**Customer**: 50374696856 (Rocío)  
**Request**: "No tengo los datos de la cuenta, por favor enviarme"

## What The Assistant Said (FABRICATED):
```
Banco BAC Credomatic ❌
Cuenta corriente: 903864340 ❌
A nombre de: Las Hojas Resort, S.A. de C.V. ❌
```

## Correct Information (from system_instructions_new.txt line 1952-1958):
```
Banco de América Central ✓
Cuenta corriente: 200252070 ✓
A nombre de: Inversiones Inmobiliarias Italia S.A. de C.V. ✓
```

## Root Cause Analysis

### 1. Broken Intent Mapping (Line 272)
**Location**: `system_instructions_new.txt` line 272

**Current (WRONG)**:
```json
"payment_method_inquiry": {"load": "MODULE_4_INFORMATION.payment_methods"}
```

**Problem**: 
- `MODULE_4_INFORMATION.payment_methods` **DOES NOT EXIST**
- MODULE_4_INFORMATION contains: hotel_basic_information, accommodations, contact_information, policies_basic, facilities_and_amenities, general_policies
- **NO payment_methods section in MODULE_4_INFORMATION**

**Correct Location**:
- `payment_methods` is in `MODULE_2B_PRICE_INQUIRY` (line 1938-1979)
- `bank_deposit_info` is nested inside `MODULE_2B_PRICE_INQUIRY.payment_methods` (line 1952-1958)

### 2. Missing Specific Intent
**Problem**: No dedicated intent for "customer requests bank account details"

The DECISION_TREE has:
- `payment_proof_received` - for when customer sends proof
- `payment_method_inquiry` - general payment methods (broken mapping)
- `hesitating_on_payment` - for objections

But **NO specific intent** for:
- "envíame los datos de la cuenta"
- "cuál es la cuenta para transferir"
- "necesito los datos bancarios"

### 3. Weak Module Loading Enforcement

**Base Prompt Rule (openai_agent.py line 126-146)**:
```
🚨🚨🚨 ABSOLUTE BLOCKING RULE - READ THIS FIRST 🚨🚨🚨
BEFORE YOU DO ANYTHING ELSE, YOU MUST:
1. Look at the user query
2. Determine which modules it needs
3. IMMEDIATELY call load_additional_modules()
4. WAIT for the tool response
5. ONLY THEN respond to the user
```

**Why It Failed**:
- Rule says "call load_additional_modules" but doesn't specify WHAT to do if module path is invalid
- When `MODULE_4_INFORMATION.payment_methods` path is broken, the assistant gets **NO ERROR**
- Assistant proceeds without the data and **HALLUCINATES** instead of blocking

### 4. No Hallucination Guard for Bank Details

**CORE_CONFIG.CRITICAL_PROHIBITIONS** (line 335-354) has prohibitions for:
- services (cunas, spa, transport)
- room types (Habitación Matrimonial)
- unit numbers
- expired promotions
- menu prices secrecy

But **NO PROHIBITION** against inventing bank account details!

## Evidence from Logs

```
Nov 15 17:16:10 - Customer asks: "No tengo los datos de la cuenta, por favor enviarme"
Nov 15 17:16:12 - Base modules loaded: 48668 chars
Nov 15 17:16:12 - [MODULE_OPTIMIZATION] Skipping base_modules at message 9
Nov 15 17:16:17 - Assistant responds with FABRICATED bank details
```

**What's Missing in Logs**:
- ❌ NO `load_additional_modules` call
- ❌ NO MODULE_2B_PRICE_INQUIRY loaded
- ❌ NO error about broken module path
- ❌ NO blocking/validation before hallucination

## Impact Assessment

### Severity: **CRITICAL - P0**

**Potential Consequences**:
1. **Customer sends payment to WRONG bank account** (if fabricated account exists)
2. **Customer payment LOST** - money goes to different company
3. **Booking NOT confirmed** - customer thinks they paid but didn't
4. **Massive revenue loss** - customer may cancel or dispute
5. **Legal liability** - providing false banking information
6. **Reputational damage** - customer trust destroyed

### Affected Scope:
- Any customer asking for bank transfer details
- Estimated: Multiple customers per day
- **How long has this been happening?**: Unknown - needs log analysis

## Required Fixes

### Fix 1: Correct Intent Mapping
**File**: `system_instructions_new.txt` line 272

**Change**:
```diff
- "payment_method_inquiry": {"load": "MODULE_4_INFORMATION.payment_methods"}
+ "payment_method_inquiry": {"load": "MODULE_2B_PRICE_INQUIRY.payment_methods"}
```

### Fix 2: Add Specific Bank Transfer Intent
**File**: `system_instructions_new.txt` line 273 (insert new)

**Add**:
```json
"bank_transfer_details_request": {
  "intent": "Customer explicitly requests bank account details for transfer/deposit",
  "trigger": "envíame los datos de la cuenta, cuál es la cuenta, datos bancarios, número de cuenta, donde deposito",
  "action": "Load bank_deposit_info immediately",
  "load": "MODULE_2B_PRICE_INQUIRY.payment_methods.bank_deposit_info",
  "🚨 CRITICAL": "NEVER respond without loading this module. NEVER invent bank details. If module fails to load, escalate to agent immediately."
}
```

### Fix 3: Add Bank Details Prohibition
**File**: `system_instructions_new.txt` line 354 (after menu_prices_secrecy)

**Add**:
```json
"bank_account_hallucination": "🚨 BLOCKING: Bank account details MUST come from MODULE_2B_PRICE_INQUIRY.payment_methods.bank_deposit_info ONLY. NEVER invent, guess, or remember bank details. If module not loaded when customer asks for account → STOP → load MODULE_2B_PRICE_INQUIRY first. Providing wrong bank details = CRITICAL REVENUE LOSS. Account: 200252070, Bank: Banco de América Central, Owner: Inversiones Inmobiliarias Italia S.A. de C.V. - these are the ONLY correct details."
```

### Fix 4: Strengthen Module Loading Validation
**File**: `openai_agent.py` line 183 (load_additional_modules function)

**Add validation**:
```python
# Validate module exists before attempting to load
if module_name not in all_modules:
    logger.error(f"[MODULE_ERROR] Module not found: {module_name}")
    return f"ERROR: Module '{module_name}' does not exist. Cannot proceed without required information. Please escalate to human agent."
```

### Fix 5: Add DEPENDENCY_CHAINS Entry
**File**: `system_instructions_new.txt` line 315 (after payment_flow)

**Add**:
```json
"bank_transfer_request": {
  "auto_load": ["MODULE_2B_PRICE_INQUIRY.payment_methods.bank_deposit_info"],
  "critical_data": ["bank_name", "account_number", "account_owner"],
  "🚨 VALIDATION": "Before providing bank details, verify all 3 fields loaded. Missing any = BLOCK response + escalate"
}
```

## Testing Requirements

### Test Case 1: Direct Request
**Input**: "Cuál es la cuenta para hacer la transferencia?"  
**Expected**: Loads MODULE_2B_PRICE_INQUIRY, provides correct bank details  
**Failure Condition**: Invents details, provides wrong account, or omits any field

### Test Case 2: Context-Based Request
**Input**: "Voy a pagar por transferencia" → "No tengo los datos de la cuenta"  
**Expected**: Recognizes bank_transfer_details_request intent, loads module, provides details  
**Failure Condition**: Asks for more info without loading module

### Test Case 3: Module Load Failure
**Simulate**: Break module path temporarily  
**Expected**: Assistant detects failure, stops, requests escalation  
**Failure Condition**: Assistant proceeds and hallucinates

### Test Case 4: Multiple Conversations
**Input**: Test with 10+ different customers over 2 days  
**Expected**: 100% provide correct details (200252070, Banco de América Central)  
**Failure Condition**: Any variation in details provided

## Deployment Plan

1. **Immediate**: Apply Fixes 1, 2, 3 to system_instructions_new.txt
2. **Within 1 hour**: Apply Fix 4 to openai_agent.py
3. **Test**: Run all 4 test cases in staging
4. **Deploy**: Push to production
5. **Monitor**: Watch logs for 24 hours for any bank transfer requests
6. **Audit**: Search last 7 days of logs for customers who may have received wrong details

## Prevention Measures

1. **Add CI/CD Validation**: Script to validate all module paths in DECISION_TREE exist
2. **Add Hallucination Detection**: Post-response validator to catch fabricated bank details
3. **Add Intent Coverage Metrics**: Track which customer requests don't match any intent
4. **Quarterly Audit**: Review CRITICAL_PROHIBITIONS for new hallucination patterns

## Responsible Parties

**Incident Discovery**: User (Robin)  
**Root Cause Analysis**: Cascade AI  
**Fix Implementation**: Required  
**Testing**: Required  
**Approval**: Required before deployment
