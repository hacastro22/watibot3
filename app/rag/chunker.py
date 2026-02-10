"""
Chunker module for parsing system_instructions_new.txt into semantic chunks.

Splits MODULE_1 through MODULE_4 into protocol-level chunks with metadata.
Each top-level key within a RAG-able module becomes one chunk containing
the exact original JSON content (not rephrased).

Base modules (MODULE_SYSTEM, DECISION_TREE, MODULE_DEPENDENCIES, CORE_CONFIG)
are NOT chunked — they stay in the always-on core.
"""

import json
import logging
import os
from typing import Dict, List, Any

logger = logging.getLogger(__name__)

# Modules that get RAG'd (chunked and embedded)
RAG_MODULES = [
    "MODULE_1_CRITICAL_WORKFLOWS",
    "MODULE_2A_PACKAGE_CONTENT",
    "MODULE_2B_PRICE_INQUIRY",
    "MODULE_2C_AVAILABILITY",
    "MODULE_2D_SPECIAL_SCENARIOS",
    "MODULE_3_SERVICE_FLOWS",
    "MODULE_4_INFORMATION",
]

# Modules that stay always-on (never chunked)
ALWAYS_ON_MODULES = [
    "MODULE_SYSTEM",
    "DECISION_TREE",
    "MODULE_DEPENDENCIES",
    "CORE_CONFIG",
]

# Default path to system instructions file
DEFAULT_INSTRUCTIONS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "resources",
    "system_instructions_new.txt",
)


def load_system_instructions(path: str = None) -> Dict[str, Any]:
    """Load and parse the system instructions JSON file.

    Args:
        path: Optional path to system_instructions_new.txt.
              Defaults to app/resources/system_instructions_new.txt.

    Returns:
        Parsed JSON as a dictionary.
    """
    path = path or DEFAULT_INSTRUCTIONS_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.loads(f.read())


def chunk_modules(data: Dict[str, Any] = None, instructions_path: str = None) -> List[Dict[str, Any]]:
    """Parse system instructions into semantic chunks from RAG-able modules.

    Each top-level key within a RAG-able module becomes one chunk.
    Chunks contain the exact original JSON content.

    Args:
        data: Pre-loaded system instructions dict. If None, loads from file.
        instructions_path: Path to system instructions file (used if data is None).

    Returns:
        List of chunk dicts with keys:
            - chunk_id: Unique identifier (e.g., "MODULE_2B_PRICE_INQUIRY.pricing_logic")
            - module_name: Parent module name
            - section: Section key within the module
            - content: JSON string of the section content
            - content_for_embedding: Natural language description for embedding
            - char_count: Character count of the content
    """
    if data is None:
        data = load_system_instructions(instructions_path)

    chunks = []

    for module_name in RAG_MODULES:
        module_data = data.get(module_name)
        if not module_data or not isinstance(module_data, dict):
            logger.warning(f"[CHUNKER] Module not found or not a dict: {module_name}")
            continue

        for section_key, section_content in module_data.items():
            chunk_id = f"{module_name}.{section_key}"
            content_json = json.dumps(
                {section_key: section_content}, ensure_ascii=False, indent=2
            )

            # Build a natural language description for embedding quality
            embedding_text = _build_embedding_text(module_name, section_key, section_content)

            chunk = {
                "chunk_id": chunk_id,
                "module_name": module_name,
                "section": section_key,
                "content": content_json,
                "content_for_embedding": embedding_text,
                "char_count": len(content_json),
            }
            chunks.append(chunk)
            logger.debug(f"[CHUNKER] Created chunk: {chunk_id} ({len(content_json):,} chars)")

    logger.info(f"[CHUNKER] Created {len(chunks)} chunks from {len(RAG_MODULES)} modules")
    return chunks


