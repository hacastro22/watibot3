# Bank Account Fix - Quick Test Cases

## CORRECT Bank Details (Reference)
```
✓ Bank: Banco de América Central
✓ Account Type: Cuenta Corriente
✓ Account Number: 200252070
✓ Account Owner: Inversiones Inmobiliarias Italia S.A. de C.V.
✓ Persona: Persona Jurídica
```

## WRONG Bank Details (What Was Hallucinated)
```
❌ Bank: Banco BAC Credomatic
❌ Account Number: 903864340
❌ Account Owner: Las Hojas Resort, S.A. de C.V.
```

---

## Test Cases

### Test 1: Direct Request
**Customer Input**: `Cuál es la cuenta para hacer la transferencia?`

**Expected Behavior**:
1. Intent detected: `bank_transfer_details_request`
2. Module loaded: `MODULE_2B_PRICE_INQUIRY.payment_methods`
3. Response contains ALL 5 correct details

**PASS Criteria**: 
- ✓ Banco de América Central (NOT "BAC Credomatic")
- ✓ 200252070 (NOT "903864340")
- ✓ Inversiones Inmobiliarias Italia S.A. de C.V.

---

### Test 2: Contextual Flow
**Customer Input 1**: `Voy a pagar por transferencia bancaria`  
**Customer Input 2**: `No tengo los datos de la cuenta`

**Expected Behavior**:
1. Message 1: Assistant confirms bank transfer option
2. Message 2: Detects `bank_transfer_details_request`
3. Loads MODULE_2B_PRICE_INQUIRY
4. Provides complete bank details

**PASS Criteria**: Same as Test 1

---

### Test 3: Trigger Phrase Variations
**Test these inputs** (should ALL trigger `bank_transfer_details_request`):

1. `"envíame los datos de la cuenta"`
2. `"necesito los datos bancarios"`
3. `"cuál es el número de cuenta"`
4. `"donde hago el depósito"`
5. `"cuenta del hotel"`
6. `"datos para la transferencia"`
7. `"a qué cuenta deposito"`

**PASS Criteria**: ALL variations provide correct bank details

---

### Test 4: First Message (Cold Start)
**Customer Input**: Customer's FIRST message = `"Datos de cuenta para transferencia"`

**Expected Behavior**:
1. No modules loaded yet (cold start)
2. Assistant detects intent
3. Calls `load_additional_modules(["MODULE_2B_PRICE_INQUIRY.payment_methods"])`
4. Waits for module load
5. THEN provides details

**PASS Criteria**: 
- Logs show module loading BEFORE response
- Correct details provided

---

### Test 5: Module Load Failure (Negative Test)
**Simulate**: Temporarily rename MODULE_2B_PRICE_INQUIRY

**Expected Behavior**:
1. Assistant tries to load module
2. Module not found
3. Assistant BLOCKS response
4. Returns error or escalates to agent

**PASS Criteria**: 
- NEVER provides bank details
- NEVER hallucinates
- Escalates gracefully

---

## Quick Check Commands

### Check logs for bank account requests:
```bash
journalctl -u watibot4 --since "1 hour ago" | grep -i "cuenta\|bank\|transfer"
```

### Check if MODULE_2B_PRICE_INQUIRY loaded:
```bash
journalctl -u watibot4 --since "1 hour ago" | grep "MODULE_2B_PRICE_INQUIRY"
```

### Verify correct details in responses:
```bash
journalctl -u watibot4 --since "1 hour ago" | grep -E "(200252070|903864340|BAC Credomatic|América Central)"
```

---

## Success Metrics

### ✅ 100% Success Criteria:
- Every bank account response contains: "200252070"
- Every response contains: "Banco de América Central"
- Every response contains: "Inversiones Inmobiliarias Italia S.A. de C.V."
- Zero instances of: "903864340" or "BAC Credomatic"

### ⚠️ Warning Signs:
- Module load failures
- Partial bank details (missing fields)
- Long response times (module not caching)

### 🚨 Critical Failures:
- ANY wrong account number
- ANY wrong bank name
- ANY hallucinated details

---

## Rollback Plan

If tests fail:

1. **Immediate**: Revert `system_instructions_new.txt` to previous version
2. **Backup location**: `.git` history or `system_instructions_backup.txt`
3. **Command**: 
```bash
cd /home/robin/watibot4
git checkout HEAD -- app/resources/system_instructions_new.txt
systemctl restart watibot4
```

---

## Production Deployment Checklist

- [ ] All 5 test cases pass in staging
- [ ] Logs show MODULE_2B_PRICE_INQUIRY loading correctly
- [ ] No JSON syntax errors
- [ ] Backup of current system_instructions_new.txt created
- [ ] Rollback plan reviewed and ready
- [ ] Monitoring dashboard open
- [ ] Team notified of deployment
- [ ] Deploy to production
- [ ] Monitor for 24 hours
- [ ] Audit last 7 days of customer interactions for affected cases
