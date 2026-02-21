"""
Always-on core system prompt builder for the RAG architecture.

Builds the system prompt that is ALWAYS present in every API call.
Contains the same base modules as the current build_classification_system_prompt()
(MODULE_SYSTEM, DECISION_TREE, MODULE_DEPENDENCIES, CORE_CONFIG) but with
a simplified wrapper since module retrieval is now handled by Python-side RAG
instead of the load_additional_modules tool call.
"""

import json
import logging
from typing import Dict, Any

from .chunker import get_always_on_data

logger = logging.getLogger(__name__)

# Cache the core prompt to avoid re-reading the file on every call
_cached_core_prompt: str = ""
_cached_data: Dict[str, Any] = {}


def get_core_prompt(force_reload: bool = False) -> str:
    """Build the always-on core system prompt.

    This prompt contains MODULE_SYSTEM, DECISION_TREE, MODULE_DEPENDENCIES,
    and CORE_CONFIG — the same base modules as the current architecture.
    The wrapper text is simplified because module loading is now handled
    by Python-side RAG retrieval instead of the LLM calling load_additional_modules.

    Args:
        force_reload: If True, re-read the instructions file even if cached.

    Returns:
        The complete always-on core system prompt string.
    """
    global _cached_core_prompt, _cached_data

    if _cached_core_prompt and not force_reload:
        return _cached_core_prompt

    _cached_data = get_always_on_data()
    _cached_core_prompt = _build_prompt(_cached_data)

    logger.info(
        f"[ALWAYS_ON_CORE] Built core prompt: {len(_cached_core_prompt):,} chars"
    )
    return _cached_core_prompt


def _build_prompt(base_modules: Dict[str, Any]) -> str:
    """Build the prompt string from base module data.

    Preserves the critical rules from the current classification prompt
    (prohibitions, availability gate, tool usage mandates) while removing
    the module-loading instructions that are no longer needed.

    Args:
        base_modules: Dict with MODULE_SYSTEM, DECISION_TREE,
                      MODULE_DEPENDENCIES, CORE_CONFIG.

    Returns:
        Formatted prompt string.
    """
    prompt = f"""🚨🚨🚨 ABSOLUTE BLOCKING RULES - READ THIS FIRST 🚨🚨🚨

YOU ARE ABSOLUTELY FORBIDDEN TO:
- 🚨 CRITICAL: NEVER say 'no tengo acceso al sistema de tarifas', 'no tengo acceso directo', or 'llame a 2505-2800 para cotizar'. YOU HAVE the get_price_for_date tool - USE IT.
- 🚨 CRITICAL: NEVER say 'no tengo habilitado el envío del enlace', 'por este medio no puedo enviar enlace', 'llame al 2505-2800 para pago', or ANY variation that deflects payment/booking to phone calls. YOU HAVE create_compraclick_link to generate payment links - USE IT.
- 🚨 CRITICAL: NEVER tell customers to call ANY phone number for quotes, payments, or bookings. You have ALL the tools needed to complete these tasks.

🚨🚨🚨 LODGING AVAILABILITY GATE - ABSOLUTE REQUIREMENT 🚨🚨🚨
For ANY lodging/hospedaje/estadía quote, you MUST call check_room_availability or check_smart_availability BEFORE:
- Quoting a price for lodging
- Calling create_compraclick_link
- Accepting or processing any payment
- Calling make_booking

SEQUENCE: check availability → confirm rooms exist → THEN quote/payment/booking
NO AVAILABILITY CHECK = NO QUOTE, NO PAYMENT LINK, NO BOOKING. PERIOD.
If unavailable: Use check_smart_availability to offer partial stays or alternatives.

IF YOU DO NOT FOLLOW THIS RULE, YOU WILL CAUSE REVENUE LOSS AND CUSTOMER SERVICE FAILURES.

{json.dumps(base_modules, ensure_ascii=False)}

CONTEXT-AWARE INSTRUCTIONS:

You have been loaded with the base system configuration above (MODULE_SYSTEM, DECISION_TREE, MODULE_DEPENDENCIES, CORE_CONFIG).

Additionally, RELEVANT MODULE CONTENT has been pre-loaded below based on the user's query.
Use the DECISION_TREE to understand the user's intent, MODULE_DEPENDENCIES to know which tools to use,
and the pre-loaded module content to follow the correct protocols.

ALL relevant protocols and rules have been pre-loaded for you. Use the content provided below to respond accurately.

KEY RULES:
1. For pricing/quotes: Use the pricing_logic and quote_generation_protocol from loaded content
2. For availability: ALWAYS check availability before pricing
3. For multi-night quotes: Check EACH date's price separately. Prices vary daily.
4. Follow ALL protocols and safety rules from CORE_CONFIG and loaded content.
"""
    return prompt


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    prompt = get_core_prompt()
    print(f"Core prompt length: {len(prompt):,} chars (~{len(prompt) // 4:,} tokens)")
    print(f"\nFirst 500 chars:\n{prompt[:500]}")
