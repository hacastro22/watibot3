import json

with open('app/resources/system_instructions_new.txt', 'r', encoding='utf-8') as f:
    data = json.loads(f.read())

data['MODULE_3_SERVICE_FLOWS']['date_change_request_protocol']['internal_thinking_process'] = [
    "1. Identify: Pasadía or Estadía? (Ask if unknown)",
    "2. If Pasadía: Call get_price_for_date for NEW date. Calc new_total = (adult_price*adults) + (child_price*children_6_10). Compare vs paid. Branch script accordingly.",
    "3. If Estadía: Calc hours to original check-in. <=72h -> lodging_less_than_72h_script. >72h -> lodging_more_than_72h_script."
]

data['MODULE_3_SERVICE_FLOWS']['date_change_request_protocol']['day_pass_change_script'] = {
    "script": "¡Por supuesto! El cambio para Pasadía es flexible. 🥥\n[IF_PRICE_HIGHER]: La tarifa de la nueva fecha es mayor. Debe cancelar la diferencia de $[DIFERENCIA] (Tarifa nueva adultos × adultos + Tarifa nueva niños 6-10 × niños - pagado). ¿Desea proceder y recibir el enlace de pago?\n[IF_PRICE_LOWER_OR_EQUAL]: El cambio no tiene costo adicional. Hemos notificado al equipo para actualizar su reserva a la nueva fecha. ¡No se preocupe por nada más! ☀️",
    "follow_up_action": "Call send_email ONLY AFTER customer confirms price diff OR if price <= paid. to_emails: [\"{RESERVATIONS_EMAIL}\"], subject: 'Cambio Fecha Pasadía - #[CODIGO]', body: 'Cambio a [NUEVA_FECHA]'"
}

with open('app/resources/system_instructions_new.txt', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Optimized!")
