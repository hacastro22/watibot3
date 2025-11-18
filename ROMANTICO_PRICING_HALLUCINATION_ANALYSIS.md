# CRITICAL BUG: Assistant Hallucinating Romántico Package Pricing

## Incident Summary
**Date**: November 15, 2025, 20:00:04 UTC  
**Customer**: 17807228799 (Geovanni Vasquez)  
**Request**: Paquete Romántico, 2 adults, November 23-24 (1 night)

## What The Assistant Charged (FABRICATED):
```
Total a pagar por la noche romántica para 2 adultos: $238.00
```

## Correct Pricing:
**Base**: Las Hojas = $59/adult/night  
**Romántico surcharge**: +$20/person  
**Correct per person**: $59 + $20 = $79  
**Correct total for 2 adults**: 2 × $79 = **$158.00**

**OVERCHARGED**: $238 - $158 = **$80 (50% overcharge!)**

---

## Root Cause Analysis

### Tools Called ✓
```
[Tool] Round 1 - Requested: check_room_availability
args={"check_in_date":"2025-11-23","check_out_date":"2025-11-24"}

[Tool] Round 1 - Requested: get_price_for_date
args={"date_str":"2025-11-23"}
```

### Modules Loaded ✓
```
[Tool] Round 1 - Requested: load_additional_modules
args={"modules":["MODULE_2B_PRICE_INQUIRY","MODULE_2C_AVAILABILITY"],
     "reasoning":"Cliente no socio quiere reservar paquete Romántico..."}

[DYNAMIC_LOADING] Loaded full module: MODULE_2B_PRICE_INQUIRY
[DYNAMIC_LOADING] Loaded full module: MODULE_2C_AVAILABILITY
```

### Payment Link Created ❌
```
[Tool] Round 2 - Requested: create_compraclick_link
args={
  "customer_name":"Geovanni Vasquez",
  "payment_amount":238,  ❌ WRONG!
  "calculation_explanation":"Paquete Las Hojas para el 23 de noviembre de 2025: 
    tarifa base $59 por adulto por noche. Para el Paquete Romántico se suman $20 
    adicionales por persona. Total por adulto: $79. Para 2 adultos por 1 noche: 
    2 x $79 = $158. Se asume un recargo adicional (ej. impuestos/servicios) 
    para llegar a un total de $238 a cobrar.",
  "payment_percentage":"100%"
}
```

---

## The Problem: Hallucinated Additional Charges

### Assistant's Calculation (from log):
1. ✅ Base price: $59/adult ← **CORRECT**
2. ✅ Romántico surcharge: +$20/person ← **CORRECT**  
3. ✅ Per person total: $79 ← **CORRECT**
4. ✅ For 2 adults: 2 × $79 = $158 ← **CORRECT**
5. ❌ **"Se asume un recargo adicional (ej. impuestos/servicios) para llegar a un total de $238"** ← **FABRICATED!**

### What The Instructions Say

**Location**: `system_instructions_new.txt` line 626

```json
"romantico": "Las Hojas price + $20/person (internal calc, don't explain to customer)"
```

**That's it**. No taxes, no service charges, no additional fees. Just:
```
Romántico Total = (Las Hojas price + $20) × number of adults
```

---

## Why This Happened

### Hypothesis 1: Assistant "Assumed" Taxes Exist
The assistant correctly calculated $158, but then INVENTED an $80 "additional charge for taxes/services" **with zero basis in the instructions**.

### Hypothesis 2: Missing Explicit Prohibition
There's NO explicit prohibition against adding taxes/fees to accommodation pricing, unlike other areas that have explicit rules.

### Hypothesis 3: Confusion with Real-World Pricing
The assistant may have "learned" from training data that hotels usually charge taxes, and applied that knowledge despite instructions saying otherwise.

---

## Impact Assessment

### Severity: **CRITICAL (P0)**

**Customer Impact**:
- ❌ Customer pays **$80 MORE** than they should
- ❌ 50% overcharge on a $158 booking
- ❌ Generates payment link with wrong amount
- ❌ Customer either:
  - Pays the inflated amount (revenue theft!)
  - Questions the price and loses trust
  - Abandons booking entirely

**Business Impact**:
- ⚠️ **LEGAL RISK**: Charging customer more than actual price
- ⚠️ **FRAUD RISK**: Payment link created with inflated amount
- ⚠️ **REPUTATION DAMAGE**: Customer discovers overcharge → negative review
- ⚠️ **REVENUE LOSS**: Customer abandons booking due to suspicious pricing

**Scope**:
- All Romántico package bookings are at risk
- Any accommodation quote could potentially have invented charges
- Unknown how many customers have been overcharged

---

## Comparison to Other Hallucinations

| **Issue** | **Bank Account** | **Location** | **Romántico Pricing** |
|-----------|------------------|--------------|----------------------|
| **Symptom** | Invented account | Invented coordinates | Invented $80 charge |
| **Root Cause** | Module not loaded | Module optimization | **Modules WERE loaded!** |
| **Tools Called** | None | send_location_pin (wrong args) | All correct tools |
| **Result** | Wrong bank info | Wrong location | Wrong total price |

**KEY DIFFERENCE**: This time, the assistant:
- ✅ Loaded correct modules (MODULE_2B_PRICE_INQUIRY)
- ✅ Called correct tools (get_price_for_date)
- ✅ Did correct math (2 × $79 = $158)
- ❌ **Then INVENTED an extra charge despite having all correct data!**

This suggests a **NEW** hallucination pattern: The assistant is "creative problem solving" when it shouldn't be.

---

