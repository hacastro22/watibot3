"""Channel detection for incoming webhooks.

Determines which adapter should handle a payload based on minimal markers.
"""
from typing import Any, Dict


def detect_channel(body: Dict[str, Any]) -> str:
    """Detect channel identifier from webhook body.

    Returns one of: 'wati', 'facebook', 'instagram'.
    Raises ValueError if the channel cannot be determined.
    """
    # Wati detection
    if "waId" in body or body.get("eventType") == "message":
        return "wati"

    # ManyChat detection
    if body.get("platform") == "manychat":
        subscriber = body.get("subscriber", {}) or {}
        ig_id = subscriber.get("ig_id")
        if ig_id:
            return "instagram"
        return "facebook"

    raise ValueError("Unknown channel for payload")
