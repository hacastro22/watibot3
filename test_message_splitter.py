#!/usr/bin/env python3
"""
Test script for message splitting functionality
"""
from app.utils.message_splitter import split_message, needs_splitting

def test_message_splitter():
    """Test the message splitting logic"""
    print("=" * 60)
    print("Testing Message Splitter for ManyChat 2000-char limit")
    print("=" * 60)
    
    # Test 1: Short message (should not split)
    print("\n📝 Test 1: Short message (under 2000 chars)")
    short_msg = "¡Hola! Este es un mensaje corto. 🌴"
    print(f"Length: {len(short_msg)} chars")
    print(f"Needs splitting: {needs_splitting(short_msg)}")
    chunks = split_message(short_msg)
    print(f"Result: {len(chunks)} chunk(s)")
    assert len(chunks) == 1, "Short message should not be split"
    print("✅ PASS")
    
    # Test 2: Long message (should split)
    print("\n📝 Test 2: Long message (over 2000 chars)")
    long_msg = """¡Perfecto, gracias por confirmarlo! 🌴 Para avanzar con su cotización de estadía (entrada viernes y salida domingo) para 2 personas, por favor indíqueme:

1. ¿Qué tipo de alojamiento prefiere? Tenemos:
   - Bungalow Junior (más íntimo y acogedor)
   - Habitación Doble (espacio confortable)
   - Bungalow Matrimonial (romántico y espacioso)
   - Bungalow Familiar (ideal para grupos)

2. ¿Qué paquete de alimentación le gustaría?
   - Paquete Las Hojas: Incluye cena, desayuno, almuerzo, 6 bebidas y 2 postres por persona por noche
   - Paquete Romántico: Todo lo del Paquete Las Hojas + detalles románticos especiales
   - Sin paquete: Solo alojamiento, puede pedir comida por separado

También es importante saber:
- ¿Tiene alguna preferencia especial o requerimiento dietético?
- ¿Celebra alguna ocasión especial?
- ¿Necesita información sobre actividades disponibles en el resort?

Recuerde que nuestras instalaciones incluyen:
- Piscinas con vistas espectaculares
- Restaurante con menú variado
- Áreas verdes y jardines
- Zona de hamacas y descanso
- Acceso a senderos naturales

Las tarifas son competitivas y garantizamos la mejor experiencia. Una vez que me confirme sus preferencias, le preparo una cotización detallada con los precios exactos.

¿Tiene alguna pregunta adicional sobre nuestros servicios o instalaciones? Estoy aquí para ayudarle a planificar su estadía perfecta. ☀️

Además, le comento que contamos con:
- Estacionamiento privado
- WiFi en áreas comunes
- Servicio de limpieza diario
- Atención personalizada
- Seguridad 24/7

Y si necesita servicios adicionales:
- Transporte desde San Salvador (costo adicional)
- Tours a lugares cercanos
- Organización de eventos especiales
- Decoración romántica para ocasiones especiales

Nuestro equipo está comprometido con hacer de su visita una experiencia memorable. Trabajamos con los más altos estándares de calidad y servicio al cliente.

¿Le gustaría que le envíe más información sobre algún aspecto específico? También puedo compartirle fotos de nuestras instalaciones y habitaciones si lo desea. 📸

Esperamos poder recibirle pronto en Las Hojas Resort. 🌴"""
    
    print(f"Length: {len(long_msg)} chars")
    print(f"Needs splitting: {needs_splitting(long_msg)}")
    chunks = split_message(long_msg)
    print(f"Result: {len(chunks)} chunk(s)")
    
    for idx, chunk in enumerate(chunks, 1):
        print(f"\n--- Chunk {idx} ({len(chunk)} chars) ---")
        print(f"First 100 chars: {chunk[:100]}...")
        print(f"Last 100 chars: ...{chunk[-100:]}")
        assert len(chunk) <= 2000, f"Chunk {idx} exceeds 2000 chars!"
    
    print("✅ PASS")
    
    # Test 3: Exactly 2000 chars (should not split)
    print("\n📝 Test 3: Exactly 2000 chars")
    exact_msg = "A" * 2000
    print(f"Length: {len(exact_msg)} chars")
    print(f"Needs splitting: {needs_splitting(exact_msg)}")
    chunks = split_message(exact_msg)
    print(f"Result: {len(chunks)} chunk(s)")
    assert len(chunks) == 1, "2000-char message should not be split"
    print("✅ PASS")
    
    # Test 4: 2001 chars (should split)
    print("\n📝 Test 4: 2001 chars (just over limit)")
    over_msg = "A" * 2001
    print(f"Length: {len(over_msg)} chars")
    print(f"Needs splitting: {needs_splitting(over_msg)}")
    chunks = split_message(over_msg)
    print(f"Result: {len(chunks)} chunk(s)")
    assert len(chunks) == 2, "2001-char message should split into 2 chunks"
    print("✅ PASS")
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED!")
    print("=" * 60)

if __name__ == "__main__":
    test_message_splitter()
