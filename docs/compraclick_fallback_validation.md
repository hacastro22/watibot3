# CompraClick Payment Fallback Validation Documentation

## Overview
This document describes the enhanced CompraClick payment validation system with fallback mechanism for handling missing or incorrect authorization codes.

## Problem Statement
Some customers experience difficulty finding or providing the correct CompraClick authorization code from their payment receipts, leading to failed validations and frustrated customers. The fallback mechanism provides an alternative validation path using credit card details and payment information.

## Solution Architecture

### Primary Validation Path
1. Customer provides CompraClick payment proof (PDF or image)
2. System extracts authorization code using AI analysis
3. System syncs CompraClick payments from portal
4. System validates payment using authorization code
5. If valid, proceed with booking

### Fallback Validation Path (NEW)
Triggered when:
- Customer fails to provide correct authorization code after 3 attempts
- Customer explicitly states they cannot find the authorization code

Process:
1. Assistant requests alternative validation data:
   - Last 4 digits of credit card used
   - Exact amount charged
   - Date of payment
2. System queries database for matching payment using:
   - `documento` column ending with card last 4 digits
   - `importe` matching charged amount (with tolerance)
   - `date` matching payment date
3. If match found and balance sufficient, proceed with booking

## Implementation Details

### New Function: `validate_compraclick_payment_fallback`
**Location**: `/home/robin/watibot3/app/compraclick_tool.py`

**Parameters**:
- `card_last_four`: Last 4 digits of credit card
- `charged_amount`: Amount charged to card
- `payment_date`: Date of payment (flexible format)
- `booking_total`: Total booking amount to validate

**Features**:
- Flexible date parsing (supports "hoy", "ayer", "DD/MM/YYYY", etc.)
- Amount tolerance of ±0.01 for floating point precision
- Detailed customer messaging for various failure scenarios
- Balance validation (must cover ≥50% of booking total)

### OpenAI Assistant Integration
**Tool Definition Added**: `validate_compraclick_payment_fallback`
- Added to tools list in `/home/robin/watibot3/app/openai_agent.py`
- Registered in `available_functions` dictionary

**System Instructions Updated**:
1. **Payment Method Consistency**:
   - Assistant stays focused on originally selected payment method
   - Does not assume payment method change unless explicitly stated
   - Confirms with customer if different payment type detected

2. **Fallback Activation Logic**:
   - Tracks failed authorization code attempts
   - After 3 failures OR explicit "can't find code" statement
   - Informs customer about alternative validation option
   - Requests required fallback data

## Customer Experience Flow

### Scenario 1: Customer Can't Find Authorization Code
```
Customer: "No encuentro el código de autorización"
Assistant: "Entiendo que no puede encontrar el código de autorización. Puedo verificar su pago con información alternativa."
Assistant: "Por favor proporcione:
- Los últimos 4 dígitos de la tarjeta de crédito usada
- El monto exacto cobrado
- La fecha del pago"
Customer: "4567, $118, hoy"
[System validates using fallback]
Assistant: "¡Perfecto! He verificado su pago exitosamente. Procedo con su reserva..."
```

### Scenario 2: After Multiple Failed Attempts
```
Customer: [Sends wrong code 3 times]
Assistant: "Veo que está teniendo dificultades con el código de autorización. Puedo verificar su pago de otra manera..."
[Continues with fallback process]
```

## Error Handling

### Common Error Messages
1. **No matching payment found**:
   "No encontramos un pago de CompraClick con esos datos. Por favor verifique..."

2. **Card digits don't match**:
   "Los últimos 4 dígitos de la tarjeta no coinciden con nuestros registros..."

3. **Amount mismatch**:
   "El monto no coincide. En nuestros registros figura $X..."

4. **Insufficient balance**:
   "El pago fue encontrado pero el saldo restante ($X) no es suficiente..."

## Testing Scenarios

### Test Case 1: Successful Fallback Validation
1. Create CompraClick payment link
2. Customer completes payment
3. Customer can't find authorization code
4. Provide correct card digits, amount, and date
5. Validation should succeed

### Test Case 2: Wrong Card Digits
1. Follow steps 1-3 from Test Case 1
2. Provide incorrect last 4 digits
3. System should return specific error about card mismatch

### Test Case 3: Amount Mismatch
1. Follow steps 1-3 from Test Case 1
2. Provide correct card digits but wrong amount
3. System should indicate the correct amount in records

### Test Case 4: Date Parsing
Test various date formats:
- "hoy" (today)
- "ayer" (yesterday)
- "07/08/2025"
- "7 de agosto"
- "agosto 7"

## Database Schema Reference

CompraClick payments table (`compraclick`):
- `id`: Primary key
- `documento`: Contains card number (last 4 digits)
- `importe`: Payment amount
- `date`: Payment date
- `autorizacion`: Authorization code (primary validation)
- `used`: Amount already used
- `codreser`: Booking references
- `dateused`: Date when used

## Security Considerations

1. **Data Protection**:
   - Only last 4 digits of card requested (never full number)
   - No sensitive card data stored in logs
   - Database queries use parameterized statements

2. **Validation Strictness**:
   - Requires exact match on card digits
   - Amount must match within tolerance
   - Date must match within same day
   - Balance check prevents overbooking

## Monitoring and Logging

Key log points:
- `[COMPRACLICK_FALLBACK]` prefix for all fallback validation logs
- Failed validation attempts logged with reasons
- Successful validations logged with payment ID
- Database errors logged for debugging

## Future Enhancements

1. **Retry Counter Persistence**:
   - Store failed attempt count in database
   - Persist across conversation sessions

2. **Analytics Dashboard**:
   - Track fallback validation usage rate
   - Identify common failure patterns
   - Monitor authorization code recovery rate

3. **Enhanced Date Matching**:
   - Consider timezone differences
   - Handle edge cases around midnight

## Support and Maintenance

For issues or questions:
1. Check logs for `[COMPRACLICK_FALLBACK]` entries
2. Verify database connectivity
3. Ensure CompraClick sync is working properly
4. Review customer conversation history for context

---

*Last Updated: August 7, 2025*
*Version: 1.0*