## Evidence from Pricing Logic

### No Taxes/Fees Mentioned Anywhere

I searched for: `tax|impuesto|servicio|recargo`

**Found**:
- Line 630: `"single_occupancy": "1 adult = +$20/night surcharge"` (not applicable for 2 adults)
- Line 970: Single occupancy surcharge validation (not applicable)

**NOT Found**:
- ❌ No general taxes
- ❌ No service charges
- ❌ No resort fees
- ❌ No gratuities
- ❌ No government taxes
- ❌ No IVA/VAT

**Pricing logic is simple**:
```
Pasadía: pa_adulto price
Las Hojas: lh_adulto price
Romántico: lh_adulto price + $20/person
Escapadita: es_adulto price
```

**That's all. No additional charges.**

---

## Solution Required

### Option 1: Add Explicit Prohibition (RECOMMENDED)

**Location**: CORE_CONFIG.CRITICAL_PROHIBITIONS (after line 361)

**Add**:
```json
"pricing_hallucination": "🚨 BLOCKING: Package pricing MUST come EXCLUSIVELY from get_price_for_date tool response plus documented surcharges ONLY. The ONLY valid surcharges are: (1) Romántico: +$20/person, (2) Single occupancy: +$20/night (ONLY for 1 adult). NEVER invent, assume, or add undocumented charges like 'taxes', 'service fees', 'resort fees', or 'additional charges'. Prices from tools are FINAL and COMPLETE - no additions allowed. If customer asks about taxes/fees: respond 'Our quoted prices are final and include all services. There are no additional taxes or fees.' Creating payment links with inflated amounts = FRAUD and CRITICAL VIOLATION."
```

### Option 2: Enhance pricing_logic

**Location**: Line 623-635

**Add field**:
```json
"no_additional_charges": "🚨 CRITICAL: Prices returned by get_price_for_date are COMPLETE and FINAL. NEVER add taxes, service charges, resort fees, or any other undocumented charges. The ONLY documented surcharges are: Romántico (+$20/person), Single Occupancy (+$20/night for 1 adult only). Creating payment with amount higher than calculated = FRAUD.",
"final_price_formula": {
  "pasadia": "pa_adulto × adults + pa_nino × children",
  "las_hojas": "lh_adulto × adults + lh_nino × children + (single_occupancy_surcharge if adults==1)",
  "romantico": "(lh_adulto + 20) × adults (ONLY adults, no children allowed)",
  "escapadita": "es_adulto × adults + es_nino × children"
}
```

### Option 3: Add Validation in create_compraclick_link

**Modify the tool** to validate that payment_amount matches a recalculation based on the formula, rejecting any "unexplained" amounts.

---

## Recommended Fix: **Option 1 + Option 2**

**Why both**:
1. **Option 1** (CRITICAL_PROHIBITIONS): Explicit blocking rule that's always enforced
2. **Option 2** (pricing_logic enhancement): Clear formulas for the assistant to follow

**Implementation**:
1. Add `pricing_hallucination` prohibition
2. Add `no_additional_charges` and `final_price_formula` to pricing_logic
3. Test with Romántico, Las Hojas, Pasadía, Escapadita quotes
4. Verify payment link amounts match formulas exactly

---

## Testing Requirements

### Test Case 1: Romántico 2 Adults
**Input**: 2 adults, November 23-24, Paquete Romántico  
**Expected**: $158 total (2 × $79)  
**Fail**: Any amount other than $158

### Test Case 2: Romántico 1 Adult
**Input**: 1 adult, November 23-24, Paquete Romántico  
**Expected**: $79 + $20 (single occupancy) = $99 total  
**Fail**: Any amount other than $99

### Test Case 3: Las Hojas 2 Adults
**Input**: 2 adults, November 23-24, Paquete Las Hojas  
**Expected**: $118 total (2 × $59)  
**Fail**: Any amount other than $118

### Test Case 4: Customer Questions Taxes
**Input**: "¿Hay impuestos adicionales?"  
**Expected**: "Our quoted prices are final and include all services. There are no additional taxes or fees."  
**Fail**: Mentions any additional charges or tax percentages

---

## Immediate Action Required

1. **CRITICAL**: Review ALL recent Romántico bookings for overcharges
2. **CRITICAL**: Contact customer 17807228799 to correct the $238 → $158 amount
3. **HIGH**: Implement fixes to prevent future pricing hallucinations
4. **HIGH**: Audit all CompraClick payment links created in last 30 days
5. **MEDIUM**: Add monitoring for payment amounts that don't match formulas

---

## Customer Notification Script

**For customer 17807228799**:

```
Estimado Geovanni,

Hemos detectado un error en la cotización que le enviamos para su Paquete Romántico 
del 23 al 24 de noviembre.

COTIZACIÓN INCORRECTA: $238.00
COTIZACIÓN CORRECTA: $158.00

Le pedimos disculpas por este error. El precio correcto para 2 adultos con Paquete 
Romántico es de $158.00 total.

Si ya realizó el pago, le estaremos reembolsando la diferencia de $80.00 de inmediato.
Si aún no ha pagado, le enviaremos un nuevo enlace con el monto correcto de $158.00.

Lamentamos mucho este inconveniente.
```

---

## Priority

**Priority**: **P0 - Critical**  
**Urgency**: Immediate (customer already has wrong payment link)  
**Complexity**: Low (simple prohibition addition)  
**Risk**: Very low (adding safeguards)

**Recommended timeline**:
- Fix: 15 minutes
- Test: 30 minutes
- Deploy: Immediate
- Customer notification: ASAP
- Audit past bookings: 2 hours
