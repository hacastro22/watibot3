"""Wati channel adapter scaffolding.

This wraps existing Wati flows behind the ChannelAdapter interface.
Implementation will map Wati webhook payloads into UnifiedMessage and
call existing client utilities. No behavior changes to current pipeline.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.adapters.base_channel_adapter import ChannelAdapter
from app.models.unified_message import UnifiedMessage


class WatiAdapter(ChannelAdapter):
    """Adapter for WhatsApp via Wati."""

    def can_handle(self, raw: Dict[str, Any]) -> bool:
        return "waId" in raw or raw.get("eventType") == "message"

    async def parse_incoming(self, raw: Dict[str, Any]) -> Optional[UnifiedMessage]:
        # Defer detailed mapping to a later step to avoid risking current behavior.
        return None

    async def send_outgoing(
        self,
        user_id: str,
        message: str,
        media: Optional[Dict[str, Any]] = None,
    ) -> bool:
        # Defer to existing wati_client calls in later wiring.
        return False