# Representative Spanish customer queries for query augmentation.
# Maps section keys to example queries that customers would ask,
# so the embedding captures conversational Spanish semantics
# in addition to the procedural English content.
_QUERY_AUGMENTATION = {
    # ── Pricing & Quotes ──
    "pricing_logic": "cuánto cuesta precio tarifa cotización hospedaje pasadía noche adultos niños romántico luna de miel cuánto sale por persona valentín febrero",
    "quote_generation_protocol": "cotización precio total cuánto cuesta cuánto sale por noche quiero cotizar me puede dar el precio necesito saber el costo pasadía hospedaje bungalow 5x4 promoción",
    "payment_methods": "cómo pago formas de pago transferencia sinpe tarjeta crédito enlace de pago comprobante depósito banco",
    "daypass_sales_protocol": "pasadía day pass cuánto cuesta el pasadía entrada por el día piscina almuerzo incluido precio pasadía adultos niños",
    "multi_room_booking_protocol": "varias habitaciones necesito dos bungalows grupo grande cuántas habitaciones reservar múltiples cuartos",
    "payment_objection_handling_protocol": "no quiero pagar tanto muy caro descuento rebaja más barato 50 porciento mitad",
    "no_example_pricing_protocol": "cómo funciona el precio explicar tarifa sin fecha todavía no tengo fecha",
    # ── Packages & Content ──
    "package_inquiry": "qué incluye incluye almuerzo desayuno piscina qué trae el paquete qué viene incluido actividades romántico luna de miel decoración valentín",
    "sales_rules": "reglas de venta cómo presentar paquetes incluir inclusiones mencionar",
    "escapadita_secret": "más barato más económico menor precio opción económica escapadita",
    # ── Members ──
    "member_handling": "soy socio miembro membresía club número de socio SOC reserva de socio",
    "membership_sales_protocol": "quiero ser socio unirme al club membresía nueva inscripción",
    # ── Availability & Rooms ──
    "date_validation": "fecha disponible puedo ir el sábado fin de semana disponibilidad para",
    "occupancy_rules": "capacidad cuántas personas caben máximo personas por bungalow habitación familiar junior",
    "accommodation_selection": "tipos de habitación bungalow familiar junior matrimonial habitación doble cuál me recomienda diferencia entre camas",
    "booking_temporal": "cuándo puedo reservar anticipación límite reserva",
    # ── Service Flows ──
    "cancellation_inquiry_protocol": "cancelar mi reserva quiero cancelar anular reservación política de cancelación reembolso",
    "cancellation_no_show": "no llegué no show penalización 72 horas cancelación tardía",
    "date_change_request_protocol": "cambiar fecha mover reserva cambio de fecha reprogramar otra fecha",
    "reservation_document_request_protocol": "comprobante de reserva confirmación documento de reserva necesito el comprobante",
    "special_request_protocol": "solicitud especial pedido extra requerimiento",
    "complaint_resolution_protocol": "queja reclamo insatisfecho mal servicio problema",
    "custom_decoration_request_protocol": "decoración especial personalizada arreglo flores globos cumpleaños aniversario",
    "check_in_out_query": "check-in check-out hora entrada salida a qué hora llego a qué hora entrego",
    # ── Hotel Info ──
    "hotel_information": "dirección ubicación cómo llego teléfono contacto horarios check-in check-out wifi internet restaurante menú comida",
    "parking": "estacionamiento parqueo dónde estaciono costo parking hay parqueo",
    "transportation": "cómo llego transporte bus ruta indicaciones dirección mapa",
    "promotion_rules": "promoción descuento oferta 5x4 promo especial temporada qué promociones tienen este mes",
    "child_pricing": "niños precio niño tarifa infantil edad gratis bebé menor",
    "children_packages": "paquete infantil menú niños comida niños incluye para niños",
    "day_use_room": "habitación por el día pasadía con habitación cuarto por horas day use",
    "baby_food_exception": "comida bebé fórmula biberón papilla traer comida de afuera",
    # ── Special Scenarios (NARROW augmentation to prevent false matches) ──
    "all_inclusive_inquiry_protocol": "todo incluido all inclusive ilimitado sin límite",
    # Holiday chunks: ONLY match explicit holiday/Christmas/NYE queries, NOT generic
    # "temporada" or "promoción" queries which should match promotion_rules instead
    "holiday_activities_protocol": "navidad año nuevo diciembre enero programación eventos fiesta fin de año christmas new year 24 25 31 diciembre",
    "new_year_party_inquiry_protocol": "fiesta fin de año año nuevo 31 diciembre celebración noche vieja party new year",
    "special_date_notification_protocol": "31 diciembre año nuevo evento especial fin de año noche vieja",
    # ── Invitational / Events ──
    "invitational_event": "invitación almuerzo gratis certificado estadía cena cortesía me invitaron",
}


