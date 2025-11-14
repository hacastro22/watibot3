# Beach Black Season Promotion - System Instructions Updates

## Overview
- **Promotion Name**: Beach Black Season
- **Booking Period**: October 15-31, 2025 (GMT-6)
- **Discount**: 20% off adult accommodation packages
- **Applies To**: Las Hojas, Escapadita, Romántico (adult rates only)
- **Excludes**: Pasadía/Day Pass, children rates

---

## Changes to system_instructions_new.txt

### 1. CORE_CONFIG Section (Insert after line 322, before CRITICAL_PROHIBITIONS)

```json
    "active_promotions": {
      "beach_black_season": {
        "period": "2025-10-15 to 2025-10-31 GMT-6",
        "applies": "Adult accommodation only (Las Hojas, Escapadita, Romántico)",
        "excludes": "Pasadía, children",
        "discount": "20% (auto-applied by get_price_for_date)",
        "first_msg_rule": "Announce proactively in first message if accommodation inquiry"
      }
    },
```

**Token count**: ~45 tokens

---

### 2. DECISION_TREE Section (Insert after line 58, in priority_based_classification)

```json
      "PRIORITY_0_ACTIVE_CAMPAIGNS": {
        "description": "🎉 Active promotional campaigns - check before other intents",
        "beach_black_season_check": {
          "trigger": "First message + current date 2025-10-15 to 2025-10-31 GMT-6 + accommodation inquiry",
          "action": "MODULE_2B_PRICE_INQUIRY.beach_black_season_protocol"
        }
      },
```

**Token count**: ~40 tokens

---

### 3. MODULE_DEPENDENCIES Section (Insert after line 269, in INTENT_TO_MODULE_MAP)

```json
      "ACTIVE_CAMPAIGNS": {
        "beach_black_season": {
          "condition": "Oct 15-31, 2025 GMT-6 + first message + accommodation",
          "load": "MODULE_2B_PRICE_INQUIRY.beach_black_season_protocol",
          "announcement": "Proactive promotion mention"
        }
      },
```

**Token count**: ~35 tokens

---

### 4. MODULE_2B_PRICE_INQUIRY Section (Insert after line 1067, after daypass_sales_protocol)

```json
    "beach_black_season_protocol": {
      "active": "2025-10-15 to 2025-10-31 GMT-6",
      "trigger": "First customer message + accommodation inquiry during active period",
      "announcement": "¡Tenemos una oferta especial! 🌊✨ Durante nuestra **Beach Black Season** (del 15 al 31 de octubre) todos los paquetes de hospedaje para adultos tienen **20% de descuento**. Es el momento perfecto para reservar la fecha que prefieras para cualquier temporada. ¿En qué puedo ayudarle? 🥥",
      "application": "Discount auto-applied by get_price_for_date. Returns field 'beach_black_season_active': true",
      "in_quote": "When beach_black_season_active=true, add note: '✨ Con nuestra promoción Beach Black Season, su tarifa de adulto incluye 20% de descuento ya aplicado'",
      "scope": "Adult accommodation only (lh_adulto, es_adulto). NOT: Pasadía, children, groups ≥30",
      "calculation": "get_price_for_date returns discounted price. Assistant mentions discount applied in quote breakdown"
    },
```

**Token count**: ~150 tokens

---

## Total Token Impact
- **CORE_CONFIG**: ~45 tokens
- **DECISION_TREE**: ~40 tokens  
- **MODULE_DEPENDENCIES**: ~35 tokens
- **MODULE_2B_PRICE_INQUIRY**: ~150 tokens
- **Total**: ~270 tokens

---

## Code Changes Summary

### ✅ COMPLETED: app/database_client.py
- Modified `get_price_for_date()` function
- Applies 20% discount to `lh_adulto` and `es_adulto` during Oct 15-31, 2025 GMT-6
- Returns `beach_black_season_active` flag in result

### ✅ COMPLETED: app/openai_agent.py
- Updated `get_price_for_date` tool description
- Mentions Beach Black Season promotion and `beach_black_season_active` field

### ⏳ PENDING: app/resources/system_instructions_new.txt
- Add the 4 JSON sections above
- Total impact: ~270 tokens

---

## Implementation Instructions

1. **Add to system_instructions_new.txt**: Insert the 4 JSON blocks at the specified locations
2. **Restart the application**: The changes in database_client.py and openai_agent.py are already applied
3. **Test scenarios**:
   - First message during Oct 15-31, 2025 should include promotion announcement
   - Quotes for accommodation should show 20% discount applied
   - Pasadía quotes should NOT show discount
   - Children rates should NOT have discount

---

## Example Customer Flow

**Customer (Oct 20, 2025)**: "Quiero información de hospedaje"

**Assistant**: 
```
¡Tenemos una oferta especial! 🌊✨ Durante nuestra Beach Black Season 
(del 15 al 31 de octubre) todos los paquetes de hospedaje para adultos 
tienen 20% de descuento. Es el momento perfecto para reservar la fecha 
que prefieras para cualquier temporada. ¿En qué puedo ayudarle? 🥥

Con gusto le presento nuestros paquetes...
```

**In Quote**:
```
Paquete Las Hojas para 2 adultos:
2 adultos x $47.20 = $94.40

✨ Con nuestra promoción Beach Black Season, su tarifa de adulto 
incluye 20% de descuento ya aplicado

Total a pagar: $94.40
```

---

## Notes
- Discount calculated automatically - assistant only mentions it's applied
- Promotion announcement only on FIRST message of conversation
- Only for accommodation inquiries (not Pasadía/info questions)
- Children rates remain full price as per promotion rules
