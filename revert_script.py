import json

with open('app/resources/system_instructions_new.txt', 'r', encoding='utf-8') as f:
    data = json.loads(f.read())

data['MODULE_3_SERVICE_FLOWS']['date_change_request_protocol']['internal_thinking_process'] = [
    "// ANOTACIÓN: Antes de responder, DEBO ejecutar este análisis lógico para no hacer promesas falsas.",
    "1. **Identificar Tipo de Reserva (Paso de Triaje Crítico):** Debo determinar si la reserva del cliente es para 'Pasadía' o para 'Estadía' (alojamiento). Si no lo sé, mi primera acción debe ser preguntar.",
    "   - **Pregunta de Aclaración (si es necesario):** '¡Con gusto le ayudamos! Para darle la información correcta, ¿podría confirmarme si su reserva es para un Paquete Pasadía o para una Estadía con alojamiento? ☀️'",
    "2. **Lógica de Decisión por Tipo de Reserva:**",
    "   - **SI es 'Pasadía':** Llamar get_price_for_date para NUEVA fecha. Comparar (adultos×tarifa + niños×tarifa) vs pagado. Usar 'day_pass_change_script' según diferencia.",
    "   - **SI es 'Estadía':** La solicitud requiere validación. Debo proceder con el análisis de temporalidad (pasos 3, 4 y 5).",
    "3. **Obtener Fecha Actual:** Identifico la fecha y hora del sistema.",
    "4. **Identificar Fecha de Check-in Original:** Extraigo de la conversación la fecha de la reserva de Estadía que se desea cambiar.",
    "5. **Cálculo de Antelación:** Calculo las horas entre la 'Fecha Actual' y la 'Fecha de Check-in Original'.",
    "6. **Lógica de Decisión para Estadía:**",
    "   - **SI el cálculo es MENOR O IGUAL a 72 horas:** Debo usar el 'lodging_less_than_72h_script'.",
    "   - **SI el cálculo es MAYOR a 72 horas:** Debo usar el 'lodging_more_than_72h_script'."
]

with open('app/resources/system_instructions_new.txt', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Reverted to original structure with minimal Pasadía addition.")