def _build_embedding_text(module_name: str, section_key: str, section_content: Any) -> str:
    """Build a natural language text optimized for embedding.

    Combines the module context, section name, query augmentation phrases,
    and a flattened preview of the content to produce a text that captures
    both procedural instructions and conversational Spanish semantics.

    Args:
        module_name: Name of the parent module.
        section_key: Key of the section within the module.
        section_content: The actual content (can be dict, list, or string).

    Returns:
        Natural language string for embedding.
    """
    # Module-level context
    module_descriptions = {
        "MODULE_1_CRITICAL_WORKFLOWS": "Critical blocking workflows and protocols",
        "MODULE_2A_PACKAGE_CONTENT": "Package content details and what is included",
        "MODULE_2B_PRICE_INQUIRY": "Pricing, quotes, payment methods and booking protocols",
        "MODULE_2C_AVAILABILITY": "Room availability, occupancy rules and date validation",
        "MODULE_2D_SPECIAL_SCENARIOS": "Special scenarios: membership, all-inclusive, events, holidays",
        "MODULE_3_SERVICE_FLOWS": "Existing reservation support, changes, cancellations, complaints",
        "MODULE_4_INFORMATION": "Hotel information, facilities, policies, transportation, parking",
    }

    module_desc = module_descriptions.get(module_name, module_name)
    section_readable = section_key.replace("_", " ").replace(".", " ")

    # Query augmentation: add representative Spanish customer queries
    query_aug = _QUERY_AUGMENTATION.get(section_key, "")
    if query_aug:
        query_aug = f" Consultas típicas: {query_aug}."

    # Extract key text from content for richer embeddings
    # Use 2000 chars to capture enough semantic content for accurate retrieval
    # (text-embedding-3-large supports up to ~32K chars / 8191 tokens per input)
    content_preview = _flatten_content_preview(section_content, max_chars=2000)

    return f"{module_desc}: {section_readable}.{query_aug} {content_preview}"


def _flatten_content_preview(content: Any, max_chars: int = 500) -> str:
    """Flatten nested content into a readable text preview for embedding.

    Extracts string values from nested dicts/lists to produce a meaningful
    text representation of the content.

    Args:
        content: The section content (dict, list, or string).
        max_chars: Maximum characters to include in the preview.

    Returns:
        Flattened text preview.
    """
    if isinstance(content, str):
        return content[:max_chars]

    parts = []
    _extract_strings(content, parts, max_chars)
    result = " ".join(parts)
    return result[:max_chars]


def _extract_strings(obj: Any, parts: List[str], max_chars: int) -> None:
    """Recursively extract string values from nested structures.

    Args:
        obj: The object to extract strings from.
        parts: List to append extracted strings to.
        max_chars: Stop extracting after this many total characters.
    """
    current_len = sum(len(p) for p in parts)
    if current_len >= max_chars:
        return

    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str):
                # Include ALL keys (including 🚨 prefixed ones) — they contain
                # critical semantic content like Spanish phrases and specific rules
                clean_key = key.lstrip("🚨 ").replace("_", " ")
                parts.append(clean_key)
            _extract_strings(value, parts, max_chars)
    elif isinstance(obj, list):
        for item in obj:
            _extract_strings(item, parts, max_chars)


def get_always_on_data(data: Dict[str, Any] = None, instructions_path: str = None) -> Dict[str, Any]:
    """Extract the always-on base modules from system instructions.

    These modules are never RAG'd and are always included in the system prompt.

    Args:
        data: Pre-loaded system instructions dict. If None, loads from file.
        instructions_path: Path to system instructions file (used if data is None).

    Returns:
        Dict with the always-on modules.
    """
    if data is None:
        data = load_system_instructions(instructions_path)

    return {key: data[key] for key in ALWAYS_ON_MODULES if key in data}


if __name__ == "__main__":
    # Standalone test: show chunk summary
    logging.basicConfig(level=logging.INFO)
    chunks = chunk_modules()
    print(f"\nTotal chunks: {len(chunks)}")
    print(f"Total content chars: {sum(c['char_count'] for c in chunks):,}")
    print(f"\nChunks by module:")
    from collections import Counter
    module_counts = Counter(c["module_name"] for c in chunks)
    for module, count in sorted(module_counts.items()):
        total_chars = sum(c["char_count"] for c in chunks if c["module_name"] == module)
        print(f"  {module}: {count} chunks, {total_chars:,} chars")
    print(f"\nAll chunk IDs:")
    for c in chunks:
        print(f"  {c['chunk_id']} ({c['char_count']:,} chars)")
