import json

with open('app/resources/system_instructions_new.txt', 'r', encoding='utf-8') as f:
    data = json.loads(f.read())

internal_process = data['MODULE_3_SERVICE_FLOWS']['date_change_request_protocol']['internal_thinking_process']

# Find where to insert the new logic, before the final decision logic
new_steps = [
    "2.5 **Validación de Tarifas para Pasadía:** Si es Pasadía, DEBO llamar a `get_price_for_date` para la NUEVA fecha solicitada.",
    "2.6 **Comparación de Precios:** Calculo el nuevo precio total (adultos × tarifa_nueva + niños × tarifa_nueva) y lo comparo con lo que el cliente ya pagó."
]

# Modify step 2 slightly
for i, step in enumerate(internal_process):
    if step.startswith("2. **Lógica de Decisión por Tipo de Reserva:**"):
        # We need to insert after step 2 and its sub-bullets
        pass

# Actually let's just rewrite the internal process entirely for clarity
data['MODULE_3_SERVICE_FLOWS']['date_change_request_protocol']['internal_thinking_process'] = [
    "// ANOTACIÓN: Antes de responder, DEBO ejecutar este análisis lógico para no hacer promesas falsas.",
    "1. **Identificar Tipo de Reserva:** Determinar si es 'Pasadía' o 'Estadía' (alojamiento). Si no sé, preguntar.",
    "2. **Lógica de Decisión para Pasadía:**",
    "   - Llamar OBLIGATORIAMENTE a `get_price_for_date` para la NUEVA fecha.",
    "   - Calcular el nuevo costo total (tarifa nueva × personas).",
    "   - Si el nuevo costo es MAYOR al pagado: usar parte [IF_PRICE_HIGHER] del 'day_pass_change_script'.",
    "   - Si el nuevo costo es IGUAL O MENOR: usar parte [IF_PRICE_EQUAL_OR_LOWER] del 'day_pass_change_script'.",
    "3. **Lógica de Decisión para Estadía:**",
    "   - Identificar Fecha de Check-in Original y calcular horas de antelación respecto a Fecha Actual.",
    "   - SI es MENOR O IGUAL a 72 horas: usar 'lodging_less_than_72h_script'.",
    "   - SI es MAYOR a 72 horas: usar 'lodging_more_than_72h_script'."
]

with open('app/resources/system_instructions_new.txt', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Updated internal_thinking_process successfully.")
