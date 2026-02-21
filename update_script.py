import json

with open('app/resources/system_instructions_new.txt', 'r', encoding='utf-8') as f:
    data = json.loads(f.read())

data['MODULE_3_SERVICE_FLOWS']['date_change_request_protocol']['day_pass_change_script'] = {
    "script": "¡Por supuesto! Como su reserva es para un Paquete Pasadía, el cambio de fecha es muy flexible. 🥥\n\n[IF_PRICE_HIGHER]: Hemos notado que la tarifa para su nueva fecha es mayor. El cambio de fecha no tiene penalidad, pero se debe cancelar la diferencia de tarifa que es de $[DIFERENCIA] (Tarifa nueva adultos × [CANTIDAD] + Tarifa nueva niños 6-10 × [CANTIDAD] menos lo ya pagado). ¿Desea que procedamos con el cambio y le enviemos el enlace por la diferencia?\n[IF_PRICE_EQUAL_OR_LOWER]: El cambio no tiene ningún costo adicional. Hemos enviado la notificación a nuestro equipo para que actualicen su reserva a la nueva fecha. ¡No tiene que preocuparse por nada más! ☀️",
    "follow_up_action": "1) Llamar get_price_for_date para la NUEVA fecha. 2) Calcular diferencia vs lo pagado. 3) Si hay diferencia, pedir confirmación. 4) Si confirman o no hay diferencia, llamar INMEDIATAMENTE a send_email con to_emails: [\"{RESERVATIONS_EMAIL}\"], subject: 'Cambio de Fecha (Pasadía) - Reserva #[CÓDIGO_DE_RESERVA]', body: 'El cliente de PASADÍA #[CÓDIGO_DE_RESERVA] solicita cambio a [NUEVA_FECHA].'"
}

with open('app/resources/system_instructions_new.txt', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print("Updated system_instructions_new.txt successfully.")
